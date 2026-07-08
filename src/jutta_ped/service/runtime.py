from __future__ import annotations

import csv
import json
import threading
import time
import uuid
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import cv2

from modulo.pediatria.detector_mvp import PediatricDetection, PediatricsDetectorMvpRunner
from src.jutta_ped.service.pediatria_service import (
    PediatriaService,
    PediatriaServiceConfig,
    cpu_economical_overrides,
)
from src.jutta_ped.service.telemetry import SessionTelemetry

ROOT = Path(__file__).resolve().parents[3]
DEFAULT_MODEL_PATH = ROOT / "src" / "models" / "pediatria_child_detector_v6_jutta_openvino_model"
if not DEFAULT_MODEL_PATH.exists():
    DEFAULT_MODEL_PATH = ROOT / "src" / "models" / "pediatria_child_detector_v6_jutta.pt"
DEFAULT_PERSON_MODEL_PATH = ROOT / "src" / "models" / "yolo11n_openvino_model"
if not DEFAULT_PERSON_MODEL_PATH.exists():
    DEFAULT_PERSON_MODEL_PATH = ROOT / "src" / "models" / "yolo26n_openvino_model"
DEFAULT_REPORT_DIR = ROOT / "pediatria_results" / "service_api"


def safe_name(value: str) -> str:
    allowed: list[str] = []
    for char in str(value or "camera"):
        if char.isalnum() or char in {"-", "_"}:
            allowed.append(char)
        elif char in {" ", ".", ":", "/"}:
            allowed.append("_")
    name = "".join(allowed).strip("_")
    return name[:80] or "camera"


def redact_source_value(source: str | int) -> str | int:
    if not isinstance(source, str):
        return source
    parsed = urlsplit(source)
    if not parsed.username and not parsed.password:
        return source
    host = parsed.hostname or "camera"
    netloc = host
    if parsed.port:
        netloc = f"{host}:{parsed.port}"
    return urlunsplit((parsed.scheme, netloc, parsed.path, parsed.query, parsed.fragment))


def source_display_name(source: str | int) -> str:
    if isinstance(source, int):
        return f"camera_usb_{source}"
    value = str(source or "pediatria_local")
    parsed = urlsplit(value)
    if parsed.scheme in {"rtsp", "http", "https"}:
        return "camera_remota"
    name = Path(value).stem if value else "pediatria_local"
    return safe_name(name or "pediatria_local")


def resolve_device(force_cpu: bool, requested: str = "auto") -> tuple[str, str]:
    if force_cpu:
        return "cpu", "force_cpu"
    value = str(requested or "auto").strip().lower()
    if value not in {"", "auto"}:
        return requested, "manual"
    try:
        import torch
        if torch.cuda.is_available() and torch.cuda.device_count() > 0:
            return "cuda:0", "cuda_available"
        return "cpu", f"torch_sem_cuda_count_{torch.cuda.device_count()}"
    except Exception as exc:
        return "cpu", f"torch_indisponivel_{type(exc).__name__}"


def open_capture_source(source: str | int) -> Any:
    return cv2.VideoCapture(source)


def normalize_bbox(frame: Any, bbox_xyxy: tuple[float, float, float, float] | list[float] | None) -> tuple[int, int, int, int] | None:
    if frame is None or bbox_xyxy is None or not hasattr(frame, "shape"):
        return None
    frame_h, frame_w = frame.shape[:2]
    if frame_h <= 0 or frame_w <= 0:
        return None
    try:
        x1, y1, x2, y2 = map(int, bbox_xyxy)
    except (TypeError, ValueError):
        return None
    x1, y1 = max(0, x1), max(0, y1)
    x2, y2 = min(frame_w - 1, x2), min(frame_h - 1, y2)
    if x2 <= x1 or y2 <= y1:
        return None
    return x1, y1, x2, y2


def crop_frame(frame: Any, bbox_xyxy: tuple[float, float, float, float] | list[float] | None) -> Any | None:
    bbox = normalize_bbox(frame, bbox_xyxy)
    if bbox is None:
        return None
    x1, y1, x2, y2 = bbox
    pad_x = max(8, int((x2 - x1) * 0.12))
    pad_y = max(8, int((y2 - y1) * 0.08))
    h, w = frame.shape[:2]
    x1, y1 = max(0, x1 - pad_x), max(0, y1 - pad_y)
    x2, y2 = min(w, x2 + pad_x), min(h, y2 + pad_y)
    if x2 <= x1 or y2 <= y1:
        return None
    crop = frame[y1:y2, x1:x2]
    return crop.copy() if crop.size else None


def draw_evidence_frame(frame: Any, detections: list[PediatricDetection], selected: PediatricDetection | None) -> Any:
    annotated = frame.copy()
    selected_track = selected.track_id if selected is not None else None
    for item in detections:
        bbox = normalize_bbox(annotated, item.bbox_xyxy)
        if bbox is None:
            continue
        x1, y1, x2, y2 = bbox
        if item.track_id == selected_track:
            color, thickness = (0, 80, 255), 4
        elif item.role == "child":
            color, thickness = (0, 165, 255), 3
        elif item.role == "adult":
            color, thickness = (80, 180, 80), 2
        else:
            color, thickness = (180, 180, 180), 2
        cv2.rectangle(annotated, (x1, y1), (x2, y2), color, thickness)
        cv2.putText(annotated, f"{item.role}:{item.confidence:.2f}", (x1, max(18, y1 - 8)), cv2.FONT_HERSHEY_SIMPLEX, 0.55, color, 2)
    return annotated


def serialize_detection(item: PediatricDetection | None) -> dict[str, Any] | None:
    if item is None:
        return None
    return {
        "track_id": item.track_id,
        "role": item.role,
        "confidence": item.confidence,
        "bbox_xyxy": list(item.bbox_xyxy),
    }


def best_detection(detections: list[PediatricDetection], role: str | None = None) -> PediatricDetection | None:
    candidates = [item for item in detections if role is None or item.role == role]
    return max(candidates, key=lambda item: item.confidence, default=None)


def select_alert_child(state: Any, detections: list[PediatricDetection]) -> PediatricDetection | None:
    alert_ids = {item.child_track_id for item in getattr(state, "children", []) if item.state == state.stable_state}
    linked = [item for item in detections if item.track_id in alert_ids and item.role == "child"]
    if linked:
        return max(linked, key=lambda item: item.confidence)
    children = [item for item in detections if item.role == "child"]
    return max(children, key=lambda item: item.confidence, default=None)


def status_final_for(state: Any, detections: list[PediatricDetection], person_detections: list[PediatricDetection]) -> str:
    if len(person_detections) == 0 and not detections:
        return "SEM_PESSOA"
    if state.stable_state == "ACCOMPANIED":
        return "CRIANCA_ACOMPANHADA"
    if state.stable_state in {"CHILD_ALONE", "CHILD_SEPARATED"}:
        return "CRIANCA_DESACOMPANHADA"
    if state.stable_state == "NO_CHILD":
        return "NO_CHILD"
    if state.stable_state == "UNCERTAIN":
        return "UNCERTAIN"
    return "ANALISANDO"


_OVERLAY_LABELS = {"child", "adult", "uncertain"}


def build_overlay_objects(detections: list[PediatricDetection], companionship: Any) -> list[dict[str, Any]]:
    """Traduz as deteccoes resolvidas de um frame para o contrato de overlay
    (plugin/schemas/overlay.schema.json): um item por alvo, com o estado de
    companhia do CompanionshipResult quando o alvo e uma crianca avaliada.
    """
    child_state_by_track = {
        child.child_track_id: child.state for child in getattr(companionship, "children", [])
    }
    objects: list[dict[str, Any]] = []
    for item in detections:
        label = item.role if item.role in _OVERLAY_LABELS else "uncertain"
        state = child_state_by_track.get(item.track_id, "ANALISANDO") if label == "child" else "ANALISANDO"
        objects.append(
            {
                "target_id": int(item.track_id),
                "label": label,
                "bbox_xyxy": [round(float(value), 1) for value in item.bbox_xyxy],
                "confidence": round(float(item.confidence), 4),
                "state": state,
            }
        )
    return objects


def choose_crop_detection(child: PediatricDetection | None, detections: list[PediatricDetection], person_detections: list[PediatricDetection]) -> PediatricDetection | None:
    if child is not None:
        return child
    if detections:
        return max(detections, key=lambda item: (item.role == "child", item.confidence))
    if person_detections:
        return max(person_detections, key=lambda item: item.confidence)
    return None


@dataclass(frozen=True)
class SessionStartConfig:
    """Configuracao operacional de uma sessao/instancia do plugin.

    `source` e o `source_ref`/`stream_ref` do modelo de plugin (ver
    plugin/README.md): hoje ainda e um caminho/URL/indice USB recebido
    diretamente, mas o campo ja e tratado internamente como uma referencia
    opaca de stream, nao como um cadastro de camera proprio do servico. A
    camada HTTP (`src/jutta_ped/api/app.py`) ja aceita `stream_ref` como
    alias preferido de `source` no payload da requisicao.
    """

    source: str | int
    camera_id: str | None = None
    stream_id: str | None = None
    lease_id: str | None = None
    force_cpu: bool = False
    modo_coleta: bool = False
    requested_device: str = "auto"
    report_dir: Path = DEFAULT_REPORT_DIR
    diagnostic_log_interval_frames: int = 60
    cooldown_seconds: float = 120.0


@dataclass
class SessionState:
    session_id: str
    camera_id: str
    source_redacted: str | int
    stream_id: str | None = None
    lease_id: str | None = None
    status: str = "starting"
    status_final: str = "ANALISANDO"
    started_at: str = field(default_factory=lambda: datetime.now().isoformat(timespec="milliseconds"))
    stopped_at: str | None = None
    frames_processed: int = 0
    popup_count: int = 0
    suppressed_count: int = 0
    last_error: str | None = None
    evidence_dir: str = ""
    events_log: str = ""
    summary_path: str = ""
    last_event_at: str | None = None
    last_frame_at: str | None = None


class SimpleAlertLatch:
    def __init__(self, cooldown_seconds: float) -> None:
        self.cooldown_seconds = max(0.0, float(cooldown_seconds))
        self._last_by_key: dict[tuple[str, str, int], float] = {}

    def claim(self, camera_id: str, state: str, track_id: int) -> bool:
        key = (camera_id, state, int(track_id))
        now = time.monotonic()
        last = self._last_by_key.get(key)
        if last is not None and now - last < self.cooldown_seconds:
            return False
        self._last_by_key[key] = now
        return True


class PediatriaHeadlessSession:
    def __init__(self, config: SessionStartConfig) -> None:
        self.config = config
        self.session_id = datetime.now().strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:6]
        self.camera_id = safe_name(config.camera_id or source_display_name(config.source))
        self.evidence_dir = config.report_dir / "evidence" / "sessions" / self.session_id
        self.events_log_path = self.evidence_dir / "events.jsonl"
        self.state = SessionState(
            session_id=self.session_id,
            camera_id=self.camera_id,
            source_redacted=redact_source_value(config.source),
            stream_id=config.stream_id,
            lease_id=config.lease_id,
            evidence_dir=str(self.evidence_dir),
            events_log=str(self.events_log_path),
            summary_path=str(self.evidence_dir / "session_summary.json"),
        )
        # Ultimo overlay/bbox conhecido (contrato de plugin, ver
        # plugin/schemas/overlay.schema.json). So o frame mais recente e
        # mantido em memoria -- sem historico, sem custo de disco extra.
        self.last_overlay_frame: dict[str, Any] | None = None
        self.device, self.device_reason = resolve_device(config.force_cpu, config.requested_device)
        perf_overrides = cpu_economical_overrides(self.device)
        self.service = PediatriaService(
            PediatriaServiceConfig(
                specialist_model_path=DEFAULT_MODEL_PATH,
                person_model_path=DEFAULT_PERSON_MODEL_PATH,
                device=self.device,
                detector_stride_frames=perf_overrides["detector_stride_frames"] or 1,
                specialist_budget_per_frame=perf_overrides["specialist_budget_per_frame"],
            ),
            runner_factory=PediatricsDetectorMvpRunner,
            crop_pipeline_factory=_load_person_crop_pipeline,
            identity_stabilizer_factory=_load_identity_stabilizer,
            weak_child_promoter_factory=_load_weak_child_promoter,
        )
        self.capture: Any | None = None
        self.thread: threading.Thread | None = None
        self.stop_event = threading.Event()
        self.lock = threading.Lock()
        self.latencies: list[float] = []
        self.stable_state_counts: Counter[str] = Counter()
        self.event_counts: Counter[str] = Counter()
        self.status_counts: Counter[str] = Counter()
        self.suppression_counts: Counter[str] = Counter()
        self.evidence_error_counts: Counter[str] = Counter()
        self.person_detector_zero = 0
        self.latch = SimpleAlertLatch(config.cooldown_seconds)
        self.telemetry = SessionTelemetry()

    def start(self) -> None:
        self.evidence_dir.mkdir(parents=True, exist_ok=True)
        self.service.start_session()
        self.capture = open_capture_source(self.config.source)
        if not self.capture.isOpened():
            raise OSError("Nao foi possivel abrir a fonte selecionada.")
        self.state.status = "running"
        self._append_session_log({"event_type": "analysis_started", "source": self.camera_id, "requested_device": self.config.requested_device, "resolved_device": self.device, "device_reason": self.device_reason, "force_cpu": self.config.force_cpu, "modo_coleta": self.config.modo_coleta})
        self.thread = threading.Thread(target=self._run_loop, name=f"pediatria-{self.session_id}", daemon=True)
        self.thread.start()

    def stop(self) -> None:
        self.stop_event.set()
        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=5.0)
        self._stop_capture()
        self._finish("stopped")

    def status(self) -> dict[str, Any]:
        with self.lock:
            return dict(self.state.__dict__)

    def summary(self) -> dict[str, Any]:
        return self._build_summary()

    def _run_loop(self) -> None:
        assert self.capture is not None
        try:
            while not self.stop_event.is_set():
                loop_started = time.perf_counter()
                ok, frame = self.capture.read()
                if not ok or frame is None:
                    break
                self._process_frame(frame)
                self.telemetry.capture_loop_ms.add((time.perf_counter() - loop_started) * 1000.0)
                self.telemetry.sample_resources()
        except Exception as exc:
            with self.lock:
                self.state.last_error = str(exc)
            self._append_session_log({"event_type": "session_error", "source": self.camera_id, "error": str(exc)})
        finally:
            self._stop_capture()
            self._finish("stopped" if self.stop_event.is_set() else "finished")

    def _process_frame(self, frame: Any) -> None:
        with self.lock:
            self.state.frames_processed += 1
            frame_id = self.state.frames_processed
        clean_frame = frame.copy()
        result = self.service.process_frame(clean_frame, frame_id=frame_id)
        self.latencies.append(result.latency_ms)
        self.telemetry.process_frame_ms.add(result.latency_ms)
        state = result.state
        resolved = result.resolved_detections
        person_detections = result.person_detections
        self.stable_state_counts[state.stable_state] += 1
        status_final = status_final_for(state, resolved, person_detections)
        overlay_objects = build_overlay_objects(resolved, state)
        captured_at = datetime.now().isoformat(timespec="milliseconds")
        with self.lock:
            self.state.status_final = status_final
            self.state.last_frame_at = captured_at
            self.last_overlay_frame = {
                "frame_seq": frame_id,
                "captured_at": captured_at,
                "objects": overlay_objects,
            }
        if frame_id % max(1, self.config.diagnostic_log_interval_frames) == 0:
            paths, errors = self._save_event_evidence("frame_diagnostic", clean_frame, state, None, resolved, person_detections, False, None)
            payload = self._event_payload("frame_diagnostic", state, None, resolved, person_detections, paths, errors, False, None)
            self._append_event(payload)
        if state.stable_state in {"CHILD_ALONE", "CHILD_SEPARATED"}:
            child = select_alert_child(state, resolved)
            if child is not None:
                allowed = self.latch.claim(self.camera_id, state.stable_state, child.track_id)
                event_type = "popup_alert" if allowed else "popup_suppressed"
                suppression_reason = None if allowed else "active_episode_or_camera_track_cooldown"
                paths, errors = self._save_event_evidence(event_type, clean_frame, state, child, resolved, person_detections, allowed, suppression_reason)
                payload = self._event_payload(event_type, state, child, resolved, person_detections, paths, errors, allowed, suppression_reason)
                self._append_event(payload)
                with self.lock:
                    if allowed:
                        self.state.popup_count += 1
                    else:
                        self.state.suppressed_count += 1

    def _save_event_evidence(self, event_type: str, frame: Any, state: Any, child: PediatricDetection | None, detections: list[PediatricDetection], person_detections: list[PediatricDetection], popup_emitido: bool, suppression_reason: str | None) -> tuple[dict[str, str], list[str]]:
        timestamp = datetime.now()
        crop_detection = choose_crop_detection(child, detections, person_detections)
        track_label = f"track_{crop_detection.track_id}" if crop_detection is not None else "track_none"
        stem = f"{timestamp.strftime('%Y%m%d_%H%M%S_%f')[:-3]}__{self.camera_id}__{track_label}__{event_type}__{state.stable_state}"
        errors: list[str] = []
        paths = {
            "frame": self.evidence_dir / "frames" / f"{stem}_frame.jpg",
            "annotated": self.evidence_dir / "annotated" / f"{stem}_bbox.jpg",
            "metadata": self.evidence_dir / "metadata" / f"{stem}.json",
        }
        self._write_image(paths["frame"], frame, errors, "frame")
        self._write_image(paths["annotated"], draw_evidence_frame(frame, detections, crop_detection), errors, "annotated")
        if crop_detection is not None:
            crop = crop_frame(frame, crop_detection.bbox_xyxy)
            crop_path = self.evidence_dir / "crops" / f"{stem}_crop.jpg"
            if self._write_image(crop_path, crop, errors, "crop"):
                paths["crop"] = crop_path
        else:
            errors.append("crop:no_detection")
        evidence_paths = {key: str(value) for key, value in paths.items() if key != "metadata"}
        metadata = self._event_payload(event_type, state, child, detections, person_detections, evidence_paths, errors, popup_emitido, suppression_reason)
        metadata["metadata_path"] = str(paths["metadata"])
        paths["metadata"].parent.mkdir(parents=True, exist_ok=True)
        metadata_text = json.dumps(metadata, indent=2, ensure_ascii=False)
        paths["metadata"].write_text(metadata_text, encoding="utf-8")
        self.telemetry.disk_usage.record_text("metadata_bytes", metadata_text)
        evidence_paths["metadata"] = str(paths["metadata"])
        return evidence_paths, errors

    _DISK_USAGE_CATEGORY_BY_LABEL = {
        "frame": "evidence_frames_bytes",
        "annotated": "evidence_annotated_bytes",
        "crop": "evidence_crops_bytes",
    }

    def _write_image(self, path: Path, image: Any, errors: list[str], label: str) -> bool:
        try:
            if image is None or not hasattr(image, "size") or image.size == 0:
                errors.append(f"{label}:empty_image")
                return False
            path.parent.mkdir(parents=True, exist_ok=True)
            if not cv2.imwrite(str(path), image):
                errors.append(f"{label}:imwrite_false")
                return False
            if not path.exists():
                return False
            category = self._DISK_USAGE_CATEGORY_BY_LABEL.get(label)
            if category is not None:
                self.telemetry.disk_usage.record_file(category, path)
            return True
        except Exception as exc:
            errors.append(f"{label}:{type(exc).__name__}:{exc}")
            return False

    def _event_payload(self, event_type: str, state: Any, child: PediatricDetection | None, detections: list[PediatricDetection], person_detections: list[PediatricDetection], evidence_paths: dict[str, str], evidence_errors: list[str], popup_emitido: bool, suppression_reason: str | None) -> dict[str, Any]:
        best_person = best_detection(person_detections)
        best_child = best_detection(detections, "child")
        best_any = best_detection(detections)
        crop_detection = choose_crop_detection(child, detections, person_detections)
        specialist_classification = None
        detector_specialist_profile = None
        crop_pipeline = getattr(self.service, "crop_pipeline", None)
        if crop_pipeline is not None:
            detector_specialist_profile = dict(crop_pipeline.last_frame_meta)
            if crop_detection is not None:
                specialist_classification = crop_pipeline.last_trace.get(crop_detection.track_id)
        return {
            "event_type": event_type,
            "timestamp": datetime.now().isoformat(timespec="milliseconds"),
            "created_at": datetime.now().isoformat(timespec="milliseconds"),
            "session_id": self.session_id,
            "camera_id": self.camera_id,
            "source": self.camera_id,
            "frame_id": self.state.frames_processed,
            "frame_index": self.state.frames_processed,
            "popup_emitido": popup_emitido,
            "suppression_reason": suppression_reason,
            "status_final": status_final_for(state, detections, person_detections),
            "person_detector_count": len(person_detections),
            "person_confidence": best_person.confidence if best_person is not None else None,
            "person_bbox_xyxy": list(best_person.bbox_xyxy) if best_person is not None else None,
            "child_specialist_result": best_any.role if best_any is not None else None,
            "child_confidence": best_child.confidence if best_child is not None else None,
            "child_bbox_xyxy": list(best_child.bbox_xyxy) if best_child is not None else None,
            "crop_bbox_xyxy": list(crop_detection.bbox_xyxy) if crop_detection is not None else None,
            "specialist_classification": specialist_classification,
            "detector_specialist_profile": detector_specialist_profile,
            "requested_device": self.config.requested_device,
            "resolved_device": self.device,
            "device_usado": self.device,
            "device_reason": self.device_reason,
            "force_cpu": self.config.force_cpu,
            "modo_coleta": self.config.modo_coleta,
            "raw_state": state.raw_state,
            "stable_state": state.stable_state,
            "reason": state.reason,
            "selected_child": serialize_detection(child),
            "children": [item.__dict__ for item in getattr(state, "children", [])],
            "detections": [serialize_detection(item) for item in detections],
            "evidence_paths": evidence_paths,
            "evidence_errors": evidence_errors,
        }

    def _append_event(self, payload: dict[str, Any]) -> None:
        with self.lock:
            self.state.last_event_at = payload.get("timestamp") or datetime.now().isoformat(timespec="milliseconds")
        self._record_summary(payload)
        self._append_events_jsonl_line(payload)

    def recent_events(self, limit: int = 50) -> list[dict[str, Any]]:
        """Cauda do events.jsonl da sessao, parseada linha a linha.

        Reaproveita o log ja escrito por `_append_events_jsonl_line` -- nao
        cria um segundo canal de eventos so para o contrato de plugin.
        """
        if not self.events_log_path.exists():
            return []
        lines = self.events_log_path.read_text(encoding="utf-8").splitlines()
        events: list[dict[str, Any]] = []
        for line in lines[-max(limit, 1):]:
            line = line.strip()
            if not line:
                continue
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return events

    def telemetry_snapshot(self) -> dict[str, Any]:
        with self.lock:
            frames_processed = self.state.frames_processed
        crop_pipeline = getattr(self.service, "crop_pipeline", None)
        return {
            "resources": self.telemetry.resources_snapshot(),
            "performance": self.telemetry.performance_snapshot(
                frames_processed=frames_processed,
                extra=crop_pipeline.timing_snapshot() if crop_pipeline is not None else None,
            ),
        }

    def _append_session_log(self, payload: dict[str, Any]) -> None:
        item = {"created_at": datetime.now().isoformat(timespec="milliseconds"), **payload}
        self._append_events_jsonl_line(item)

    def _append_events_jsonl_line(self, payload: dict[str, Any]) -> None:
        self.events_log_path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(payload, ensure_ascii=False) + "\n"
        with self.events_log_path.open("a", encoding="utf-8") as handle:
            handle.write(line)
        self.telemetry.disk_usage.record_text("events_jsonl_bytes", line)

    def _record_summary(self, payload: dict[str, Any]) -> None:
        self.event_counts[str(payload.get("event_type") or "unknown")] += 1
        self.status_counts[str(payload.get("status_final") or "unknown")] += 1
        if payload.get("suppression_reason"):
            self.suppression_counts[str(payload["suppression_reason"])] += 1
        if int(payload.get("person_detector_count") or 0) == 0:
            self.person_detector_zero += 1
        for error in payload.get("evidence_errors") or []:
            self.evidence_error_counts[str(error)] += 1

    def _build_summary(self) -> dict[str, Any]:
        crop_pipeline = getattr(self.service, "crop_pipeline", None)
        return {
            "session_id": self.session_id,
            "camera_id": self.camera_id,
            "source": self.state.source_redacted,
            "status": self.state.status,
            "status_final": self.state.status_final,
            "started_at": self.state.started_at,
            "stopped_at": self.state.stopped_at,
            "evidence_dir": str(self.evidence_dir),
            "events_log": str(self.events_log_path),
            "total_frames_analisados": self.state.frames_processed,
            "total_popups": self.state.popup_count,
            "total_suprimidos": self.state.suppressed_count,
            "total_uncertain": self.status_counts.get("UNCERTAIN", 0),
            "total_no_child": self.status_counts.get("NO_CHILD", 0),
            "total_sem_pessoa": self.status_counts.get("SEM_PESSOA", 0),
            "total_person_detector_zero": self.person_detector_zero,
            "event_type_counts": dict(self.event_counts),
            "status_final_distribution": dict(self.status_counts),
            "suppression_reason_distribution": dict(self.suppression_counts),
            "evidence_error_distribution": dict(self.evidence_error_counts),
            "stable_state_counts": dict(self.stable_state_counts),
            "average_latency_ms": round(sum(self.latencies) / len(self.latencies), 2) if self.latencies else None,
            "specialist_classification_stats": dict(crop_pipeline.stats) if crop_pipeline is not None else None,
            "resources": self.telemetry.resources_snapshot(),
            "performance": self.telemetry.performance_snapshot(
                frames_processed=self.state.frames_processed,
                extra=crop_pipeline.timing_snapshot() if crop_pipeline is not None else None,
            ),
            "disk_usage": self.telemetry.disk_usage_snapshot(),
        }

    def _finish(self, status: str) -> None:
        with self.lock:
            if self.state.status in {"stopped", "finished", "error"}:
                return
            self.state.status = status
            self.state.stopped_at = datetime.now().isoformat(timespec="milliseconds")
        self._append_session_log({"event_type": "analysis_stopped", "source": self.camera_id, "frames_processed": self.state.frames_processed, "popup_count": self.state.popup_count, "stable_state_counts": dict(self.stable_state_counts)})
        self._write_summary_files()

    def _write_summary_files(self) -> None:
        summary = self._build_summary()
        self.evidence_dir.mkdir(parents=True, exist_ok=True)
        summary_text = json.dumps(summary, indent=2, ensure_ascii=False)
        (self.evidence_dir / "session_summary.json").write_text(summary_text, encoding="utf-8")
        self.telemetry.disk_usage.record_text("session_summary_bytes", summary_text)
        csv_path = self.evidence_dir / "session_summary.csv"
        with csv_path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(["metric", "value"])
            for key, value in summary.items():
                if isinstance(value, (dict, list)):
                    value = json.dumps(value, ensure_ascii=False)
                writer.writerow([key, value])
        self.telemetry.disk_usage.record_file("session_summary_bytes", csv_path)
        self.telemetry.write_resource_samples(self.evidence_dir / "resource_samples.jsonl")

    def _stop_capture(self) -> None:
        if self.capture is not None:
            self.capture.release()
            self.capture = None


def _load_person_crop_pipeline(*args: Any, **kwargs: Any) -> Any:
    from modulo.pediatria.crop_pipeline import PersonCropSpecialistPipeline
    return PersonCropSpecialistPipeline(*args, **kwargs)


def _load_identity_stabilizer() -> Any:
    from modulo.pediatria.identity_stabilizer import PediatricIdentityStabilizer
    return PediatricIdentityStabilizer()


def _load_weak_child_promoter() -> Any:
    from modulo.pediatria.weak_child_promoter import WeakChildCandidatePromoter
    return WeakChildCandidatePromoter(min_hits=12)


def operational_status_from_session(status: dict[str, Any]) -> str:
    state = str(status.get("status") or "").lower()
    if state == "error" or status.get("last_error"):
        return "ERROR"
    if state in {"stopped", "finished"}:
        return "STOPPED"
    value = str(status.get("status_final") or "ANALISANDO")
    allowed = {
        "ANALISANDO",
        "CRIANCA_ACOMPANHADA",
        "CRIANCA_DESACOMPANHADA",
        "SEM_PESSOA",
        "UNCERTAIN",
        "NO_CHILD",
    }
    return value if value in allowed else "ANALISANDO"


def camera_status_payload(status: dict[str, Any]) -> dict[str, Any]:
    return {
        "camera_id": status.get("camera_id"),
        "session_id": status.get("session_id"),
        "active": str(status.get("status") or "").lower() == "running",
        "session_status": status.get("status"),
        "operational_status": operational_status_from_session(status),
        "frames_processed": status.get("frames_processed", 0),
        "popup_count": status.get("popup_count", 0),
        "suppressed_count": status.get("suppressed_count", 0),
        "evidence_dir": status.get("evidence_dir"),
        "summary_path": status.get("summary_path"),
        "last_error": status.get("last_error"),
        "started_at": status.get("started_at"),
        "stopped_at": status.get("stopped_at"),
    }


PLUGIN_ID = "ia.pediatria"

# Estados operacionais minimos do contrato de instancia de plugin (ver
# plugin/manifest.json / plugin/README.md). "stopping" e "degraded" estao
# previstos no contrato mas o runtime atual e sincrono o bastante para nao
# os observar hoje (stop() bloqueia ate a thread terminar antes de retornar).
_INSTANCE_STATES = {"starting", "running", "degraded", "stopping", "stopped", "error"}
_SESSION_STATUS_TO_INSTANCE_STATE = {
    "starting": "starting",
    "running": "running",
    "stopped": "stopped",
    "finished": "stopped",
    "error": "error",
}
_ANALYSIS_STATES = {
    "ANALISANDO",
    "SEM_PESSOA",
    "UNCERTAIN",
    "NO_CHILD",
    "CRIANCA_ACOMPANHADA",
    "CRIANCA_DESACOMPANHADA",
    "ERROR",
}


def instance_state_from_session(status: dict[str, Any]) -> str:
    if status.get("last_error"):
        return "error"
    raw = str(status.get("status") or "starting").lower()
    return _SESSION_STATUS_TO_INSTANCE_STATE.get(raw, "running")


def instance_health_from_state(state: str) -> str:
    if state == "error":
        return "unhealthy"
    if state in {"starting", "stopping"}:
        return "degraded"
    return "healthy"


def analysis_state_from_session(status: dict[str, Any]) -> str:
    if status.get("last_error"):
        return "ERROR"
    value = str(status.get("status_final") or "ANALISANDO")
    return value if value in _ANALYSIS_STATES else "ANALISANDO"


def instance_status_payload(status: dict[str, Any]) -> dict[str, Any]:
    """Formato padronizado de status de instancia (criterio 4 desta rodada).

    Recebe o mesmo dict que `PediatriaHeadlessSession.status()` retorna (mais
    a chave opcional "telemetry", injetada por quem chama) e projeta nos
    campos minimos do contrato de plugin.
    """
    state = instance_state_from_session(status)
    return {
        "plugin_id": PLUGIN_ID,
        "instance_id": status.get("session_id"),
        "camera_id": status.get("camera_id"),
        "stream_id": status.get("stream_id"),
        "state": state,
        "health": instance_health_from_state(state),
        "analysis_state": analysis_state_from_session(status),
        "last_event_at": status.get("last_event_at"),
        "last_frame_at": status.get("last_frame_at"),
        "telemetry": status.get("telemetry") or {},
    }


class PediatriaSessionManager:
    def __init__(self) -> None:
        self._sessions: dict[str, PediatriaHeadlessSession] = {}
        self._camera_sessions: dict[str, str] = {}
        self._lock = threading.Lock()

    def start_session(self, config: SessionStartConfig) -> dict[str, Any]:
        session = PediatriaHeadlessSession(config)
        session.start()
        with self._lock:
            self._sessions[session.session_id] = session
            self._camera_sessions[session.camera_id] = session.session_id
        return session.status()

    def stop_session(self, session_id: str) -> dict[str, Any]:
        session = self._get(session_id)
        session.stop()
        with self._lock:
            if self._camera_sessions.get(session.camera_id) == session.session_id:
                self._camera_sessions.pop(session.camera_id, None)
        return session.status()

    def status(self, session_id: str) -> dict[str, Any]:
        return self._get(session_id).status()

    def summary(self, session_id: str) -> dict[str, Any]:
        return self._get(session_id).summary()

    def sessions(self) -> list[dict[str, Any]]:
        with self._lock:
            sessions = list(self._sessions.values())
        return [session.status() for session in sessions]

    def enable_camera(self, config: SessionStartConfig) -> dict[str, Any]:
        camera_id = safe_name(config.camera_id or source_display_name(config.source))
        with self._lock:
            current_session_id = self._camera_sessions.get(camera_id)
            current = self._sessions.get(current_session_id) if current_session_id else None
        if current is not None:
            status = current.status()
            if str(status.get("status") or "").lower() == "running":
                return camera_status_payload(status)
        started = self.start_session(
            SessionStartConfig(
                source=config.source,
                camera_id=camera_id,
                stream_id=config.stream_id,
                lease_id=config.lease_id,
                force_cpu=config.force_cpu,
                modo_coleta=config.modo_coleta,
                requested_device=config.requested_device,
                report_dir=config.report_dir,
                diagnostic_log_interval_frames=config.diagnostic_log_interval_frames,
                cooldown_seconds=config.cooldown_seconds,
            )
        )
        return camera_status_payload(started)

    def disable_camera(self, camera_id: str) -> dict[str, Any]:
        safe_camera_id = safe_name(camera_id)
        with self._lock:
            session_id = self._camera_sessions.get(safe_camera_id)
        if session_id is None:
            return {
                "camera_id": safe_camera_id,
                "session_id": None,
                "active": False,
                "session_status": "stopped",
                "operational_status": "STOPPED",
                "frames_processed": 0,
                "popup_count": 0,
                "suppressed_count": 0,
                "evidence_dir": None,
                "summary_path": None,
                "last_error": None,
                "started_at": None,
                "stopped_at": None,
            }
        status = self.stop_session(session_id)
        return camera_status_payload(status)

    def camera_status(self, camera_id: str) -> dict[str, Any]:
        safe_camera_id = safe_name(camera_id)
        with self._lock:
            session_id = self._camera_sessions.get(safe_camera_id)
            session = self._sessions.get(session_id) if session_id else None
        if session is None:
            return {
                "camera_id": safe_camera_id,
                "session_id": None,
                "active": False,
                "session_status": "stopped",
                "operational_status": "STOPPED",
                "frames_processed": 0,
                "popup_count": 0,
                "suppressed_count": 0,
                "evidence_dir": None,
                "summary_path": None,
                "last_error": None,
                "started_at": None,
                "stopped_at": None,
            }
        return camera_status_payload(session.status())

    def cameras(self) -> list[dict[str, Any]]:
        with self._lock:
            camera_ids = sorted(self._camera_sessions)
        return [self.camera_status(camera_id) for camera_id in camera_ids]

    # -- Contrato de instancia de plugin (instance_id == session_id hoje;
    #    ver plugin/README.md) -- reaproveita as sessoes/cameras acima, so
    #    projeta a saida no formato padronizado do contrato.

    def instance_status(self, instance_id: str) -> dict[str, Any]:
        session = self._get(instance_id)
        status = session.status()
        telemetry_getter = getattr(session, "telemetry_snapshot", None)
        status["telemetry"] = telemetry_getter() if callable(telemetry_getter) else {}
        return instance_status_payload(status)

    def instances(self) -> list[dict[str, Any]]:
        with self._lock:
            session_ids = list(self._sessions.keys())
        return [self.instance_status(session_id) for session_id in session_ids]

    def overlay_latest(self, instance_id: str) -> dict[str, Any]:
        session = self._get(instance_id)
        status = session.status()
        now = datetime.now()
        frame = getattr(session, "last_overlay_frame", None)
        if frame is None:
            return {
                "type": "overlay_bboxes",
                "plugin_id": PLUGIN_ID,
                "instance_id": status.get("session_id"),
                "camera_id": status.get("camera_id"),
                "stream_id": status.get("stream_id"),
                "frame_seq": 0,
                "captured_at": now.isoformat(timespec="milliseconds"),
                "frame_age_ms": None,
                "objects": [],
            }
        frame_age_ms: float | None
        try:
            frame_age_ms = max(0.0, (now - datetime.fromisoformat(frame["captured_at"])).total_seconds() * 1000.0)
        except ValueError:
            frame_age_ms = None
        return {
            "type": "overlay_bboxes",
            "plugin_id": PLUGIN_ID,
            "instance_id": status.get("session_id"),
            "camera_id": status.get("camera_id"),
            "stream_id": status.get("stream_id"),
            "frame_seq": frame["frame_seq"],
            "captured_at": frame["captured_at"],
            "frame_age_ms": frame_age_ms,
            "objects": frame["objects"],
        }

    def events(self, instance_id: str, *, limit: int = 50, event_type: str | None = None) -> list[dict[str, Any]]:
        session = self._get(instance_id)
        recent_events_getter = getattr(session, "recent_events", None)
        if not callable(recent_events_getter):
            return []
        safe_limit = max(1, min(int(limit), 500))
        raw = recent_events_getter(limit=safe_limit * 5 if event_type else safe_limit)
        if event_type:
            raw = [item for item in raw if item.get("event_type") == event_type]
        return raw[-safe_limit:]

    def _get(self, session_id: str) -> PediatriaHeadlessSession:
        with self._lock:
            session = self._sessions.get(session_id)
        if session is None:
            raise KeyError(session_id)
        return session
