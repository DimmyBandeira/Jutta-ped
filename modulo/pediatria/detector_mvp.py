from __future__ import annotations

import json
import time
from collections import Counter, deque
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable

import cv2

from .companionship import CompanionTrack, CompanionshipAnalyzer


@dataclass(frozen=True)
class PediatricDetection:
    bbox_xyxy: tuple[float, float, float, float]
    role: str
    confidence: float
    track_id: int


def bbox_iou(
    left: tuple[float, float, float, float],
    right: tuple[float, float, float, float],
) -> float:
    x1 = max(left[0], right[0])
    y1 = max(left[1], right[1])
    x2 = min(left[2], right[2])
    y2 = min(left[3], right[3])
    intersection = max(0.0, x2 - x1) * max(0.0, y2 - y1)
    if intersection <= 0:
        return 0.0
    left_area = max(0.0, left[2] - left[0]) * max(0.0, left[3] - left[1])
    right_area = max(0.0, right[2] - right[0]) * max(0.0, right[3] - right[1])
    union = left_area + right_area - intersection
    return intersection / union if union > 0 else 0.0


def resolve_role_conflicts(
    detections: list[PediatricDetection],
    *,
    iou_threshold: float = 0.60,
    ambiguity_margin: float = 0.15,
) -> list[PediatricDetection]:
    """Consolida bboxes sobrepostas e preserva conflito como uncertain."""

    remaining = sorted(detections, key=lambda item: item.confidence, reverse=True)
    resolved: list[PediatricDetection] = []
    while remaining:
        leader = remaining.pop(0)
        cluster = [leader]
        outside: list[PediatricDetection] = []
        for candidate in remaining:
            if bbox_iou(leader.bbox_xyxy, candidate.bbox_xyxy) >= iou_threshold:
                cluster.append(candidate)
            else:
                outside.append(candidate)
        remaining = outside

        runner_up = next(
            (item for item in cluster[1:] if item.role != leader.role),
            None,
        )
        if (
            runner_up is not None
            and leader.confidence - runner_up.confidence < ambiguity_margin
        ):
            resolved.append(
                PediatricDetection(
                    leader.bbox_xyxy,
                    "uncertain",
                    leader.confidence - runner_up.confidence,
                    leader.track_id,
                )
            )
        else:
            resolved.append(leader)
    return resolved


class PediatricRoleContextAdjuster:
    """Aplica memoria temporal e escala de cena antes da companhia."""

    def __init__(
        self,
        *,
        child_memory_min_confidence: float = 0.30,
        child_memory_min_hits: int = 2,
        child_memory_ttl_frames: int = 15,
        adult_reference_confidence: float = 0.60,
        same_depth_max_delta_px: float = 120.0,
        child_height_ratio: float = 0.72,
        child_area_ratio: float = 0.62,
        contextual_child_confidence: float = 0.62,
        scene_memory_ttl_frames: int = 450,
        scene_depth_max_delta_px: float = 35.0,
        scene_height_ratio_min: float = 0.70,
        scene_height_ratio_max: float = 1.35,
        scene_area_ratio_min: float = 0.55,
        scene_area_ratio_max: float = 1.90,
        scene_memory_max_samples: int = 600,
        enable_scene_scale_promotion: bool = False,
    ) -> None:
        self.child_memory_min_confidence = child_memory_min_confidence
        self.child_memory_min_hits = child_memory_min_hits
        self.child_memory_ttl_frames = child_memory_ttl_frames
        self.adult_reference_confidence = adult_reference_confidence
        self.same_depth_max_delta_px = same_depth_max_delta_px
        self.child_height_ratio = child_height_ratio
        self.child_area_ratio = child_area_ratio
        self.contextual_child_confidence = contextual_child_confidence
        self.scene_memory_ttl_frames = scene_memory_ttl_frames
        self.scene_depth_max_delta_px = scene_depth_max_delta_px
        self.scene_height_ratio_min = scene_height_ratio_min
        self.scene_height_ratio_max = scene_height_ratio_max
        self.scene_area_ratio_min = scene_area_ratio_min
        self.scene_area_ratio_max = scene_area_ratio_max
        self.enable_scene_scale_promotion = enable_scene_scale_promotion
        self._child_hits: dict[int, int] = {}
        self._last_child_frame: dict[int, int] = {}
        self._scene_trusted_child_tracks: set[int] = set()
        self._scene_child_samples: deque[tuple[int, float, float, float]] = deque(
            maxlen=scene_memory_max_samples
        )

    def adjust(
        self,
        detections: list[PediatricDetection],
        *,
        frame_index: int,
    ) -> list[PediatricDetection]:
        for item in detections:
            if (
                item.role == "child"
                and item.confidence >= self.child_memory_min_confidence
            ):
                self._child_hits[item.track_id] = self._child_hits.get(item.track_id, 0) + 1
                self._last_child_frame[item.track_id] = frame_index

        self._expire_scene_samples(frame_index)
        for item in detections:
            if (
                item.role == "child"
                and self._child_hits.get(item.track_id, 0) >= self.child_memory_min_hits
            ):
                self._scene_trusted_child_tracks.add(item.track_id)
                self._scene_child_samples.append(
                    (frame_index, self._foot_y(item), self._height(item), self._area(item))
                )

        adult_references = [
            item
            for item in detections
            if item.role == "adult" and item.confidence >= self.adult_reference_confidence
        ]
        adjusted: list[PediatricDetection] = []
        for item in detections:
            if item.role != "adult":
                adjusted.append(item)
                continue
            has_child_memory = self._has_child_memory(item.track_id, frame_index)
            has_relative_scale_evidence = self._is_contextual_child(
                item,
                adult_references,
            )
            has_scene_scale_evidence = (
                self.enable_scene_scale_promotion
                and self._matches_scene_child_scale(item)
            )
            if has_child_memory or has_relative_scale_evidence or has_scene_scale_evidence:
                promoted = PediatricDetection(
                    item.bbox_xyxy,
                    "child",
                    max(item.confidence, self.contextual_child_confidence),
                    item.track_id,
                )
                adjusted.append(promoted)
                self._child_hits[item.track_id] = max(
                    self._child_hits.get(item.track_id, 0),
                    self.child_memory_min_hits,
                )
                self._last_child_frame[item.track_id] = frame_index
                if has_relative_scale_evidence:
                    self._scene_trusted_child_tracks.add(item.track_id)
                if item.track_id in self._scene_trusted_child_tracks:
                    self._scene_child_samples.append(
                        (
                            frame_index,
                            self._foot_y(promoted),
                            self._height(promoted),
                            self._area(promoted),
                        )
                    )
            else:
                adjusted.append(item)
        return adjusted

    def _has_child_memory(self, track_id: int, frame_index: int) -> bool:
        if self._child_hits.get(track_id, 0) < self.child_memory_min_hits:
            return False
        last_seen = self._last_child_frame.get(track_id)
        if last_seen is None:
            return False
        return frame_index - last_seen <= self.child_memory_ttl_frames

    def _expire_scene_samples(self, frame_index: int) -> None:
        while (
            self._scene_child_samples
            and frame_index - self._scene_child_samples[0][0]
            > self.scene_memory_ttl_frames
        ):
            self._scene_child_samples.popleft()

    def _matches_scene_child_scale(self, item: PediatricDetection) -> bool:
        item_height = self._height(item)
        item_area = self._area(item)
        if item_height <= 0 or item_area <= 0:
            return False
        for _, sample_foot_y, sample_height, sample_area in self._scene_child_samples:
            if sample_height <= 0 or sample_area <= 0:
                continue
            if abs(self._foot_y(item) - sample_foot_y) > self.scene_depth_max_delta_px:
                continue
            height_ratio = item_height / sample_height
            area_ratio = item_area / sample_area
            if (
                self.scene_height_ratio_min <= height_ratio <= self.scene_height_ratio_max
                and self.scene_area_ratio_min <= area_ratio <= self.scene_area_ratio_max
            ):
                return True
        return False

    def _is_contextual_child(
        self,
        item: PediatricDetection,
        adult_references: list[PediatricDetection],
    ) -> bool:
        item_height = self._height(item)
        item_area = self._area(item)
        if item_height <= 0 or item_area <= 0:
            return False
        for adult in adult_references:
            if adult.track_id == item.track_id:
                continue
            adult_height = self._height(adult)
            adult_area = self._area(adult)
            if adult_height <= 0 or adult_area <= 0:
                continue
            if abs(self._foot_y(item) - self._foot_y(adult)) > self.same_depth_max_delta_px:
                continue
            if (
                item_height / adult_height <= self.child_height_ratio
                and item_area / adult_area <= self.child_area_ratio
            ):
                return True
        return False

    @staticmethod
    def _height(item: PediatricDetection) -> float:
        return item.bbox_xyxy[3] - item.bbox_xyxy[1]

    @staticmethod
    def _area(item: PediatricDetection) -> float:
        x1, y1, x2, y2 = item.bbox_xyxy
        return max(0.0, x2 - x1) * max(0.0, y2 - y1)

    @staticmethod
    def _foot_y(item: PediatricDetection) -> float:
        return item.bbox_xyxy[3]


class WeakChildCandidateTracker:
    """Promove candidatos fracos de crianca (conf abaixo do limiar principal) apos N frames."""

    def __init__(self, promote_frames: int = 12, ttl_frames: int = 6) -> None:
        self.promote_frames = promote_frames
        self.ttl_frames = ttl_frames
        self._hits: dict[int, int] = {}
        self._last_frame: dict[int, int] = {}

    def reset(self) -> None:
        self._hits.clear()
        self._last_frame.clear()

    def update(
        self,
        candidates: list[PediatricDetection],
        *,
        frame_index: int,
    ) -> set[int]:
        """Atualiza contagens e retorna track_ids prontos para promocao."""
        for tid, last in list(self._last_frame.items()):
            if frame_index - last > self.ttl_frames:
                self._hits.pop(tid, None)
                self._last_frame.pop(tid, None)
        for det in candidates:
            self._hits[det.track_id] = self._hits.get(det.track_id, 0) + 1
            self._last_frame[det.track_id] = frame_index
        return {tid for tid, hits in self._hits.items() if hits >= self.promote_frames}


class PediatricsDetectorMvpRunner:
    """Runner standalone para demonstracao local. Nao publica alertas.

    Suporta dois modos detectados automaticamente pelo conteudo do modelo:
      - Especialista: modelo treinado com classes 'adult' e 'child'.
      - Generico (nivel 1): modelo COCO com classe 'person'; todas as
        deteccoes entram como 'adult' e o PediatricRoleContextAdjuster
        com enable_scene_scale_promotion promove criancas por escala.
    Funciona com backends .pt (PyTorch) e OpenVINO sem alteracao.
    """

    # Cores por papel interno — visível ao operador só via cor, sem label de classe
    # BGR: child=laranja (atenção), adult=ciano (neutro), uncertain=amarelo (cautela)
    COLORS = {
        "child": (0, 140, 255),
        "adult": (180, 180, 0),
        "uncertain": (0, 215, 255),
    }

    # Mapeamento classe-do-modelo → papel pediatrico
    _SPECIALIST_ROLE_MAP: dict[str, str] = {"adult": "adult", "child": "child"}
    _GENERIC_ROLE_MAP: dict[str, str] = {"person": "adult"}

    def __init__(
        self,
        model_path: str | Path,
        *,
        model_loader: Callable[[str], Any] | None = None,
    ) -> None:
        path = Path(model_path)
        if not path.is_file() and not path.is_dir():
            raise FileNotFoundError(f"Modelo nao encontrado: {path}")
        self.model_path = path
        loader = model_loader or self._default_model_loader
        self.model = loader(str(path))
        self.names = {
            int(index): str(name).strip().lower()
            for index, name in dict(self.model.names).items()
        }
        available = set(self.names.values())
        if {"adult", "child"}.issubset(available):
            self.role_map: dict[str, str] = self._SPECIALIST_ROLE_MAP
        elif "person" in available:
            self.role_map = self._GENERIC_ROLE_MAP
        else:
            raise ValueError(
                f"Modelo precisa ter 'adult'+'child' (especialista) "
                f"ou 'person' (generico COCO): {self.names}"
            )

    @property
    def is_generic_detector(self) -> bool:
        """True quando o modelo e generico COCO (person→adult)."""
        return "person" in self.role_map

    @staticmethod
    def _default_model_loader(path: str) -> Any:
        from ultralytics import YOLO

        model_path = Path(path)
        if model_path.is_dir() or model_path.suffix.lower() == ".xml":
            return YOLO(path, task="detect")
        return YOLO(path)

    def run(
        self,
        source: str | Path,
        *,
        output_video: str | Path,
        report_path: str | Path,
        confidence: float = 0.25,
        max_frames: int = 0,
        device: str = "cpu",
    ) -> dict[str, Any]:
        capture = cv2.VideoCapture(str(source))
        if not capture.isOpened():
            raise OSError(f"Nao foi possivel abrir: {source}")

        output_video = Path(output_video)
        report_path = Path(report_path)
        output_video.parent.mkdir(parents=True, exist_ok=True)
        report_path.parent.mkdir(parents=True, exist_ok=True)
        fps = capture.get(cv2.CAP_PROP_FPS) or 30.0
        width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH))
        height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT))
        writer = cv2.VideoWriter(
            str(output_video),
            cv2.VideoWriter_fourcc(*"mp4v"),
            fps,
            (width, height),
        )
        if not writer.isOpened():
            capture.release()
            raise OSError(f"Nao foi possivel criar: {output_video}")

        analyzer = CompanionshipAnalyzer()
        role_adjuster = PediatricRoleContextAdjuster()
        raw_states: Counter[str] = Counter()
        stable_states: Counter[str] = Counter()
        role_counts: Counter[str] = Counter()
        frame_rows: list[dict[str, Any]] = []
        frame_index = 0
        started = time.perf_counter()
        try:
            while True:
                ok, frame = capture.read()
                if not ok or frame is None:
                    break
                if max_frames and frame_index >= max_frames:
                    break
                frame_index += 1
                inference_started = time.perf_counter()
                result = self.model.track(
                    frame,
                    persist=True,
                    conf=confidence,
                    device=device,
                    tracker="bytetrack.yaml",
                    verbose=False,
                )[0]
                latency_ms = (time.perf_counter() - inference_started) * 1000.0
                detections = self._parse_result(result)
                resolved = role_adjuster.adjust(
                    resolve_role_conflicts(detections),
                    frame_index=frame_index,
                )
                tracks = [
                    CompanionTrack(
                        item.track_id,
                        item.bbox_xyxy,
                        item.role,
                        item.confidence,
                    )
                    for item in resolved
                ]
                companionship = analyzer.observe(tracks)
                raw_states[companionship.raw_state] += 1
                stable_states[companionship.stable_state] += 1
                role_counts.update(item.role for item in resolved)
                self._annotate(frame, resolved, companionship.stable_state, frame_index)
                writer.write(frame)
                frame_rows.append(
                    {
                        "frame": frame_index,
                        "latency_ms": round(latency_ms, 2),
                        "raw_state": companionship.raw_state,
                        "stable_state": companionship.stable_state,
                        "detections": [asdict(item) for item in resolved],
                        "companionship_children": [
                            asdict(item) for item in companionship.children
                        ],
                    }
                )
        finally:
            capture.release()
            writer.release()

        report = {
            "mode": "standalone_pediatria_local",
            "publishes_alerts": False,
            "source": str(source),
            "model": str(self.model_path),
            "model_classes": self.names,
            "confidence_threshold": confidence,
            "frames_processed": frame_index,
            "elapsed_seconds": round(time.perf_counter() - started, 2),
            "average_latency_ms": round(
                sum(row["latency_ms"] for row in frame_rows) / len(frame_rows), 2
            )
            if frame_rows
            else None,
            "role_counts": dict(role_counts),
            "raw_state_counts": dict(raw_states),
            "stable_state_counts": dict(stable_states),
            "frames": frame_rows,
        }
        report_path.write_text(
            json.dumps(report, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        return report

    def _parse_result(self, result: Any) -> list[PediatricDetection]:
        boxes = result.boxes
        if boxes is None:
            return []
        parsed: list[PediatricDetection] = []
        for index in range(len(boxes)):
            source_class = self.names.get(int(boxes.cls[index]), "")
            role = self.role_map.get(source_class)
            if role is None:
                continue
            track_id = (
                int(boxes.id[index])
                if boxes.id is not None
                else index + 1
            )
            bbox = tuple(float(value) for value in boxes.xyxy[index].cpu().numpy())
            parsed.append(
                PediatricDetection(
                    bbox,
                    role,
                    float(boxes.conf[index]),
                    track_id,
                )
            )
        return parsed

    _THINKING_FRAMES = ["analisando", "analisando.", "analisando..", "analisando..."]
    _STATE_LABELS = {
        "UNCERTAIN": "ANALISANDO",
        "NO_CHILD": "MONITORANDO",
        "ACCOMPANIED": "ACOMPANHAMENTO OK",
        "CHILD_SEPARATED": "ATENCAO: AFASTAMENTO",
        "CHILD_ALONE": "ATENCAO: POSSIVEL DESACOMPANHAMENTO",
    }

    def _annotate(
        self,
        frame: Any,
        detections: list[PediatricDetection],
        stable_state: str,
        frame_index: int = 0,
    ) -> None:
        operator_state = self._STATE_LABELS.get(stable_state, "ANALISANDO")
        cv2.putText(
            frame,
            f"PEDIATRIA LOCAL | {operator_state}",
            (16, 28),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.75,
            (255, 255, 255),
            2,
        )
        thinking = self._THINKING_FRAMES[(frame_index // 6) % 4]
        for item in detections:
            x1, y1, x2, y2 = map(int, item.bbox_xyxy)
            color = self.COLORS[item.role]
            cv2.rectangle(frame, (x1, y1), (x2, y2), color, 2)
            cv2.putText(
                frame,
                f"ID {item.track_id} | {thinking}",
                (x1, max(18, y1 - 5)),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.60,
                color,
                2,
            )
