from __future__ import annotations

"""Demo Viewer da IA Pediatria: apresentacao/cliente opcional do servico.

Este modulo e o "Demo Viewer", nao o runtime da IA. Ele existe para
demonstrar/operar a IA visualmente (start/stop, popup de alerta, video
anotado) mas NAO e onde a inteligencia roda de verdade -- isso e
responsabilidade do servico (`src/jutta_ped/service/pediatria_service.py`
e `runtime.py`), que continua funcionando sozinho via API/headless sem
nenhuma dependencia deste arquivo.

Fronteira de dependencia do plugin: `ui -> service -> core`, nunca o
contrario. Este arquivo so pode importar a camada de servico atraves de
`src.jutta_ped.ui.service_client` (nunca `src.jutta_ped.service.*`
diretamente) e so usa classes de IA "core" (deteccao, identidade, crop
pipeline) importadas de `modulo.pediatria.*` -- as mesmas que o servico usa,
sem duplicacao.
"""

import argparse
import csv
import json
import os
import sys
import threading
import time
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlsplit, urlunsplit

import cv2
from PyQt6.QtCore import QPoint, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QImage, QPixmap
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGraphicsDropShadowEffect,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

DEFAULT_MODEL_PATH = ROOT / "src" / "models" / "pediatria_child_detector_v6_jutta_openvino_model"
if not DEFAULT_MODEL_PATH.exists():
    DEFAULT_MODEL_PATH = ROOT / "src" / "models" / "pediatria_child_detector_v6_jutta.pt"
if not DEFAULT_MODEL_PATH.exists():
    DEFAULT_MODEL_PATH = ROOT / "src" / "models" / "pediatria_child_detector_v5.pt"
DEFAULT_PERSON_MODEL_PATH = ROOT / "src" / "models" / "yolo11n_openvino_model"
if not DEFAULT_PERSON_MODEL_PATH.exists():
    DEFAULT_PERSON_MODEL_PATH = ROOT / "src" / "models" / "yolo26n_openvino_model"
if not DEFAULT_PERSON_MODEL_PATH.exists():
    DEFAULT_PERSON_MODEL_PATH = ROOT / "src" / "models" / "yolo11n.pt"
if not DEFAULT_PERSON_MODEL_PATH.exists():
    DEFAULT_PERSON_MODEL_PATH = ROOT / "src" / "models" / "yolo26n.pt"
DEFAULT_REPORT_PATH = ROOT / "pediatria_results" / "v6_jutta_openvino_local" / "report.json"

from modulo.pediatria.companionship import CompanionTrack, CompanionshipAnalyzer
from modulo.pediatria.crop_pipeline import PersonCropSpecialistPipeline, TrackClassificationState, crop_frame
from modulo.pediatria.detector_mvp import (
    PediatricDetection,
    PediatricRoleContextAdjuster,
    PediatricsDetectorMvpRunner,
    resolve_role_conflicts,
)
from modulo.pediatria.identity_stabilizer import (
    PediatricIdentityMemory,
    PediatricIdentityMerge,
    PediatricIdentityStabilizer,
)
from modulo.pediatria.mvp_popup import AlertEventLatch, PediatriaAlertPopup
from modulo.pediatria.mvp_voice import MvpVoiceAnnouncer
from modulo.pediatria.weak_child_promoter import (
    WeakChildCandidatePromoter,
    WeakChildMemory,
    WeakChildPromotion,
)
from src.jutta_ped.ui.service_client import (
    PediatriaService,
    PediatriaServiceConfig,
    SessionTelemetry,
    cpu_economical_overrides,
    service_launcher,
)

SOURCE_FILE = "Arquivo de video"
SOURCE_URL = "URL de camera"
SOURCE_USB = "Camera USB"
VIDEO_FILE_FILTER = (
    "Videos (*.mp4 *.avi *.dav *.mov *.mkv *.m4v);;"
    "DVR Intelbras/Dahua (*.dav);;"
    "Todos os arquivos (*)"
)


def resolve_inference_device(requested: str) -> tuple[str, str]:
    """Resolve dispositivo de inferencia sem exigir CUDA no PC de apresentacao."""

    value = str(requested or "auto").strip().lower()
    if value not in {"", "auto"}:
        return requested, "manual"
    try:
        import torch
    except Exception as exc:
        return "cpu", f"torch_indisponivel_{type(exc).__name__}"

    try:
        if torch.cuda.is_available():
            min_gpu_gb = float(os.getenv("PEDIATRIA_MIN_GPU_GB", "3.0"))
            props = torch.cuda.get_device_properties(0)
            total_gb = float(props.total_memory) / (1024.0**3)
            if total_gb >= min_gpu_gb:
                return "cuda:0", f"cuda_disponivel_{total_gb:.1f}gb"
            return "cpu", f"gpu_memoria_baixa_{total_gb:.1f}gb"
        cuda_version = getattr(getattr(torch, "version", None), "cuda", None)
        device_count = torch.cuda.device_count()
        if cuda_version:
            return "cpu", f"cuda_indisponivel_count_{device_count}_torch_cuda_{cuda_version}"
        return "cpu", f"torch_sem_cuda_count_{device_count}"
    except Exception as exc:
        return "cpu", f"cuda_check_falhou_{type(exc).__name__}"


def performance_profile_for_device(device: str, source_fps: float) -> dict[str, int | float | str | None]:
    """Perfil operacional por device: 3 cadencias desacopladas.

    - display_fps: com que frequencia a UI le/renderiza um frame (loop
      unico, sempre roda para o video nao travar).
    - detector_stride_frames: a cada quantos frames ANALISADOS o detector de
      pessoa de fato roda (>1 reaproveita a ultima leitura nos demais,
      dentro de PersonCropSpecialistPipeline).
    - specialist_budget_per_frame: teto de chamadas ao especialista por
      frame analisado (None = sem teto). Em CPU, cenas com muitos tracks
      elegiveis para reclassificar sao priorizadas em vez de gastar em todo
      mundo igualmente.

    O crop-pipeline continua sendo o caminho principal de acuracia em
    qualquer perfil: essas cadencias so controlam a FREQUENCIA das chamadas
    caras (detector/especialista), nunca removem o estagio.
    """
    is_cpu = str(device or "").lower() == "cpu"
    display_fps = 12.0 if is_cpu else min(max(float(source_fps or 30.0), 1.0), 30.0)
    overrides = cpu_economical_overrides(device)
    return {
        "device": device,
        "display_fps": display_fps,
        "detector_stride_frames": overrides["detector_stride_frames"],
        "specialist_budget_per_frame": overrides["specialist_budget_per_frame"],
    }


def resolve_capture_source(mode: str, value: str, usb_index: int) -> str | int:
    """Converte a selecao da interface em uma fonte aceita pelo OpenCV."""

    if mode == SOURCE_USB:
        return usb_index
    source = value.strip()
    if not source:
        raise ValueError("Selecione ou informe uma fonte antes de iniciar.")
    if mode == SOURCE_FILE and not Path(source).is_file():
        raise ValueError(f"Arquivo nao encontrado: {source}")
    if mode == SOURCE_URL and not source.lower().startswith(
        ("rtsp://", "rtsps://", "http://", "https://")
    ):
        raise ValueError("Informe uma URL RTSP, RTSPS, HTTP ou HTTPS valida.")
    return source


def open_capture_source(source: str | int) -> Any:
    """Abre fonte OpenCV; tenta FFmpeg explicitamente para exportacoes DVR .dav."""

    if isinstance(source, int):
        return cv2.VideoCapture(source)
    path = Path(source)
    if path.suffix.lower() == ".dav":
        capture = cv2.VideoCapture(str(path), cv2.CAP_FFMPEG)
        if capture.isOpened():
            return capture
        capture.release()
    return cv2.VideoCapture(source)


def resolve_model_path(value: str, fallback: Path) -> Path:
    text = str(value or "").strip()
    path = Path(text) if text else fallback
    if not path.is_absolute():
        path = ROOT / path
    return path


def source_display_name(source: str | int) -> str:
    if isinstance(source, int):
        return f"camera_usb_{source}"
    if "://" in source:
        return "camera_remota"
    return Path(source).stem


def redact_source_value(source: str | int) -> str:
    if isinstance(source, int):
        return str(source)
    if "://" not in source:
        return source
    try:
        parts = urlsplit(source)
        hostname = parts.hostname or ""
        netloc = hostname
        if parts.port is not None:
            netloc = f"{netloc}:{parts.port}"
        return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))
    except ValueError:
        return "camera_url_redigida"


def detection_area(detection: PediatricDetection) -> float:
    x1, y1, x2, y2 = detection.bbox_xyxy
    return max(0.0, x2 - x1) * max(0.0, y2 - y1)


def role_counts(detections: list[PediatricDetection]) -> dict[str, int]:
    return dict(Counter(item.role for item in detections))


def dedupe_detections_by_track(
    detections: list[PediatricDetection],
) -> list[PediatricDetection]:
    best_by_track: dict[int, PediatricDetection] = {}
    for item in detections:
        previous = best_by_track.get(item.track_id)
        if previous is None or item.confidence > previous.confidence:
            best_by_track[item.track_id] = item
    return list(best_by_track.values())


def select_popup_evidence_detection(
    detections: list[PediatricDetection],
    alert_child_track_ids: set[int],
) -> PediatricDetection | None:
    """Escolhe o crop do popup priorizando crianca visivel no frame atual."""

    if not alert_child_track_ids:
        return None
    by_track = {item.track_id: item for item in detections}
    linked_visible_children = [
        by_track[track_id]
        for track_id in alert_child_track_ids
        if track_id in by_track and by_track[track_id].role == "child"
    ]
    if linked_visible_children:
        return max(
            linked_visible_children,
            key=lambda item: (detection_area(item), item.confidence),
        )

    visible_children = [item for item in detections if item.role == "child"]
    linked_visible = [
        by_track[track_id] for track_id in alert_child_track_ids if track_id in by_track
    ]
    if visible_children:
        best_visible_child = max(
            visible_children,
            key=lambda item: (detection_area(item), item.confidence),
        )
        linked_candidate = max(
            linked_visible,
            key=lambda item: (detection_area(item), item.confidence),
            default=None,
        )
        if (
            linked_candidate is not None
            and linked_candidate.confidence >= 0.60
            and detection_area(linked_candidate)
            >= detection_area(best_visible_child) * 0.90
        ):
            return linked_candidate
        return best_visible_child
    return linked_visible[0] if linked_visible else None


def draw_popup_alert_bbox(frame: Any, bbox_xyxy: tuple[float, float, float, float]) -> Any:
    """Desenha a evidencia do popup com cor fixa, independente da classe interna."""

    x1, y1, x2, y2 = map(int, bbox_xyxy)
    frame_h, frame_w = frame.shape[:2]
    x1, y1 = max(0, x1), max(0, y1)
    x2, y2 = min(frame_w - 1, x2), min(frame_h - 1, y2)
    if x2 <= x1 or y2 <= y1:
        return frame
    alert_color = (0, 80, 255)
    cv2.rectangle(frame, (x1, y1), (x2, y2), alert_color, 4)
    cv2.putText(
        frame,
        "ALERTA",
        (x1, max(24, y1 - 8)),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.8,
        alert_color,
        2,
        getattr(cv2, "LINE_AA", 16),
    )
    return frame


def safe_name(value: str) -> str:
    """Nome simples para arquivos/pastas de evidencia."""

    allowed = []
    for char in str(value or "camera"):
        if char.isalnum() or char in {"-", "_"}:
            allowed.append(char)
        elif char in {" ", ".", ":", "/"}:
            allowed.append("_")
    name = "".join(allowed).strip("_")
    return name[:80] or "camera"


def normalize_bbox_for_image(
    frame: Any,
    bbox_xyxy: tuple[float, float, float, float] | list[float] | None,
) -> tuple[int, int, int, int] | None:
    if frame is None or bbox_xyxy is None:
        return None
    if not hasattr(frame, "shape") or len(frame.shape) < 2:
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


def draw_event_evidence_frame(
    frame: Any,
    detections: list[PediatricDetection],
    selected: PediatricDetection | None = None,
) -> Any:
    canvas = frame.copy()
    selected_track = selected.track_id if selected is not None else None
    for item in detections:
        bbox = normalize_bbox_for_image(canvas, item.bbox_xyxy)
        if bbox is None:
            continue
        x1, y1, x2, y2 = bbox
        if item.track_id == selected_track:
            color = (0, 80, 255)
            thickness = 4
        elif item.role == "child":
            color = (0, 165, 255)
            thickness = 3
        elif item.role == "adult":
            color = (80, 180, 80)
            thickness = 2
        else:
            color = (180, 180, 180)
            thickness = 2
        cv2.rectangle(canvas, (x1, y1), (x2, y2), color, thickness)
        label = f"{item.role}:{item.confidence:.2f} id={item.track_id}"
        cv2.putText(
            canvas,
            label,
            (x1, max(18, y1 - 8)),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.55,
            color,
            2,
            getattr(cv2, "LINE_AA", 16),
        )
    return canvas


def choose_event_crop_detection(
    child: PediatricDetection | None,
    detections: list[PediatricDetection],
    person_detections: list[PediatricDetection],
) -> PediatricDetection | None:
    if child is not None:
        return child
    if detections:
        return max(detections, key=lambda item: (item.role == "child", item.confidence))
    if person_detections:
        return max(person_detections, key=lambda item: item.confidence)
    return None


def serialize_detection(item: PediatricDetection | None) -> dict[str, Any] | None:
    if item is None:
        return None
    return {
        "track_id": item.track_id,
        "role": item.role,
        "confidence": item.confidence,
        "bbox_xyxy": list(item.bbox_xyxy),
    }


class PediatriaDatasetCollector:
    """Coleta crops e frames para revisao humana/treino, sem afetar alertas."""

    def __init__(
        self,
        *,
        root_dir: Path,
        source_name: str,
        model_path: Path,
        requested_device: str,
        resolved_device: str,
        device_reason: str,
        sample_interval_frames: int = 15,
        max_crops_per_track: int = 40,
    ) -> None:
        self.root_dir = root_dir
        self.source_name = source_name
        self.model_path = model_path
        self.requested_device = requested_device
        self.resolved_device = resolved_device
        self.device_reason = device_reason
        self.sample_interval_frames = max(1, int(sample_interval_frames))
        self.max_crops_per_track = max(1, int(max_crops_per_track))
        self.session_dir = root_dir / safe_name(source_name)
        self.frames_dir = self.session_dir / "frames"
        self.crops_dir = self.session_dir / "crops"
        self.manifest_path = self.session_dir / "review_manifest.jsonl"
        self.summary_path = self.session_dir / "summary.json"
        self._crops_by_track: Counter[int] = Counter()
        self._saved_frames = 0
        self._saved_crops = 0
        self._role_counts: Counter[str] = Counter()
        self._write_readme()

    def collect(
        self,
        *,
        frame_index: int,
        clean_frame: Any,
        detections: list[PediatricDetection],
        state: Any,
    ) -> None:
        if frame_index % self.sample_interval_frames != 0 or not detections:
            return

        saved_items: list[tuple[PediatricDetection, Path]] = []
        for detection in sorted(
            detections,
            key=lambda item: (item.role, item.track_id, -item.confidence),
        ):
            if self._crops_by_track[detection.track_id] >= self.max_crops_per_track:
                continue
            crop = crop_frame(clean_frame, detection.bbox_xyxy)
            if crop is None:
                continue
            role_dir = self.crops_dir / f"pred_{safe_name(detection.role)}"
            role_dir.mkdir(parents=True, exist_ok=True)
            stem = (
                f"f{frame_index:06d}"
                f"__track_{detection.track_id}"
                f"__pred_{safe_name(detection.role)}"
                f"__conf_{int(detection.confidence * 1000):04d}"
            )
            crop_path = role_dir / f"{stem}.jpg"
            if not cv2.imwrite(str(crop_path), crop):
                continue
            self._crops_by_track[detection.track_id] += 1
            self._saved_crops += 1
            self._role_counts[detection.role] += 1
            saved_items.append((detection, crop_path))

        if not saved_items:
            return

        self.frames_dir.mkdir(parents=True, exist_ok=True)
        frame_path = self.frames_dir / f"f{frame_index:06d}.jpg"
        if not frame_path.exists() and cv2.imwrite(str(frame_path), clean_frame):
            self._saved_frames += 1

        child_state_by_track = {
            item.child_track_id: {
                "state": item.state,
                "nearest_adult_track_id": item.nearest_adult_track_id,
                "nearest_adult_distance_ratio": item.nearest_adult_distance_ratio,
            }
            for item in state.children
        }
        self.manifest_path.parent.mkdir(parents=True, exist_ok=True)
        with self.manifest_path.open("a", encoding="utf-8") as handle:
            for detection, crop_path in saved_items:
                record = {
                    "schema_version": 1,
                    "created_at": datetime.now().isoformat(timespec="milliseconds"),
                    "source": self.source_name,
                    "frame_index": frame_index,
                    "track_id": detection.track_id,
                    "predicted_role": detection.role,
                    "confidence": detection.confidence,
                    "bbox_xyxy": list(detection.bbox_xyxy),
                    "crop_path": str(crop_path),
                    "frame_path": str(frame_path),
                    "raw_state": state.raw_state,
                    "stable_state": state.stable_state,
                    "reason": state.reason,
                    "companionship": child_state_by_track.get(detection.track_id),
                    "model": str(self.model_path),
                    "requested_device": self.requested_device,
                    "resolved_device": self.resolved_device,
                    "device_reason": self.device_reason,
                    "human_label": None,
                    "training_eligible": False,
                    "review_notes": "",
                }
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        self._write_summary()

    def summary(self) -> dict[str, Any]:
        return {
            "mode": "pediatria_dataset_review_collection",
            "source": self.source_name,
            "session_dir": str(self.session_dir),
            "manifest": str(self.manifest_path),
            "sample_interval_frames": self.sample_interval_frames,
            "max_crops_per_track": self.max_crops_per_track,
            "saved_frames": self._saved_frames,
            "saved_crops": self._saved_crops,
            "role_counts": dict(self._role_counts),
            "tracks_sampled": len(self._crops_by_track),
        }

    def _write_summary(self) -> None:
        self.summary_path.parent.mkdir(parents=True, exist_ok=True)
        self.summary_path.write_text(
            json.dumps(self.summary(), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    def _write_readme(self) -> None:
        self.session_dir.mkdir(parents=True, exist_ok=True)
        readme = self.session_dir / "README.md"
        if readme.exists():
            return
        readme.write_text(
            "\n".join(
                [
                    "# Coleta pediatrica para revisao",
                    "",
                    "Este pacote foi gerado pelo modo de coleta do MVP local.",
                    "Ele nao altera o fluxo normal de alerta, popup, voz ou cooldown.",
                    "",
                    "Arquivos principais:",
                    "",
                    "- `review_manifest.jsonl`: um registro por crop salvo;",
                    "- `frames/`: frame original amostrado;",
                    "- `crops/pred_child`: crops que o modelo sugeriu como crianca;",
                    "- `crops/pred_adult`: crops que o modelo sugeriu como adulto;",
                    "- `crops/pred_uncertain`: crops ambiguos, quando existirem;",
                    "- `summary.json`: resumo rapido da sessao.",
                    "",
                    "Campos para revisao humana:",
                    "",
                    "- `human_label`: preencher com `child`, `adult`, `uncertain` ou `ignore`;",
                    "- `training_eligible`: usar `true` apenas em crops revisados e uteis;",
                    "- `review_notes`: observacao livre.",
                    "",
                    "Para treino, usar somente amostras com revisao humana.",
                ]
            ),
            encoding="utf-8",
        )


class PediatriaPopupDemo(QMainWindow):
    """Interface local de apresentacao. Nao chama servicos do WebGuardiao."""

    _service_check_done = pyqtSignal(object, bool)

    def __init__(
        self,
        *,
        model: Path,
        person_model: Path | None = None,
        report: Path,
        confidence: float,
        cooldown_seconds: float,
        device: str,
        initial_source: str = "",
        voice_enabled: bool = True,
        voice_repeat_seconds: float = 10.0,
        diagnostic_log_interval_frames: int = 60,
        force_cpu_default: bool = False,
        person_crop_pipeline_default: bool = True,
        crop_cache_frames: int = 12,
        person_confidence: float = 0.20,
        weak_child_candidates: bool = True,
        weak_child_confidence: float = 0.03,
        weak_child_promote_frames: int = 12,
        collect_dataset_default: bool = False,
        dataset_output_dir: Path | None = None,
        dataset_sample_interval_frames: int = 15,
        dataset_max_crops_per_track: int = 40,
    ) -> None:
        super().__init__()
        self.setWindowFlags(self.windowFlags() | Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self._drag_offset: QPoint | None = None
        self.model_path = model
        self.person_model_path = person_model or DEFAULT_PERSON_MODEL_PATH
        self.report_path = report
        self.confidence = confidence
        self.person_crop_pipeline_default = person_crop_pipeline_default
        self.crop_cache_frames = max(1, crop_cache_frames)
        self.person_confidence = max(0.01, min(0.90, person_confidence))
        self.diagnostic_log_interval_frames = max(0, diagnostic_log_interval_frames)
        self.collect_dataset_default = collect_dataset_default
        self.dataset_output_dir = dataset_output_dir
        self.dataset_sample_interval_frames = max(1, dataset_sample_interval_frames)
        self.dataset_max_crops_per_track = max(1, dataset_max_crops_per_track)
        self.dataset_collector: PediatriaDatasetCollector | None = None
        self.default_requested_device = device
        self.force_cpu_default = force_cpu_default
        self.weak_child_candidates_default = weak_child_candidates
        self.weak_child_confidence = max(0.01, min(0.24, weak_child_confidence))
        self.weak_child_promote_frames = max(1, weak_child_promote_frames)
        self.requested_device = "cpu" if force_cpu_default else device
        self.device, self.device_reason = resolve_inference_device(self.requested_device)
        self.performance_profile: dict[str, int | float | str | None] = {}
        self.runner: PediatricsDetectorMvpRunner | None = None
        self.crop_pipeline: PersonCropSpecialistPipeline | None = None
        self.service: PediatriaService | None = None
        self._loaded_specialist_model_path: Path | None = None
        self._loaded_person_model_path: Path | None = None
        self.capture: Any | None = None
        self.current_source: str | int | None = None
        # Reconexao automatica para fontes de rede/dispositivo (RTSP/HTTP/USB)
        # quando capture.read() falha (queda de rede, camera reiniciando,
        # etc.) -- nao se aplica a arquivo local, que so chega aqui em EOF
        # real e continua fazendo loop como antes.
        self._reconnecting = False
        self._reconnect_attempts = 0
        self._reconnect_max_attempts = 5
        self._reconnect_backoff_seconds = 2.0
        self._reconnect_backoff_max_seconds = 30.0
        self.analyzer = CompanionshipAnalyzer()
        self.role_adjuster = PediatricRoleContextAdjuster()
        self.identity_stabilizer = PediatricIdentityStabilizer()
        self.weak_child_promoter = WeakChildCandidatePromoter(
            min_hits=self.weak_child_promote_frames,
        )
        self.alert_latch = AlertEventLatch(cooldown_seconds=cooldown_seconds)
        self.frame_index = 0
        self.popup_count = 0
        self.states: Counter[str] = Counter()
        self.latencies: list[float] = []
        self.active_popups: list[PediatriaAlertPopup] = []
        self._resolved_frames_by_camera: Counter[str] = Counter()
        self._last_suppressed_log: dict[tuple[str, int, str], float] = {}
        self.session_started_at = datetime.now()
        self.session_id = self.session_started_at.strftime("%Y%m%d_%H%M%S")
        self.evidence_dir = self._make_session_evidence_dir()
        self.events_log_path = self.evidence_dir / "events.jsonl"
        self.session_event_counts: Counter[str] = Counter()
        self.session_status_counts: Counter[str] = Counter()
        self.session_suppression_counts: Counter[str] = Counter()
        self.session_evidence_error_counts: Counter[str] = Counter()
        self.session_person_detector_zero = 0
        self.telemetry = SessionTelemetry()
        self.voice = MvpVoiceAnnouncer(
            enabled=voice_enabled,
            repeat_interval_seconds=voice_repeat_seconds,
        )
        self._service_process: Any | None = None
        self._service_start_attempts = 0
        # Timers filhos de `self`: o Qt os cancela automaticamente quando a
        # janela e destruida, evitando callback em widget ja deletado.
        self._service_startup_timer = QTimer(self)
        self._service_startup_timer.setSingleShot(True)
        self._service_startup_timer.timeout.connect(self._auto_check_service_on_startup)
        self._service_poll_timer = QTimer(self)
        self._service_poll_timer.setSingleShot(True)
        self._service_poll_timer.timeout.connect(self._poll_service_startup)
        self._service_check_done.connect(lambda callback, running: callback(running))
        self._reconnect_timer = QTimer(self)
        self._reconnect_timer.setSingleShot(True)
        self._reconnect_timer.timeout.connect(self._attempt_reconnect_capture)

        self._build_ui(initial_source)

        self.timer = QTimer(self)
        self.timer.timeout.connect(self._next_frame)
        self.setWindowTitle("WebGuardiao IA Pediatria - Demo Viewer")
        self.setMinimumSize(980, 600)
        self.resize(1180, 700)

        self._service_startup_timer.start(200)

    def _make_session_evidence_dir(self) -> Path:
        return self.report_path.parent / "evidence" / "sessions" / self.session_id

    def _build_ui(self, initial_source: str) -> None:
        self.stack = QStackedWidget()
        self.stack.setObjectName("pageStack")
        self.login_page = self._build_login_page()
        self.viewer_page = self._build_viewer_page(initial_source)
        self.stack.addWidget(self.login_page)
        self.stack.addWidget(self.viewer_page)
        self.setCentralWidget(self.stack)
        self._update_source_controls(self.source_mode.currentText())

    def _build_login_page(self) -> QWidget:
        page = QWidget()
        page.setObjectName("loginRoot")
        page.setStyleSheet(self._showroom_stylesheet())

        root = QVBoxLayout(page)
        root.setContentsMargins(14, 14, 14, 14)
        root.setSpacing(0)

        shell = QFrame()
        shell.setObjectName("loginShell")
        shell_shadow = QGraphicsDropShadowEffect(shell)
        shell_shadow.setBlurRadius(42)
        shell_shadow.setOffset(0, 0)
        shell_shadow.setColor(QColor(0, 160, 255, 52))
        shell.setGraphicsEffect(shell_shadow)
        root.addWidget(shell, 1)

        shell_layout = QVBoxLayout(shell)
        shell_layout.setContentsMargins(18, 14, 18, 18)
        shell_layout.setSpacing(10)

        header_frame = QFrame()
        header_frame.setObjectName("windowHeader")
        self.login_drag_header = header_frame
        header = QHBoxLayout(header_frame)
        header.setContentsMargins(0, 0, 0, 0)
        header.setSpacing(14)
        logo = QLabel(
            '<span style="font-size:22px;font-weight:800;color:#eaf4ff;">DSA</span>'
            '<span style="font-size:22px;font-weight:800;color:#00a7ff;">WEB</span><br>'
            '<span style="font-size:8px;letter-spacing:5px;color:#9aa8b8;">SOLUCOES</span>'
        )
        logo.setObjectName("brandLogo")
        header.addWidget(logo)
        header.addStretch(1)
        section = QLabel("IA Pediatria")
        section.setObjectName("topAccent")
        header.addWidget(section)
        divider = QFrame()
        divider.setFrameShape(QFrame.Shape.VLine)
        divider.setObjectName("topDivider")
        header.addWidget(divider)
        demo = QLabel("Demo Viewer")
        demo.setObjectName("topText")
        header.addWidget(demo)
        self.login_minimize_button = QPushButton("-")
        self.login_minimize_button.setObjectName("windowButton")
        self.login_minimize_button.setFixedSize(32, 28)
        self.login_minimize_button.clicked.connect(self.showMinimized)
        header.addWidget(self.login_minimize_button)
        self.login_close_button = QPushButton("X")
        self.login_close_button.setObjectName("closeButton")
        self.login_close_button.setFixedSize(32, 28)
        self.login_close_button.clicked.connect(self.close)
        header.addWidget(self.login_close_button)
        shell_layout.addWidget(header_frame)
        body = QHBoxLayout()
        body.setContentsMargins(10, 10, 10, 0)
        body.setSpacing(34)
        shell_layout.addLayout(body, 1)

        left = QVBoxLayout()
        left.setSpacing(18)
        left.addStretch(1)
        title = QLabel(
            '<span style="color:#008dff;">WEB</span>'
            '<span style="color:#f5f8ff;">GUARDIAO</span>'
        )
        title.setObjectName("heroTitle")
        subtitle = QLabel("Inteligencia Artificial para\nMonitoramento e Protecao")
        subtitle.setObjectName("heroSubtitle")
        left.addWidget(title)
        left.addWidget(subtitle)
        line = QFrame()
        line.setFrameShape(QFrame.Shape.HLine)
        line.setObjectName("heroLine")
        left.addWidget(line)
        left.addWidget(self._feature_row("AI", "Monitoramento Inteligente", "Deteccao avancada de eventos e comportamentos em tempo real."))
        left.addWidget(self._feature_row("CV", "IA Especializada", "Modelos dedicados para cada cenario e necessidade."))
        left.addWidget(self._feature_row("!", "Alertas em Tempo Real", "Notificacoes instantaneas para acao imediata."))
        left.addWidget(self._feature_row("BI", "Analytics e Relatorios", "Dashboards completos para melhor tomada de decisao."))
        left.addStretch(2)
        badge = QLabel("  Solucao completa para ambientes corporativos e criticos.")
        badge.setObjectName("bottomBadge")
        left.addWidget(badge)
        body.addLayout(left, 1)

        card = QFrame()
        card.setObjectName("loginCard")
        card.setMinimumWidth(390)
        card.setMaximumWidth(460)
        shadow = QGraphicsDropShadowEffect(card)
        shadow.setBlurRadius(34)
        shadow.setOffset(0, 0)
        shadow.setColor(QColor(0, 160, 255, 65))
        card.setGraphicsEffect(shadow)
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(34, 28, 34, 24)
        card_layout.setSpacing(12)

        shield = QLabel("IA")
        shield.setObjectName("shieldMark")
        shield.setAlignment(Qt.AlignmentFlag.AlignCenter)
        card_layout.addWidget(shield, alignment=Qt.AlignmentFlag.AlignHCenter)

        card_title = QLabel("Acesso ao Sistema")
        card_title.setObjectName("cardTitle")
        card_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        card_layout.addWidget(card_title)
        card_subtitle = QLabel("Faca login para continuar")
        card_subtitle.setObjectName("cardSubtitle")
        card_subtitle.setAlignment(Qt.AlignmentFlag.AlignCenter)
        card_layout.addWidget(card_subtitle)

        user_label = QLabel("Usuario")
        user_label.setObjectName("fieldLabel")
        self.login_user_value = QLineEdit()
        self.login_user_value.setObjectName("loginField")
        self.login_user_value.setPlaceholderText("Digite seu usuario")
        pass_label = QLabel("Senha")
        pass_label.setObjectName("fieldLabel")
        self.login_password_value = QLineEdit()
        self.login_password_value.setObjectName("loginField")
        self.login_password_value.setPlaceholderText("Digite sua senha")
        self.login_password_value.setEchoMode(QLineEdit.EchoMode.Password)
        self.login_password_value.returnPressed.connect(self._login_to_viewer)
        card_layout.addSpacing(6)
        card_layout.addWidget(user_label)
        card_layout.addWidget(self.login_user_value)
        card_layout.addWidget(pass_label)
        card_layout.addWidget(self.login_password_value)
        self.login_error_label = QLabel("")
        self.login_error_label.setObjectName("loginError")
        self.login_error_label.setVisible(False)
        card_layout.addWidget(self.login_error_label)

        options = QHBoxLayout()
        self.remember_checkbox = QCheckBox("Lembrar-me")
        self.remember_checkbox.setObjectName("rememberCheck")
        forgot = QLabel("Esqueci minha senha")
        forgot.setObjectName("linkLabel")
        options.addWidget(self.remember_checkbox)
        options.addStretch(1)
        options.addWidget(forgot)
        card_layout.addLayout(options)

        self.login_button = QPushButton("Entrar")
        self.login_button.setObjectName("primaryButton")
        self.login_button.clicked.connect(self._login_to_viewer)
        card_layout.addWidget(self.login_button)

        separator = QHBoxLayout()
        line_left = QFrame()
        line_left.setFrameShape(QFrame.Shape.HLine)
        line_left.setObjectName("cardLine")
        line_right = QFrame()
        line_right.setFrameShape(QFrame.Shape.HLine)
        line_right.setObjectName("cardLine")
        or_label = QLabel("ou")
        or_label.setObjectName("orLabel")
        separator.addWidget(line_left)
        separator.addWidget(or_label)
        separator.addWidget(line_right)
        card_layout.addLayout(separator)

        self.demo_access_button = QPushButton("Acesso Rapido (Demo)")
        self.demo_access_button.setObjectName("secondaryButton")
        self.demo_access_button.clicked.connect(self._enter_viewer)
        card_layout.addWidget(self.demo_access_button)
        card_layout.addStretch(1)
        footer = QLabel("© 2026 DSAWEB Solucoes. Todos os direitos reservados.")
        footer.setObjectName("cardFooter")
        footer.setAlignment(Qt.AlignmentFlag.AlignCenter)
        card_layout.addWidget(footer)
        body.addWidget(card, 0, Qt.AlignmentFlag.AlignVCenter)
        return page

    def _feature_row(self, icon: str, title: str, description: str) -> QWidget:
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(14)
        icon_box = QLabel(icon)
        icon_box.setObjectName("featureIcon")
        icon_box.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon_box.setFixedSize(62, 62)
        text = QVBoxLayout()
        text.setSpacing(2)
        title_label = QLabel(title)
        title_label.setObjectName("featureTitle")
        desc_label = QLabel(description)
        desc_label.setObjectName("featureDesc")
        desc_label.setWordWrap(True)
        text.addWidget(title_label)
        text.addWidget(desc_label)
        layout.addWidget(icon_box)
        layout.addLayout(text, 1)
        return row

    def _build_viewer_page(self, initial_source: str) -> QWidget:
        page = QWidget()
        page.setObjectName("viewerRoot")
        page.setStyleSheet(self._showroom_stylesheet())
        layout = QVBoxLayout(page)
        layout.setContentsMargins(14, 12, 14, 14)
        layout.setSpacing(8)

        header = QHBoxLayout()
        brand = QLabel(
            '<span style="color:#00a7ff;font-weight:800;">WEB</span>'
            '<span style="color:#f5f8ff;font-weight:800;">GUARDIAO</span>'
        )
        brand.setObjectName("viewerBrand")
        header.addWidget(brand)
        self.viewer_badge = QLabel("IA Pediatria | Demo Viewer")
        self.viewer_badge.setObjectName("viewerBadge")
        header.addStretch(1)
        header.addWidget(self.viewer_badge)
        self.service_status_label = QLabel("API: verificando...")
        self.service_status_label.setObjectName("serviceStatus")
        header.addWidget(self.service_status_label)
        self.service_button = QPushButton("Ativar API")
        self.service_button.setObjectName("serviceButton")
        self.service_button.clicked.connect(self._on_service_button_clicked)
        header.addWidget(self.service_button)
        self.back_to_login_button = QPushButton("Sair")
        self.back_to_login_button.setObjectName("ghostButton")
        self.back_to_login_button.clicked.connect(self._logout_to_login)
        header.addWidget(self.back_to_login_button)
        layout.addLayout(header)

        controls_card = QFrame()
        controls_card.setObjectName("controlCard")
        panel_layout = QGridLayout(controls_card)
        panel_layout.setContentsMargins(10, 8, 10, 8)
        panel_layout.setHorizontalSpacing(8)
        panel_layout.setVerticalSpacing(6)

        self.source_mode = QComboBox()
        self.source_mode.addItems([SOURCE_FILE, SOURCE_URL, SOURCE_USB])
        self.source_mode.currentTextChanged.connect(self._update_source_controls)
        panel_layout.addWidget(QLabel("Camera/fonte"), 0, 0)
        panel_layout.addWidget(self.source_mode, 0, 1)

        self.source_value = QLineEdit(initial_source)
        self.source_value.setPlaceholderText("Escolha um video, URL RTSP ou camera USB")
        self.browse_button = QPushButton("Escolher")
        self.browse_button.clicked.connect(self._browse_video)
        panel_layout.addWidget(self.source_value, 0, 2, 1, 3)
        panel_layout.addWidget(self.browse_button, 0, 5)

        self.usb_index = QSpinBox()
        self.usb_index.setRange(0, 20)
        panel_layout.addWidget(QLabel("USB"), 0, 6)
        panel_layout.addWidget(self.usb_index, 0, 7)

        self.force_cpu_checkbox = QCheckBox("Forcar CPU")
        self.force_cpu_checkbox.setChecked(self.force_cpu_default)
        panel_layout.addWidget(self.force_cpu_checkbox, 1, 0, 1, 2)

        self.collect_dataset_checkbox = QCheckBox("Modo coleta/treino")
        self.collect_dataset_checkbox.setChecked(self.collect_dataset_default)
        panel_layout.addWidget(self.collect_dataset_checkbox, 1, 2, 1, 2)

        self.start_button = QPushButton("Iniciar")
        self.start_button.setObjectName("primarySmallButton")
        self.start_button.clicked.connect(self.start_analysis)
        self.stop_button = QPushButton("Parar")
        self.stop_button.setObjectName("dangerButton")
        self.stop_button.clicked.connect(self.stop_analysis)
        self.stop_button.setEnabled(False)
        panel_layout.addWidget(self.start_button, 1, 4)
        panel_layout.addWidget(self.stop_button, 1, 5)
        panel_layout.setColumnStretch(2, 1)
        layout.addWidget(controls_card)

        status_row = QHBoxLayout()
        self.pipeline_status = QLabel("ANALISANDO: aguardando inicio")
        self.pipeline_status.setObjectName("pipelineStatus")
        self.status = self.pipeline_status
        self.session_summary_label = QLabel("Sessao: 0 frames | 0 alertas")
        self.session_summary_label.setObjectName("sessionSummary")
        status_row.addWidget(self.pipeline_status, 1)
        status_row.addWidget(self.session_summary_label)
        layout.addLayout(status_row)

        self.video = QLabel("Selecione uma fonte e clique em Iniciar")
        self.video.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.video.setMinimumSize(720, 420)
        self.video.setObjectName("videoPreview")
        self.video.setScaledContents(True)
        layout.addWidget(self.video, 1)


        # Configuracoes internas/protegidas: continuam existindo para CLI/testes,
        # mas nao aparecem no viewer operacional.
        self.model_value = QLineEdit(str(self.model_path))
        self.model_value.setVisible(False)
        self.model_browse_button = QPushButton("Escolher modelo...")
        self.model_browse_button.setVisible(False)
        self.model_browse_button.clicked.connect(self._browse_specialist_model)
        self.person_model_value = QLineEdit(str(self.person_model_path))
        self.person_model_value.setVisible(False)
        self.person_model_browse_button = QPushButton("Escolher pessoa...")
        self.person_model_browse_button.setVisible(False)
        self.person_model_browse_button.clicked.connect(self._browse_person_model)
        self.person_crop_checkbox = QCheckBox("Pessoa -> crop -> especialista")
        self.person_crop_checkbox.setChecked(self.person_crop_pipeline_default)
        self.person_crop_checkbox.setVisible(False)
        self.weak_child_checkbox = QCheckBox("Usar candidatos infantis fracos persistentes")
        self.weak_child_checkbox.setChecked(self.weak_child_candidates_default)
        self.weak_child_checkbox.setVisible(False)
        self.dataset_interval = QSpinBox()
        self.dataset_interval.setRange(1, 600)
        self.dataset_interval.setValue(self.dataset_sample_interval_frames)
        self.dataset_interval.setVisible(False)
        self.dataset_max_per_track = QSpinBox()
        self.dataset_max_per_track.setRange(1, 1000)
        self.dataset_max_per_track.setValue(self.dataset_max_crops_per_track)
        self.dataset_max_per_track.setVisible(False)
        return page

    def _login_to_viewer(self) -> None:
        user = self.login_user_value.text().strip()
        password = self.login_password_value.text().strip()
        if user == "admin" and password == "admin":
            self.login_error_label.setVisible(False)
            self._enter_viewer()
            return
        self.login_error_label.setText("Usuario ou senha invalidos. Use admin/admin para demo.")
        self.login_error_label.setVisible(True)
        self.login_password_value.clear()
        self.login_password_value.setFocus()

    def _enter_viewer(self) -> None:
        self.stack.setCurrentIndex(1)
        self.source_value.setFocus()

    def _logout_to_login(self) -> None:
        if self.capture is not None or self.timer.isActive():
            self.stop_analysis()
        self.login_password_value.clear()
        self.login_error_label.setVisible(False)
        self.stack.setCurrentIndex(0)
        self.login_user_value.setFocus()

    def _is_login_drag_target(self, pos: QPoint) -> bool:
        if self.stack.currentWidget() is not self.login_page:
            return False
        child = self.childAt(pos)
        blocked_types = (QPushButton, QLineEdit, QComboBox, QCheckBox, QSpinBox)
        while child is not None:
            if isinstance(child, blocked_types):
                return False
            if child is self.login_drag_header:
                return True
            child = child.parentWidget()
        return False

    def mousePressEvent(self, event: Any) -> None:
        if (
            event.button() == Qt.MouseButton.LeftButton
            and self._is_login_drag_target(event.position().toPoint())
        ):
            self._drag_offset = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: Any) -> None:
        if self._drag_offset is not None and event.buttons() & Qt.MouseButton.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_offset)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: Any) -> None:
        self._drag_offset = None
        super().mouseReleaseEvent(event)
    def _showroom_stylesheet(self) -> str:
        return """
        QWidget#loginRoot {
            background: transparent;
            color: #eef5ff;
            font-family: Segoe UI, Arial, sans-serif;
        }
        QWidget#viewerRoot {
            background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                stop:0 #020814, stop:0.52 #061322, stop:1 #020814);
            color: #eef5ff;
            font-family: Segoe UI, Arial, sans-serif;
        }
        QFrame#loginShell {
            background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                stop:0 rgba(7, 18, 34, 232), stop:0.52 rgba(5, 22, 42, 214), stop:1 rgba(3, 9, 20, 235));
            border: 1px solid rgba(84, 150, 214, 170);
            border-radius: 22px;
        }
        QFrame#windowHeader {
            background: transparent;
            min-height: 32px;
        }
        QLabel { color: #eef5ff; }
        QLineEdit, QComboBox, QSpinBox {
            background: rgba(3, 12, 24, 185);
            border: 1px solid rgba(132, 164, 196, 120);
            border-radius: 8px;
            color: #edf6ff;
            min-height: 30px;
            padding: 4px 10px;
            selection-background-color: #007dff;
        }
        QComboBox::drop-down { width: 24px; border: 0; }
        QCheckBox { color: #d8e6f5; spacing: 8px; }
        QCheckBox::indicator {
            width: 18px; height: 18px;
            border: 1px solid rgba(148, 177, 206, 150);
            border-radius: 4px;
            background: rgba(4, 13, 27, 210);
        }
        QCheckBox::indicator:checked { background: #008dff; border-color: #2ab7ff; }
        QPushButton {
            border-radius: 8px;
            min-height: 30px;
            padding: 6px 14px;
            color: #eff8ff;
            background: rgba(13, 36, 63, 190);
            border: 1px solid rgba(0, 157, 255, 130);
            font-weight: 600;
        }
        QPushButton:hover { background: rgba(0, 121, 231, 190); }
        QPushButton:disabled { color: #6e7f8f; border-color: #31465b; background: #111b27; }
        QLabel#brandLogo { line-height: 1.0; }
        QLabel#topAccent { color: #12aaff; font-size: 15px; font-weight: 700; }
        QLabel#topText { color: #c8d2df; font-size: 15px; }
        QPushButton#windowButton, QPushButton#closeButton {
            color: #c8d8e8;
            background: rgba(4, 16, 31, 85);
            border: 1px solid rgba(115, 152, 190, 100);
            border-radius: 8px;
            font-weight: 800;
            min-height: 24px;
            padding: 0;
        }
        QPushButton#windowButton:hover { background: rgba(0, 140, 255, 95); color: #ffffff; }
        QPushButton#closeButton:hover { background: rgba(255, 80, 80, 155); color: #ffffff; border-color: rgba(255, 135, 135, 190); }
        QFrame#topDivider { color: rgba(151, 171, 194, 80); max-height: 28px; }
        QLabel#heroTitle { font-size: 50px; font-weight: 900; letter-spacing: -1px; }
        QLabel#heroSubtitle { color: #b9c4d2; font-size: 24px; line-height: 1.25; }
        QFrame#heroLine { color: #008dff; max-height: 1px; }
        QLabel#featureIcon {
            color: #19b8ff;
            border: 1px solid rgba(0, 145, 255, 170);
            border-radius: 10px;
            background: rgba(2, 16, 34, 190);
            font-size: 18px;
            font-weight: 800;
        }
        QLabel#featureTitle { font-size: 17px; font-weight: 700; color: #f5f8ff; }
        QLabel#featureDesc { font-size: 14px; color: #bdc9d8; line-height: 1.3; }
        QLabel#bottomBadge {
            color: #d5dfeb;
            background: rgba(2, 10, 22, 170);
            border: 1px solid rgba(66, 96, 126, 130);
            border-radius: 9px;
            padding: 10px 14px;
        }
        QFrame#loginCard {
            background: rgba(4, 14, 28, 188);
            border: 1px solid rgba(84, 150, 214, 170);
            border-radius: 18px;
        }
        QLabel#shieldMark {
            min-width: 74px; min-height: 74px;
            border-radius: 37px;
            border: 2px solid #00a7ff;
            color: #19b8ff;
            background: rgba(0, 126, 255, 30);
            font-size: 22px;
            font-weight: 900;
        }
        QLabel#cardTitle { font-size: 24px; font-weight: 800; }
        QLabel#cardSubtitle { font-size: 15px; color: #b7c3d1; }
        QLabel#fieldLabel { font-size: 13px; font-weight: 700; color: #eef5ff; }
        QLineEdit#loginField { min-height: 36px; font-size: 14px; padding-left: 12px; }
        QLabel#linkLabel { color: #0aa7ff; }
        QLabel#loginError { color: #ff8f8f; font-size: 12px; }
        QPushButton#primaryButton {
            min-height: 38px;
            background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #008cff, stop:1 #0752c9);
            border: 1px solid #209bff;
            font-size: 18px;
            font-weight: 800;
        }
        QPushButton#secondaryButton {
            min-height: 36px;
            color: #15adff;
            background: rgba(2, 14, 29, 170);
            border: 1px solid rgba(0, 157, 255, 180);
            font-size: 14px;
            font-weight: 800;
        }
        QFrame#cardLine { color: rgba(146, 165, 186, 80); }
        QLabel#orLabel, QLabel#cardFooter { color: #b8c4d1; }
        QLabel#viewerBrand { font-size: 22px; }
        QLabel#viewerBadge {
            color: #13aaff;
            border: 1px solid rgba(0, 157, 255, 120);
            border-radius: 14px;
            padding: 6px 12px;
            background: rgba(0, 120, 255, 20);
        }
        QPushButton#ghostButton, QPushButton#serviceButton { background: transparent; color: #b9c8d8; border-color: rgba(106, 135, 164, 130); min-height: 28px; padding: 4px 10px; }
        QFrame#controlCard {
            background: rgba(5, 18, 35, 210);
            border: 1px solid rgba(67, 103, 141, 150);
            border-radius: 12px;
        }
        QPushButton#primarySmallButton { background: #007dff; border-color: #18aaff; }
        QPushButton#dangerButton { background: #172335; border-color: rgba(255, 110, 110, 140); }
        QLabel#pipelineStatus {
            background: rgba(0, 125, 255, 55);
            border: 1px solid rgba(0, 157, 255, 140);
            border-radius: 9px;
            padding: 8px 12px;
            font-weight: 800;
        }
        QLabel#sessionSummary { color: #b8c7d7; padding: 8px 10px; }
        QLabel#videoPreview {
            background: #02070d;
            color: #8294a7;
            border: 1px solid rgba(66, 101, 137, 150);
            border-radius: 12px;
        }
        """
    def _update_source_controls(self, mode: str) -> None:
        is_usb = mode == SOURCE_USB
        self.usb_index.setEnabled(is_usb)
        self.source_value.setEnabled(not is_usb)
        self.browse_button.setEnabled(mode == SOURCE_FILE)
        placeholders = {
            SOURCE_FILE: "Escolha um video local",
            SOURCE_URL: "rtsp://usuario:senha@camera/stream",
            SOURCE_USB: "O indice USB sera usado",
        }
        self.source_value.setPlaceholderText(placeholders[mode])

    def _set_service_status(self, running: bool | None, note: str = "") -> None:
        if running is True:
            text = "API online"
            style = (
                "color: #7cffb2; background: rgba(24, 180, 105, 35); "
                "border: 1px solid rgba(78, 255, 170, 120); border-radius: 12px; "
                "padding: 5px 10px; font-weight: 800;"
            )
            self.service_button.setText("Verificar")
        elif running is False:
            text = "API offline" + (f" - {note}" if note else "")
            style = (
                "color: #ffb2b2; background: rgba(220, 80, 80, 28); "
                "border: 1px solid rgba(255, 130, 130, 110); border-radius: 12px; "
                "padding: 5px 10px; font-weight: 800;"
            )
            self.service_button.setText("Ativar API")
        else:
            text = f"API: {note or 'verificando...'}"
            style = (
                "color: #b8c7d7; background: rgba(0, 120, 255, 18); "
                "border: 1px solid rgba(0, 157, 255, 90); border-radius: 12px; "
                "padding: 5px 10px; font-weight: 700;"
            )
            self.service_button.setText("Aguarde")
        self.service_status_label.setText(text)
        self.service_status_label.setStyleSheet(style)
    def _check_service_async(self, callback: Callable[[bool], None]) -> None:
        """Roda o health-check numa thread separada.

        `is_service_running` faz uma chamada de rede bloqueante; rodando na
        thread principal do Qt ela trava o loop de video/frames (mesma thread
        do QTimer de captura) enquanto espera resposta. O resultado volta via
        signal, que o Qt entrega de forma thread-safe na thread da GUI.
        """
        def worker() -> None:
            running = service_launcher.is_service_running()
            self._service_check_done.emit(callback, running)

        threading.Thread(target=worker, daemon=True).start()

    def _auto_check_service_on_startup(self) -> None:
        self._check_service_async(self._handle_startup_check)

    def _handle_startup_check(self, running: bool) -> None:
        # So checa e mostra o status: nao sobe o servico sozinho. Um segundo
        # processo Python disputando CPU com a inferencia (sobretudo com
        # force_cpu) deixa a analise mais lenta. Quem precisar do servico
        # local clica em "Iniciar servico".
        self._set_service_status(running, "" if running else "nao iniciado")

    def _on_service_button_clicked(self) -> None:
        self.service_button.setEnabled(False)
        self._set_service_status(None, "verificando...")
        self._check_service_async(self._handle_button_check)

    def _handle_button_check(self, running: bool) -> None:
        if running:
            self._set_service_status(True)
            self.service_button.setEnabled(True)
            return
        self._start_service_and_poll()

    def _start_service_and_poll(self) -> None:
        self.service_button.setEnabled(False)
        self._set_service_status(None, "iniciando...")
        try:
            self._service_process = service_launcher.start_service_process()
        except Exception as exc:
            self._set_service_status(False, f"falha ao iniciar ({exc})")
            self.service_button.setEnabled(True)
            return
        self._service_start_attempts = 0
        self._service_poll_timer.start(500)

    def _poll_service_startup(self) -> None:
        self._check_service_async(self._handle_poll_check)

    def _handle_poll_check(self, running: bool) -> None:
        self._service_start_attempts += 1
        if running:
            self._set_service_status(True)
            self.service_button.setEnabled(True)
            return
        if self._service_start_attempts >= 20:
            self._set_service_status(False, "nao respondeu a tempo")
            self.service_button.setEnabled(True)
            return
        self._service_poll_timer.start(500)

    def _browse_video(self) -> None:
        filename, _filter = QFileDialog.getOpenFileName(
            self,
            "Escolher video para apresentacao",
            str(ROOT / "storage" / "videos"),
            VIDEO_FILE_FILTER,
        )
        if filename:
            self.source_value.setText(filename)

    def _browse_specialist_model(self) -> None:
        filename, _filter = QFileDialog.getOpenFileName(
            self,
            "Escolher modelo especialista v5/v6",
            str(ROOT / "src" / "models"),
            "Modelos (*.pt *.xml);;Todos os arquivos (*)",
        )
        if filename:
            self.model_value.setText(filename)

    def _browse_person_model(self) -> None:
        filename, _filter = QFileDialog.getOpenFileName(
            self,
            "Escolher detector de pessoa",
            str(ROOT / "src" / "models"),
            "Modelos (*.pt *.xml);;Todos os arquivos (*)",
        )
        if filename:
            self.person_model_value.setText(filename)

    def start_analysis(self) -> None:
        capture: Any | None = None
        try:
            self.requested_device = (
                "cpu"
                if self.force_cpu_checkbox.isChecked()
                else self.default_requested_device
            )
            self.device, self.device_reason = resolve_inference_device(
                self.requested_device
            )
            source = resolve_capture_source(
                self.source_mode.currentText(),
                self.source_value.text(),
                self.usb_index.value(),
            )
            capture = open_capture_source(source)
            if not capture.isOpened():
                capture.release()
                suffix = Path(source).suffix.lower() if isinstance(source, str) else ""
                extra = (
                    " Arquivos .dav dependem do codec/FFmpeg disponivel no OpenCV."
                    if suffix == ".dav"
                    else ""
                )
                raise OSError(f"Nao foi possivel abrir a fonte selecionada.{extra}")
            selected_model_path = resolve_model_path(str(self.model_path), self.model_path)
            selected_person_model_path = resolve_model_path(
                str(self.person_model_path),
                self.person_model_path,
            )
            assert capture is not None  # capture.isOpened() ja validou acima
            fps = capture.get(cv2.CAP_PROP_FPS) or 30.0
            self.performance_profile = performance_profile_for_device(self.device, fps)
            self.status.setText("Preparando analise local...")
            QApplication.processEvents()
            self.service = PediatriaService(
                PediatriaServiceConfig(
                    specialist_model_path=selected_model_path,
                    person_model_path=selected_person_model_path,
                    confidence=self.confidence,
                    device=self.device,
                    person_crop_pipeline=self.person_crop_pipeline_default,
                    person_confidence=self.person_confidence,
                    crop_cache_frames=self.crop_cache_frames,
                    weak_child_candidates=self.weak_child_candidates_default,
                    weak_child_confidence=self.weak_child_confidence,
                    detector_stride_frames=int(self.performance_profile["detector_stride_frames"] or 1),
                    specialist_budget_per_frame=(
                        int(self.performance_profile["specialist_budget_per_frame"])
                        if self.performance_profile["specialist_budget_per_frame"] is not None
                        else None
                    ),
                ),
                runner_factory=PediatricsDetectorMvpRunner,
                crop_pipeline_factory=PersonCropSpecialistPipeline,
                identity_stabilizer_factory=PediatricIdentityStabilizer,
                weak_child_promoter_factory=lambda: WeakChildCandidatePromoter(
                    min_hits=self.weak_child_promote_frames,
                ),
            )
            self.service.start_session()
            self.runner = self.service.runner
            self.crop_pipeline = self.service.crop_pipeline
            self.model_path = selected_model_path
            self.person_model_path = selected_person_model_path
            self._loaded_specialist_model_path = selected_model_path
            self._loaded_person_model_path = selected_person_model_path
        except (OSError, ValueError, FileNotFoundError) as exc:
            if capture is not None:
                capture.release()
            QMessageBox.warning(self, "Fonte indisponivel", str(exc))
            self.status.setText(f"Falha ao iniciar: {exc}")
            return
        except Exception as exc:
            if capture is not None:
                capture.release()
            QMessageBox.warning(self, "Falha ao preparar", str(exc))
            self.status.setText(f"Falha ao preparar analise: {exc}")
            return

        self.stop_analysis()
        self.capture = capture
        self.current_source = source
        self._reconnecting = False
        self._reconnect_attempts = 0
        self._reconnect_timer.stop()
        if self.service is not None:
            self.service.reset_session_state()
        self.alert_latch.clear()
        self.frame_index = 0
        self.popup_count = 0
        self.states.clear()
        self.latencies.clear()
        self._resolved_frames_by_camera.clear()
        self._last_suppressed_log.clear()
        self.telemetry = SessionTelemetry()
        self.session_started_at = datetime.now()
        self.session_id = self.session_started_at.strftime("%Y%m%d_%H%M%S")
        self.evidence_dir = self._make_session_evidence_dir()
        self.evidence_dir.mkdir(parents=True, exist_ok=True)
        self.events_log_path = self.evidence_dir / "events.jsonl"
        self.session_event_counts.clear()
        self.session_status_counts.clear()
        self.session_suppression_counts.clear()
        self.session_evidence_error_counts.clear()
        self.session_person_detector_zero = 0
        self.dataset_collector = None
        source_name = source_display_name(source)
        if self.collect_dataset_checkbox.isChecked():
            collection_root = (
                self.dataset_output_dir
                or self.report_path.parent / "dataset_review_collection"
            )
            self.dataset_collector = PediatriaDatasetCollector(
                root_dir=collection_root
                / self.session_started_at.strftime("coleta_%Y%m%d_%H%M%S"),
                source_name=source_name,
                model_path=self.model_path,
                requested_device=self.requested_device,
                resolved_device=self.device,
                device_reason=self.device_reason,
                sample_interval_frames=self.dataset_sample_interval_frames,
                max_crops_per_track=self.dataset_max_crops_per_track,
            )
        self.timer.start(max(1, int(1000 / float(self.performance_profile["display_fps"] or 12.0))))
        self.start_button.setEnabled(False)
        self.stop_button.setEnabled(True)
        self.force_cpu_checkbox.setEnabled(False)
        self.collect_dataset_checkbox.setEnabled(False)
        self._append_session_log(
            {
                "event_type": "analysis_started",
                "source": source_name,
                "raw_source": redact_source_value(source),
                "source_fps": fps,
                "requested_device": self.requested_device,
                "resolved_device": self.device,
                "device_reason": self.device_reason,
                "force_cpu": self.force_cpu_checkbox.isChecked(),
                "person_crop_pipeline": self.person_crop_pipeline_default,
                "performance_profile": self.performance_profile,
                "dataset_collection": (
                    self.dataset_collector.summary()
                    if self.dataset_collector is not None
                    else None
                ),
            }
        )
        self.status.setText(f"Analisando {source_name} | CPU/GPU: {self.device}")

    def stop_analysis(self) -> None:
        self.timer.stop()
        self._reconnect_timer.stop()
        self._reconnecting = False
        if self.capture is not None:
            self.capture.release()
            self.capture = None
        self.start_button.setEnabled(True)
        self.stop_button.setEnabled(False)
        self.force_cpu_checkbox.setEnabled(True)
        self.model_value.setEnabled(True)
        self.model_browse_button.setEnabled(True)
        self.person_crop_checkbox.setEnabled(True)
        self.person_model_value.setEnabled(True)
        self.person_model_browse_button.setEnabled(True)
        self.weak_child_checkbox.setEnabled(True)
        self.collect_dataset_checkbox.setEnabled(True)
        self.dataset_interval.setEnabled(True)
        self.dataset_max_per_track.setEnabled(True)
        if self.current_source is not None:
            self._append_session_log(
                {
                    "event_type": "analysis_stopped",
                    "source": source_display_name(self.current_source),
                    "frames_processed": self.frame_index,
                    "popup_count": self.popup_count,
                    "stable_state_counts": dict(self.states),
                    "average_latency_ms": (
                        round(sum(self.latencies) / len(self.latencies), 2)
                        if self.latencies
                        else None
                    ),
                    "dataset_collection": (
                        self.dataset_collector.summary()
                        if self.dataset_collector is not None
                        else None
                    ),
                }
            )
            if self.dataset_collector is not None:
                self.dataset_collector._write_summary()
            self._write_report()
            self.status.setText("Analise parada. Relatorio salvo.")
            self.current_source = None

    def _begin_reconnect(self) -> None:
        """Fonte de rede/dispositivo parou de entregar frames (queda de rede,
        camera reiniciando, etc.). Em vez de encerrar a analise como antes
        (exigindo clicar 'Iniciar' de novo), tenta reabrir a mesma fonte com
        backoff exponencial, sem bloquear a thread de UI (QTimer.singleShot
        em vez de time.sleep)."""
        if self.current_source is None or self.capture is None:
            self.stop_analysis()
            return
        self.capture.release()
        self.capture = None
        self._reconnecting = True
        self._reconnect_attempts = 0
        source_name = source_display_name(self.current_source)
        self.status.setText(f"Conexao perdida com {source_name}. Tentando reconectar...")
        self._append_session_log(
            {"event_type": "reconnect_started", "source": source_name, "attempt": 1}
        )
        self._reconnect_timer.start(0)

    def _attempt_reconnect_capture(self) -> None:
        if not self._reconnecting or self.current_source is None:
            return
        self._reconnect_attempts += 1
        source_name = source_display_name(self.current_source)
        candidate = open_capture_source(self.current_source)
        ok = candidate.isOpened()
        if ok:
            ok, _probe_frame = candidate.read()
        if ok:
            self.capture = candidate
            self._reconnecting = False
            self.status.setText(f"Reconectado a {source_name} | CPU/GPU: {self.device}")
            self._append_session_log(
                {"event_type": "reconnected", "source": source_name, "attempt": self._reconnect_attempts}
            )
            return
        candidate.release()
        self._append_session_log(
            {"event_type": "reconnect_failed", "source": source_name, "attempt": self._reconnect_attempts}
        )
        if self._reconnect_attempts >= self._reconnect_max_attempts:
            self._reconnecting = False
            self.status.setText(f"Falha ao reconectar a {source_name}. Analise parada.")
            self.stop_analysis()
            return
        backoff_seconds = min(
            self._reconnect_backoff_seconds * (2 ** (self._reconnect_attempts - 1)),
            self._reconnect_backoff_max_seconds,
        )
        self.status.setText(
            f"Reconectando a {source_name}... tentativa {self._reconnect_attempts + 1} em {backoff_seconds:.0f}s"
        )
        self._reconnect_timer.start(int(backoff_seconds * 1000))

    def _next_frame(self) -> None:
        if self.capture is None or self.service is None:
            return
        if self._reconnecting:
            # Reconexao assincrona em andamento (ver _attempt_reconnect_capture);
            # nao ha capture valido para ler agora, so aguardar o callback.
            return
        loop_started = time.perf_counter()
        ok, frame = self.capture.read()
        if not ok or frame is None:
            if isinstance(self.current_source, str) and "://" not in self.current_source:
                self.capture.set(cv2.CAP_PROP_POS_FRAMES, 0)
                return
            self._begin_reconnect()
            return

        self.frame_index += 1
        clean_frame = frame.copy()
        try:
            frame_result = self.service.process_frame(clean_frame, frame_id=self.frame_index)
        except Exception as exc:
            reason = str(exc).lower()
            if self.device != "cpu" and (
                "cuda" in reason or "out of memory" in reason or "cublas" in reason
            ):
                self.device = "cpu"
                self.device_reason = "gpu_indisponivel_fallback_cpu"
                fps = self.capture.get(cv2.CAP_PROP_FPS) if self.capture is not None else 30.0
                self.performance_profile = performance_profile_for_device(self.device, fps or 30.0)
                if self.service is not None:
                    self.service.set_device(
                        "cpu",
                        detector_stride_frames=int(self.performance_profile["detector_stride_frames"] or 1),
                        specialist_budget_per_frame=self.performance_profile["specialist_budget_per_frame"],
                    )
                self.timer.start(max(1, int(1000 / float(self.performance_profile["display_fps"] or 12.0))))
                self.status.setText("GPU indisponivel. Reprocessando em CPU.")
                try:
                    frame_result = self.service.process_frame(clean_frame, frame_id=self.frame_index)
                except Exception as retry_exc:
                    self._handle_inference_failure(retry_exc)
                    return
            else:
                self._handle_inference_failure(exc)
                return
        self.latencies.append(frame_result.latency_ms)
        resolved = frame_result.resolved_detections
        person_detections = frame_result.person_detections
        weak_child_detections = frame_result.weak_child_detections
        weak_child_promoted = frame_result.weak_child_promotions
        state = frame_result.state
        self.states[state.stable_state] += 1
        source_name = source_display_name(self.current_source or "pediatria_local")
        self._update_pipeline_status(state.stable_state, source_name)
        for promotion in frame_result.weak_promotions:
            self._append_session_log(
                {
                    "event_type": "weak_child_promoted",
                    "source": source_name,
                    "frame_index": self.frame_index,
                    "original_track_id": promotion.original_track_id,
                    "stable_track_id": promotion.stable_track_id,
                    "hits": promotion.hits,
                    "confidence": promotion.confidence,
                    "spatial_score": promotion.spatial_score,
                    "size_score": promotion.size_score,
                    "weak_child_confidence": self.weak_child_confidence,
                    "promoted_confidence": self.service.weak_child_promoter.promoted_confidence,
                }
            )
        for merge in frame_result.identity_merges:
            self._append_session_log(
                {
                    "event_type": "identity_merge",
                    "source": source_name,
                    "frame_index": self.frame_index,
                    "original_track_id": merge.original_track_id,
                    "stable_track_id": merge.stable_track_id,
                    "role_before": merge.role_before,
                    "role_after": merge.role_after,
                    "score": merge.score,
                    "palette_similarity": merge.palette_similarity,
                    "spatial_score": merge.spatial_score,
                    "size_score": merge.size_score,
                    "dominant_color": merge.dominant_color,
                }
            )
        if self.dataset_collector is not None:
            self.dataset_collector.collect(
                frame_index=self.frame_index,
                clean_frame=clean_frame,
                detections=resolved,
                state=state,
            )
        diagnostic_evidence_paths: dict[str, str] = {}
        diagnostic_evidence_errors: list[str] = []
        if self._should_log_frame_diagnostic():
            diagnostic_evidence_paths, diagnostic_evidence_errors = self._save_event_evidence(
                event_type="frame_diagnostic",
                source_name=source_name,
                state=state,
                child=None,
                detections=resolved,
                person_detections=person_detections,
                clean_frame=clean_frame,
                popup_emitido=False,
                suppression_reason=None,
            )
        self._append_frame_diagnostic_if_due(
            state=state,
            detections=resolved,
            person_detections=person_detections,
            weak_child_detections=weak_child_detections,
            weak_child_promotions=weak_child_promoted,
            source_name=source_name,
            evidence_paths=diagnostic_evidence_paths,
            evidence_errors=diagnostic_evidence_errors,
        )
        if state.stable_state in {"CHILD_ALONE", "CHILD_SEPARATED"}:
            self._resolved_frames_by_camera[source_name] = 0
            alert_child_track_ids = {
                item.child_track_id
                for item in state.children
                if item.state == state.stable_state
            }
            child = select_popup_evidence_detection(
                resolved,
                alert_child_track_ids,
            )
            if child is not None:
                popup_allowed = self.alert_latch.claim(
                    source_name,
                    state.stable_state,
                    child.track_id,
                )
            else:
                popup_allowed = False
            if child is not None and popup_allowed:
                evidence_frame = draw_popup_alert_bbox(
                    clean_frame.copy(),
                    child.bbox_xyxy,
                )
                evidence = self._frame_to_pixmap(evidence_frame)
                crop = self._crop_to_pixmap(clean_frame, child.bbox_xyxy)
                evidence_paths, evidence_errors = self._save_event_evidence(
                    event_type="popup_alert",
                    source_name=source_name,
                    state=state,
                    child=child,
                    detections=resolved,
                    person_detections=person_detections,
                    clean_frame=clean_frame,
                    popup_emitido=True,
                    suppression_reason=None,
                )
                self._append_event_log(
                    "popup_alert",
                    source_name=source_name,
                    state=state,
                    child=child,
                    detections=resolved,
                    person_detections=person_detections,
                    evidence_paths=evidence_paths,
                    evidence_errors=evidence_errors,
                    popup_emitido=True,
                )
                self._show_popup(
                    source_name,
                    child.track_id,
                    child.confidence,
                    state.stable_state,
                    evidence,
                    crop,
                )
            elif child is not None:
                self._append_suppressed_alert_if_due(
                    source_name=source_name,
                    child=child,
                    state=state,
                    detections=resolved,
                    person_detections=person_detections,
                    clean_frame=clean_frame,
                )
        else:
            self._resolved_frames_by_camera[source_name] += 1
            if self._resolved_frames_by_camera[source_name] >= 45:
                self.alert_latch.resolve_camera(source_name)

        render_started = time.perf_counter()
        self.runner._annotate(frame, resolved, state.stable_state, self.frame_index)
        self.video.setPixmap(self._frame_to_pixmap(frame))
        self.telemetry.render_frame_ms.add((time.perf_counter() - render_started) * 1000.0)

        self.telemetry.process_frame_ms.add(frame_result.latency_ms)
        self.telemetry.capture_loop_ms.add((time.perf_counter() - loop_started) * 1000.0)
        self.telemetry.sample_resources()



    def _update_pipeline_status(self, stable_state: str, source_name: str) -> None:
        labels = {
            "ACCOMPANIED": "CRIANCA ACOMPANHADA",
            "CHILD_ALONE": "CRIANCA DESACOMPANHADA",
            "CHILD_SEPARATED": "CRIANCA DESACOMPANHADA",
            "NO_CHILD": "SEM CRIANCA",
            "UNCERTAIN": "ANALISANDO",
        }
        text = labels.get(stable_state, "ANALISANDO")
        if hasattr(self, "pipeline_status"):
            self.pipeline_status.setText(f"{text}: {source_name}")
        if hasattr(self, "session_summary_label"):
            self.session_summary_label.setText(
                f"Sessao: {self.frame_index} frames | {self.popup_count} alertas"
            )

    def _handle_inference_failure(self, exc: Exception) -> None:
        self.status.setText(f"Falha na inferencia: {exc}")
        self._append_session_log(
            {
                "event_type": "inference_failed",
                "source": source_display_name(self.current_source or "pediatria_local"),
                "frame_index": self.frame_index,
                "resolved_device": self.device,
                "device_reason": self.device_reason,
                "error": str(exc),
            }
        )
        self.stop_analysis()

    @staticmethod
    def _frame_to_pixmap(frame: Any) -> QPixmap:
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        height, width, channels = rgb.shape
        image = QImage(
            rgb.data,
            width,
            height,
            channels * width,
            QImage.Format.Format_RGB888,
        )
        return QPixmap.fromImage(image.copy())

    @classmethod
    def _crop_to_pixmap(
        cls,
        frame: Any,
        bbox_xyxy: tuple[float, float, float, float],
    ) -> QPixmap | None:
        frame_h, frame_w = frame.shape[:2]
        x1, y1, x2, y2 = map(int, bbox_xyxy)
        pad_x = max(8, int((x2 - x1) * 0.12))
        pad_y = max(8, int((y2 - y1) * 0.08))
        x1, y1 = max(0, x1 - pad_x), max(0, y1 - pad_y)
        x2, y2 = min(frame_w, x2 + pad_x), min(frame_h, y2 + pad_y)
        if x2 <= x1 or y2 <= y1:
            return None
        crop = frame[y1:y2, x1:x2]
        return cls._frame_to_pixmap(crop) if crop.size else None

    def _should_log_frame_diagnostic(self) -> bool:
        interval = self.diagnostic_log_interval_frames
        return interval > 0 and self.frame_index % interval == 0

    def _event_status_final(
        self,
        state: Any,
        detections: list[PediatricDetection],
        person_detections: list[PediatricDetection],
    ) -> str:
        person_count = len(person_detections)
        if person_count == 0 and not detections:
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

    @staticmethod
    def _best_detection(
        detections: list[PediatricDetection],
        role: str | None = None,
    ) -> PediatricDetection | None:
        candidates = [item for item in detections if role is None or item.role == role]
        return max(candidates, key=lambda item: item.confidence, default=None)

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
                errors.append(f"{label}:missing_after_write")
                return False
            category = self._DISK_USAGE_CATEGORY_BY_LABEL.get(label)
            if category is not None:
                self.telemetry.disk_usage.record_file(category, path)
            return True
        except Exception as exc:
            errors.append(f"{label}:{type(exc).__name__}:{exc}")
            return False

    def _save_event_evidence(
        self,
        *,
        event_type: str,
        source_name: str,
        state: Any,
        child: PediatricDetection | None,
        detections: list[PediatricDetection],
        person_detections: list[PediatricDetection],
        clean_frame: Any,
        popup_emitido: bool,
        suppression_reason: str | None,
    ) -> tuple[dict[str, str], list[str]]:
        timestamp = datetime.now()
        crop_detection = choose_event_crop_detection(child, detections, person_detections)
        track_label = (
            f"track_{crop_detection.track_id}" if crop_detection is not None else "track_none"
        )
        stem = (
            f"{timestamp.strftime('%Y%m%d_%H%M%S_%f')[:-3]}"
            f"__{safe_name(source_name)}__{track_label}__{event_type}__{state.stable_state}"
        )
        errors: list[str] = []
        paths: dict[str, Path] = {
            "frame": self.evidence_dir / "frames" / f"{stem}_frame.jpg",
            "annotated": self.evidence_dir / "annotated" / f"{stem}_bbox.jpg",
            "metadata": self.evidence_dir / "metadata" / f"{stem}.json",
        }
        if self._write_image(paths["frame"], clean_frame, errors, "frame"):
            pass
        annotated = draw_event_evidence_frame(clean_frame, detections, crop_detection)
        self._write_image(paths["annotated"], annotated, errors, "annotated")
        if crop_detection is not None:
            bbox = normalize_bbox_for_image(clean_frame, crop_detection.bbox_xyxy)
            if bbox is None:
                errors.append("crop:invalid_bbox")
            else:
                crop = crop_frame(clean_frame, crop_detection.bbox_xyxy)
                crop_path = self.evidence_dir / "crops" / f"{stem}_crop.jpg"
                if self._write_image(crop_path, crop, errors, "crop"):
                    paths["crop"] = crop_path
        else:
            errors.append("crop:no_detection")
        evidence_paths = {key: str(value) for key, value in paths.items() if key != "metadata"}
        metadata = self._build_event_payload(
            event_type=event_type,
            source_name=source_name,
            state=state,
            child=child,
            detections=detections,
            person_detections=person_detections,
            evidence_paths=evidence_paths,
            evidence_errors=errors,
            popup_emitido=popup_emitido,
            suppression_reason=suppression_reason,
        )
        metadata["metadata_path"] = str(paths["metadata"])
        paths["metadata"].parent.mkdir(parents=True, exist_ok=True)
        metadata_text = json.dumps(metadata, indent=2, ensure_ascii=False)
        paths["metadata"].write_text(metadata_text, encoding="utf-8")
        self.telemetry.disk_usage.record_text("metadata_bytes", metadata_text)
        evidence_paths["metadata"] = str(paths["metadata"])
        return evidence_paths, errors

    def _append_event_log(
        self,
        event_type: str,
        *,
        source_name: str,
        state: Any,
        child: PediatricDetection | None,
        detections: list[PediatricDetection],
        person_detections: list[PediatricDetection] | None = None,
        evidence_paths: dict[str, str] | None = None,
        evidence_errors: list[str] | None = None,
        popup_emitido: bool = False,
        suppression_reason: str | None = None,
    ) -> None:
        payload = self._build_event_payload(
            event_type=event_type,
            source_name=source_name,
            state=state,
            child=child,
            detections=detections,
            person_detections=person_detections or [],
            evidence_paths=evidence_paths or {},
            evidence_errors=evidence_errors or [],
            popup_emitido=popup_emitido,
            suppression_reason=suppression_reason,
        )
        self._record_event_summary(payload)
        self._append_events_jsonl_line(payload)

    def _append_session_log(self, payload: dict[str, Any]) -> None:
        item = {
            "created_at": datetime.now().isoformat(timespec="milliseconds"),
            **payload,
        }
        self._append_events_jsonl_line(item)

    def _append_events_jsonl_line(self, payload: dict[str, Any]) -> None:
        self.events_log_path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(payload, ensure_ascii=False) + "\n"
        with self.events_log_path.open("a", encoding="utf-8") as handle:
            handle.write(line)
        self.telemetry.disk_usage.record_text("events_jsonl_bytes", line)

    def _append_suppressed_alert_if_due(
        self,
        *,
        source_name: str,
        child: PediatricDetection,
        state: Any,
        detections: list[PediatricDetection],
        person_detections: list[PediatricDetection],
        clean_frame: Any,
    ) -> None:
        key = (source_name, child.track_id, state.stable_state)
        now = time.monotonic()
        if now - self._last_suppressed_log.get(key, 0.0) < 10.0:
            return
        self._last_suppressed_log[key] = now
        suppression_reason = "active_episode_or_camera_track_cooldown"
        evidence_paths, evidence_errors = self._save_event_evidence(
            event_type="popup_suppressed",
            source_name=source_name,
            state=state,
            child=child,
            detections=detections,
            person_detections=person_detections,
            clean_frame=clean_frame,
            popup_emitido=False,
            suppression_reason=suppression_reason,
        )
        self._append_event_log(
            "popup_suppressed",
            source_name=source_name,
            state=state,
            child=child,
            detections=detections,
            person_detections=person_detections,
            evidence_paths=evidence_paths,
            evidence_errors=evidence_errors,
            popup_emitido=False,
            suppression_reason=suppression_reason,
        )

    def _append_frame_diagnostic_if_due(
        self,
        *,
        state: Any,
        detections: list[PediatricDetection],
        person_detections: list[PediatricDetection],
        weak_child_detections: list[PediatricDetection],
        weak_child_promotions: list[PediatricDetection],
        source_name: str,
        evidence_paths: dict[str, str] | None = None,
        evidence_errors: list[str] | None = None,
    ) -> None:
        if not self._should_log_frame_diagnostic():
            return
        payload = self._build_event_payload(
            event_type="frame_diagnostic",
            source_name=source_name,
            state=state,
            child=None,
            detections=detections,
            person_detections=person_detections,
            evidence_paths=evidence_paths or {},
            evidence_errors=evidence_errors or [],
            popup_emitido=False,
            suppression_reason=None,
        )
        payload["role_counts"] = role_counts(detections)
        payload["person_detector_top"] = [
            {
                "track_id": item.track_id,
                "confidence": round(item.confidence, 4),
                "bbox_xyxy": [round(value, 2) for value in item.bbox_xyxy],
            }
            for item in sorted(
                person_detections,
                key=lambda det: det.confidence,
                reverse=True,
            )[:8]
        ]
        payload["top_detections"] = [
            {
                "track_id": item.track_id,
                "role": item.role,
                "confidence": round(item.confidence, 4),
                "bbox_xyxy": [round(value, 2) for value in item.bbox_xyxy],
            }
            for item in sorted(detections, key=lambda det: det.confidence, reverse=True)[:8]
        ]
        payload["identity_merges"] = [
            {
                "frame_index": item.frame_index,
                "original_track_id": item.original_track_id,
                "stable_track_id": item.stable_track_id,
                "role_before": item.role_before,
                "role_after": item.role_after,
                "score": item.score,
                "palette_similarity": item.palette_similarity,
                "spatial_score": item.spatial_score,
                "size_score": item.size_score,
                "dominant_color": item.dominant_color,
            }
            for item in (self.service.identity_stabilizer.last_merges if self.service is not None else [])
        ]
        payload["weak_child_candidates"] = [
            {
                "track_id": item.track_id,
                "confidence": round(item.confidence, 4),
                "bbox_xyxy": [round(value, 2) for value in item.bbox_xyxy],
            }
            for item in sorted(
                weak_child_detections,
                key=lambda det: det.confidence,
                reverse=True,
            )[:8]
        ]
        payload["weak_child_promotions"] = [
            {
                "track_id": item.track_id,
                "confidence": round(item.confidence, 4),
                "bbox_xyxy": [round(value, 2) for value in item.bbox_xyxy],
            }
            for item in weak_child_promotions
        ]
        self._record_event_summary(payload)
        self.events_log_path.parent.mkdir(parents=True, exist_ok=True)
        with self.events_log_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False) + "\n")

    def _build_event_payload(
        self,
        *,
        event_type: str,
        source_name: str,
        state: Any,
        child: PediatricDetection | None,
        detections: list[PediatricDetection],
        person_detections: list[PediatricDetection],
        evidence_paths: dict[str, str],
        evidence_errors: list[str],
        popup_emitido: bool,
        suppression_reason: str | None,
    ) -> dict[str, Any]:
        children = [
            {
                "child_track_id": item.child_track_id,
                "state": item.state,
                "nearest_adult_track_id": item.nearest_adult_track_id,
                "nearest_adult_distance_ratio": item.nearest_adult_distance_ratio,
            }
            for item in state.children
        ]
        best_person = self._best_detection(person_detections)
        best_child = self._best_detection(detections, "child")
        best_detection = self._best_detection(detections)
        crop_detection = choose_event_crop_detection(child, detections, person_detections)
        status_final = self._event_status_final(state, detections, person_detections)
        specialist_classification = None
        detector_specialist_profile = None
        crop_pipeline = getattr(self, "crop_pipeline", None)
        if crop_pipeline is not None:
            detector_specialist_profile = dict(crop_pipeline.last_frame_meta)
            if crop_detection is not None:
                specialist_classification = crop_pipeline.last_trace.get(crop_detection.track_id)
        try:
            force_cpu_checked = bool(
                getattr(self, "force_cpu_checkbox", None)
                and self.force_cpu_checkbox.isChecked()
            )
        except RuntimeError:
            force_cpu_checked = False
        return {
            "event_type": event_type,
            "created_at": datetime.now().isoformat(timespec="milliseconds"),
            "timestamp": datetime.now().isoformat(timespec="milliseconds"),
            "session_id": self.session_id,
            "camera_id": source_name,
            "source": source_name,
            "frame_id": self.frame_index,
            "frame_index": self.frame_index,
            "popup_emitido": popup_emitido,
            "suppression_reason": suppression_reason,
            "status_final": status_final,
            "requested_device": self.requested_device,
            "resolved_device": self.device,
            "device_usado": self.device,
            "device_reason": self.device_reason,
            "force_cpu": force_cpu_checked,
            "modo_coleta": self.dataset_collector is not None,
            "model_path": str(self.model_path),
            "person_model_path": str(self.person_model_path),
            "raw_state": state.raw_state,
            "stable_state": state.stable_state,
            "reason": state.reason,
            "person_detector_count": len(person_detections),
            "person_confidence": best_person.confidence if best_person is not None else None,
            "person_bbox_xyxy": list(best_person.bbox_xyxy) if best_person is not None else None,
            "child_specialist_result": best_detection.role if best_detection is not None else None,
            "child_confidence": best_child.confidence if best_child is not None else None,
            "child_bbox_xyxy": list(best_child.bbox_xyxy) if best_child is not None else None,
            "crop_bbox_xyxy": list(crop_detection.bbox_xyxy) if crop_detection is not None else None,
            "specialist_classification": specialist_classification,
            "detector_specialist_profile": detector_specialist_profile,
            "selected_child": serialize_detection(child),
            "children": children,
            "detections": [serialize_detection(item) for item in detections],
            "evidence_paths": evidence_paths,
            "evidence_errors": evidence_errors,
        }

    def _record_event_summary(self, payload: dict[str, Any]) -> None:
        event_type = str(payload.get("event_type") or "unknown")
        status_final = str(payload.get("status_final") or "unknown")
        self.session_event_counts[event_type] += 1
        self.session_status_counts[status_final] += 1
        suppression_reason = payload.get("suppression_reason")
        if suppression_reason:
            self.session_suppression_counts[str(suppression_reason)] += 1
        if int(payload.get("person_detector_count") or 0) == 0:
            self.session_person_detector_zero += 1
        for error in payload.get("evidence_errors") or []:
            self.session_evidence_error_counts[str(error)] += 1

    def _show_popup(
        self,
        source_name: str,
        track_id: int,
        confidence: float,
        alert_state: str,
        evidence_pixmap: QPixmap,
        crop_pixmap: QPixmap | None,
    ) -> None:
        popup = PediatriaAlertPopup(
            self,
            camera_id=source_name,
            track_id=track_id,
            confidence=confidence,
            alert_state=alert_state,
            evidence_pixmap=evidence_pixmap,
            crop_pixmap=crop_pixmap,
        )
        popup.finished.connect(
            lambda _result, item=popup, camera=source_name: self._forget_popup(
                item,
                camera,
            )
        )
        self.active_popups.append(popup)
        popup.show()
        popup.raise_()
        popup.activateWindow()
        self.popup_count += 1
        self.voice.activate_companionship_alert(source_name, alert_state)

    def _forget_popup(self, popup: PediatriaAlertPopup, source_name: str) -> None:
        if popup in self.active_popups:
            self.active_popups.remove(popup)
        if not any(item.camera_id == source_name for item in self.active_popups):
            self.voice.deactivate_alert(source_name)

    def _write_report(self) -> None:
        self.report_path.parent.mkdir(parents=True, exist_ok=True)
        report = {
            "mode": "standalone_pediatria_local",
            "publishes_alerts": False,
            "source": source_display_name(self.current_source or "pediatria_local"),
            "requested_device": self.requested_device,
            "resolved_device": self.device,
            "device_reason": self.device_reason,
            "performance_profile": self.performance_profile,
            "evidence_dir": str(self.evidence_dir),
            "events_log": str(self.events_log_path),
            "frames_processed": self.frame_index,
            "popup_count": self.popup_count,
            "specialist_model": str(self.model_path),
            "person_crop_pipeline": self.person_crop_checkbox.isChecked(),
            "person_model": str(self.person_model_path),
            "person_confidence": self.person_confidence,
            "crop_cache_frames": self.crop_cache_frames,
            "weak_child_candidates": self.weak_child_checkbox.isChecked(),
            "weak_child_confidence": self.weak_child_confidence,
            "weak_child_promote_frames": self.weak_child_promote_frames,
            "stable_state_counts": dict(self.states),
            "average_latency_ms": (
                round(sum(self.latencies) / len(self.latencies), 2)
                if self.latencies
                else None
            ),
            "session_summary": self._build_session_summary(),
            "dataset_collection": (
                self.dataset_collector.summary()
                if self.dataset_collector is not None
                else None
            ),
        }
        self.report_path.write_text(
            json.dumps(report, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        self._write_session_summary_files(report["session_summary"])

    def _build_session_summary(self) -> dict[str, Any]:
        crop_pipeline = getattr(self, "crop_pipeline", None)
        return {
            "session_id": self.session_id,
            "source": source_display_name(self.current_source or "pediatria_local"),
            "evidence_dir": str(self.evidence_dir),
            "events_log": str(self.events_log_path),
            "total_frames_analisados": self.frame_index,
            "total_popups": self.popup_count,
            "total_suprimidos": self.session_event_counts.get("popup_suppressed", 0),
            "total_uncertain": self.session_status_counts.get("UNCERTAIN", 0),
            "total_no_child": self.session_status_counts.get("NO_CHILD", 0),
            "total_sem_pessoa": self.session_status_counts.get("SEM_PESSOA", 0),
            "total_person_detector_zero": self.session_person_detector_zero,
            "event_type_counts": dict(self.session_event_counts),
            "status_final_distribution": dict(self.session_status_counts),
            "suppression_reason_distribution": dict(self.session_suppression_counts),
            "evidence_error_distribution": dict(self.session_evidence_error_counts),
            "stable_state_counts": dict(self.states),
            "average_latency_ms": (
                round(sum(self.latencies) / len(self.latencies), 2)
                if self.latencies
                else None
            ),
            "specialist_classification_stats": (
                dict(crop_pipeline.stats) if crop_pipeline is not None else None
            ),
            "resources": self.telemetry.resources_snapshot(),
            "performance": self.telemetry.performance_snapshot(
                frames_processed=self.frame_index,
                extra=crop_pipeline.timing_snapshot() if crop_pipeline is not None else None,
            ),
            "disk_usage": self.telemetry.disk_usage_snapshot(),
        }

    def _write_session_summary_files(self, summary: dict[str, Any]) -> None:
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

    def closeEvent(self, event: Any) -> None:
        self.stop_analysis()
        self.voice.close()
        super().closeEvent(event)


def main() -> None:
    parser = argparse.ArgumentParser(description="Demo local do alerta pediatrico.")
    parser.add_argument("--source", default="")
    parser.add_argument("--model", default=str(DEFAULT_MODEL_PATH))
    parser.add_argument("--person-model", default=str(DEFAULT_PERSON_MODEL_PATH))
    parser.add_argument(
        "--report",
        default=str(DEFAULT_REPORT_PATH),
    )
    parser.add_argument("--conf", type=float, default=0.25)
    parser.add_argument(
        "--cooldown-seconds",
        type=float,
        default=120.0,
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--device",
        default="auto",
        help="Dispositivo de inferencia: auto, cpu, cuda:0 etc. Em auto usa GPU CUDA se existir; senao CPU.",
    )
    parser.add_argument(
        "--voice",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Ativa/desativa a voz local do alerta.",
    )
    parser.add_argument(
        "--voice-repeat-seconds",
        type=float,
        default=10.0,
        help="Intervalo entre alertas de voz ativos.",
    )
    parser.add_argument(
        "--diagnostic-log-interval-frames",
        type=int,
        default=60,
        help="Registra resumo de deteccoes a cada N frames; use 0 para desligar.",
    )
    parser.add_argument(
        "--force-cpu",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="Inicia com a opcao de forcar CPU marcada, mesmo se houver GPU.",
    )
    parser.add_argument(
        "--person-crop-pipeline",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Detecta pessoas com modelo generico e classifica cada crop com o especialista.",
    )
    parser.add_argument(
        "--person-conf",
        type=float,
        default=0.20,
        help="Limiar do detector generico de pessoa no fluxo por crop.",
    )
    parser.add_argument(
        "--crop-cache-frames",
        type=int,
        default=12,
        help="Frames para reaproveitar a decisao do especialista por track.",
    )
    parser.add_argument(
        "--weak-child-candidates",
        action=argparse.BooleanOptionalAction,
        default=True,
        help=(
            "Usa deteccoes child abaixo do limiar principal apenas quando "
            "persistirem por varios frames."
        ),
    )
    parser.add_argument(
        "--weak-child-conf",
        type=float,
        default=0.03,
        help="Limiar minimo para observar candidatos infantis fracos.",
    )
    parser.add_argument(
        "--weak-child-promote-frames",
        type=int,
        default=12,
        help="Quantidade de frames persistentes antes de promover child fraco.",
    )
    parser.add_argument(
        "--collect-dataset",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="Inicia com a coleta de crops/frames para revisao humana habilitada.",
    )
    parser.add_argument(
        "--dataset-output-dir",
        default="",
        help="Diretorio base para coleta de dataset; vazio usa pasta ao lado do report.",
    )
    parser.add_argument(
        "--dataset-sample-interval-frames",
        type=int,
        default=15,
        help="Salva crops a cada N frames quando a coleta estiver habilitada.",
    )
    parser.add_argument(
        "--dataset-max-crops-per-track",
        type=int,
        default=40,
        help="Limite de crops salvos por track para controlar disco/revisao.",
    )
    args = parser.parse_args()

    app = QApplication(sys.argv)
    demo = PediatriaPopupDemo(
        model=Path(args.model),
        person_model=Path(args.person_model),
        report=Path(args.report),
        confidence=args.conf,
        cooldown_seconds=args.cooldown_seconds,
        device=args.device,
        initial_source=args.source,
        voice_enabled=args.voice,
        voice_repeat_seconds=args.voice_repeat_seconds,
        diagnostic_log_interval_frames=args.diagnostic_log_interval_frames,
        force_cpu_default=args.force_cpu,
        person_crop_pipeline_default=args.person_crop_pipeline,
        crop_cache_frames=args.crop_cache_frames,
        person_confidence=args.person_conf,
        weak_child_candidates=args.weak_child_candidates,
        weak_child_confidence=args.weak_child_conf,
        weak_child_promote_frames=args.weak_child_promote_frames,
        collect_dataset_default=args.collect_dataset,
        dataset_output_dir=Path(args.dataset_output_dir) if args.dataset_output_dir else None,
        dataset_sample_interval_frames=args.dataset_sample_interval_frames,
        dataset_max_crops_per_track=args.dataset_max_crops_per_track,
    )
    demo.show()
    raise SystemExit(app.exec())


if __name__ == "__main__":
    main()











