from __future__ import annotations

import argparse
import csv
import colorsys
import json
import os
import sys
import threading
import time
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlsplit, urlunsplit

import cv2
from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QImage, QPixmap
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

ROOT = Path(__file__).resolve().parents[1]
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
from modulo.pediatria.detector_mvp import (
    PediatricDetection,
    PediatricRoleContextAdjuster,
    PediatricsDetectorMvpRunner,
    bbox_iou,
    resolve_role_conflicts,
)
from modulo.pediatria.mvp_popup import AlertEventLatch, PediatriaAlertPopup
from modulo.pediatria.mvp_voice import MvpVoiceAnnouncer
from src.jutta_ped.service import launcher as service_launcher
from src.jutta_ped.service.pediatria_service import (
    PediatriaService,
    PediatriaServiceConfig,
    cpu_economical_overrides,
)
from src.jutta_ped.service.telemetry import RunningStat, SessionTelemetry

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


def crop_frame(
    frame: Any,
    bbox_xyxy: tuple[float, float, float, float],
    *,
    pad_ratio_x: float = 0.12,
    pad_ratio_y: float = 0.08,
) -> Any | None:
    frame_h, frame_w = frame.shape[:2]
    x1, y1, x2, y2 = map(int, bbox_xyxy)
    pad_x = max(8, int((x2 - x1) * pad_ratio_x))
    pad_y = max(8, int((y2 - y1) * pad_ratio_y))
    x1, y1 = max(0, x1 - pad_x), max(0, y1 - pad_y)
    x2, y2 = min(frame_w, x2 + pad_x), min(frame_h, y2 + pad_y)
    if x2 <= x1 or y2 <= y1:
        return None
    crop = frame[y1:y2, x1:x2]
    return crop.copy() if crop.size else None


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


@dataclass
class PediatricIdentityMemory:
    stable_id: int
    last_track_id: int
    last_frame: int
    bbox_xyxy: tuple[float, float, float, float]
    palette: list[float]
    dominant_color: str
    hits: int = 1


@dataclass(frozen=True)
class PediatricIdentityMerge:
    frame_index: int
    original_track_id: int
    stable_track_id: int
    role_before: str
    role_after: str
    score: float
    palette_similarity: float
    spatial_score: float
    size_score: float
    dominant_color: str


class PediatricIdentityStabilizer:
    """Reidentificacao leve por posicao, tamanho e paleta do torso."""

    def __init__(
        self,
        *,
        ttl_frames: int = 90,
        match_threshold: float = 0.66,
        promote_uncertain_threshold: float = 0.74,
    ) -> None:
        self.ttl_frames = ttl_frames
        self.match_threshold = match_threshold
        self.promote_uncertain_threshold = promote_uncertain_threshold
        self._memories: dict[int, PediatricIdentityMemory] = {}
        self._track_to_stable: dict[int, int] = {}
        self._logged_merge_keys: set[tuple[int, int, str, str]] = set()
        self.last_merges: list[PediatricIdentityMerge] = []

    def reset(self) -> None:
        self._memories.clear()
        self._track_to_stable.clear()
        self._logged_merge_keys.clear()
        self.last_merges = []

    def stabilize(
        self,
        detections: list[PediatricDetection],
        frame: Any,
        *,
        frame_index: int,
    ) -> list[PediatricDetection]:
        self.last_merges = []
        self._expire(frame_index)
        stabilized: list[PediatricDetection] = []
        used_stable_ids: set[int] = set()

        for detection in detections:
            if detection.role not in {"child", "uncertain"}:
                stabilized.append(detection)
                continue
            palette, dominant_color = self._torso_palette(frame, detection.bbox_xyxy)
            if not palette:
                stabilized.append(detection)
                continue

            best = self._best_match(
                detection,
                palette,
                frame_index=frame_index,
                used_stable_ids=used_stable_ids,
            )
            stable_id = detection.track_id
            role_after = detection.role
            confidence = detection.confidence
            if best is not None:
                memory, score, palette_similarity, spatial_score, size_score = best
                if score >= self.match_threshold:
                    stable_id = memory.stable_id
                    used_stable_ids.add(stable_id)
                    if (
                        detection.role == "uncertain"
                        and score >= self.promote_uncertain_threshold
                    ):
                        role_after = "child"
                        confidence = max(confidence, 0.62)
                    merge_key = (
                        detection.track_id,
                        stable_id,
                        detection.role,
                        role_after,
                    )
                    if (
                        (stable_id != detection.track_id or role_after != detection.role)
                        and merge_key not in self._logged_merge_keys
                    ):
                        self._logged_merge_keys.add(merge_key)
                        self.last_merges.append(
                            PediatricIdentityMerge(
                                frame_index=frame_index,
                                original_track_id=detection.track_id,
                                stable_track_id=stable_id,
                                role_before=detection.role,
                                role_after=role_after,
                                score=round(score, 4),
                                palette_similarity=round(palette_similarity, 4),
                                spatial_score=round(spatial_score, 4),
                                size_score=round(size_score, 4),
                                dominant_color=dominant_color,
                            )
                        )

            stabilized_detection = PediatricDetection(
                detection.bbox_xyxy,
                role_after,
                confidence,
                stable_id,
            )
            stabilized.append(stabilized_detection)
            if role_after == "child" and confidence >= 0.35:
                self._remember(
                    stable_id=stable_id,
                    current_track_id=detection.track_id,
                    bbox_xyxy=detection.bbox_xyxy,
                    palette=palette,
                    dominant_color=dominant_color,
                    frame_index=frame_index,
                )
        return stabilized

    def _remember(
        self,
        *,
        stable_id: int,
        current_track_id: int,
        bbox_xyxy: tuple[float, float, float, float],
        palette: list[float],
        dominant_color: str,
        frame_index: int,
    ) -> None:
        previous = self._memories.get(stable_id)
        hits = previous.hits + 1 if previous is not None else 1
        self._memories[stable_id] = PediatricIdentityMemory(
            stable_id=stable_id,
            last_track_id=current_track_id,
            last_frame=frame_index,
            bbox_xyxy=bbox_xyxy,
            palette=palette,
            dominant_color=dominant_color,
            hits=hits,
        )
        self._track_to_stable[current_track_id] = stable_id

    def _best_match(
        self,
        detection: PediatricDetection,
        palette: list[float],
        *,
        frame_index: int,
        used_stable_ids: set[int],
    ) -> tuple[PediatricIdentityMemory, float, float, float, float] | None:
        mapped_id = self._track_to_stable.get(detection.track_id)
        if mapped_id is not None and mapped_id in self._memories:
            memory = self._memories[mapped_id]
            if frame_index - memory.last_frame <= self.ttl_frames:
                score, palette_similarity, spatial_score, size_score = self._score(
                    detection,
                    memory,
                    palette,
                )
                return memory, max(score, 0.95), palette_similarity, spatial_score, size_score

        best: tuple[PediatricIdentityMemory, float, float, float, float] | None = None
        for memory in self._memories.values():
            if memory.stable_id in used_stable_ids:
                continue
            if frame_index - memory.last_frame > self.ttl_frames:
                continue
            score, palette_similarity, spatial_score, size_score = self._score(
                detection,
                memory,
                palette,
            )
            if best is None or score > best[1]:
                best = memory, score, palette_similarity, spatial_score, size_score
        return best

    def _score(
        self,
        detection: PediatricDetection,
        memory: PediatricIdentityMemory,
        palette: list[float],
    ) -> tuple[float, float, float, float]:
        spatial_score = self._spatial_score(detection.bbox_xyxy, memory.bbox_xyxy)
        size_score = self._size_score(detection.bbox_xyxy, memory.bbox_xyxy)
        palette_similarity = self._cosine_similarity(palette, memory.palette)
        score = (
            0.45 * spatial_score
            + 0.35 * palette_similarity
            + 0.20 * size_score
        )
        return score, palette_similarity, spatial_score, size_score

    def _expire(self, frame_index: int) -> None:
        expired = [
            stable_id
            for stable_id, memory in self._memories.items()
            if frame_index - memory.last_frame > self.ttl_frames
        ]
        for stable_id in expired:
            memory = self._memories.pop(stable_id, None)
            if memory is not None:
                self._track_to_stable.pop(memory.last_track_id, None)

    @staticmethod
    def _torso_palette(
        frame: Any,
        bbox_xyxy: tuple[float, float, float, float],
    ) -> tuple[list[float], str]:
        crop = crop_frame(frame, bbox_xyxy, pad_ratio_x=0.0, pad_ratio_y=0.0)
        if crop is None:
            return [], "unknown"
        height, width = crop.shape[:2]
        if height < 24 or width < 16:
            return [], "unknown"
        y1 = max(0, int(height * 0.18))
        y2 = min(height, int(height * 0.62))
        x1 = max(0, int(width * 0.08))
        x2 = min(width, int(width * 0.92))
        torso = crop[y1:y2, x1:x2]
        if torso.size == 0:
            return [], "unknown"

        if all(hasattr(cv2, name) for name in ("cvtColor", "inRange", "calcHist")):
            hsv = cv2.cvtColor(torso, cv2.COLOR_BGR2HSV)
            mask = cv2.inRange(hsv, (0, 25, 35), (179, 255, 255))
            hist = cv2.calcHist([hsv], [0], mask, [12], [0, 180])
            total = float(hist.sum())
            if total <= 0:
                return [], "unknown"
            values = [float(item[0] / total) for item in hist]
        else:
            values = PediatricIdentityStabilizer._fallback_hue_histogram(torso)
            if not values:
                return [], "unknown"
        dominant_index = max(range(len(values)), key=lambda index: values[index])
        return values, PediatricIdentityStabilizer._dominant_color_name(dominant_index)

    @staticmethod
    def _fallback_hue_histogram(torso: Any) -> list[float]:
        bins = [0.0 for _ in range(12)]
        pixels = torso.reshape(-1, 3)
        step = max(1, len(pixels) // 2500)
        count = 0
        for pixel in pixels[::step]:
            blue, green, red = [float(value) / 255.0 for value in pixel[:3]]
            hue, saturation, value = colorsys.rgb_to_hsv(red, green, blue)
            if saturation < 0.12 or value < 0.14:
                continue
            index = min(11, int(hue * 12.0))
            bins[index] += 1.0
            count += 1
        if count <= 0:
            return []
        return [value / count for value in bins]

    @staticmethod
    def _dominant_color_name(bin_index: int) -> str:
        names = [
            "red",
            "orange",
            "yellow",
            "yellow_green",
            "green",
            "cyan_green",
            "cyan",
            "blue",
            "blue",
            "purple",
            "magenta",
            "red",
        ]
        return names[max(0, min(bin_index, len(names) - 1))]

    @staticmethod
    def _center_and_height(
        bbox_xyxy: tuple[float, float, float, float],
    ) -> tuple[float, float, float]:
        x1, y1, x2, y2 = bbox_xyxy
        return (x1 + x2) / 2.0, y2, max(1.0, y2 - y1)

    @classmethod
    def _spatial_score(
        cls,
        current: tuple[float, float, float, float],
        previous: tuple[float, float, float, float],
    ) -> float:
        cx, cy, height = cls._center_and_height(current)
        px, py, previous_height = cls._center_and_height(previous)
        scale = max(height, previous_height, 1.0)
        distance_ratio = ((cx - px) ** 2 + (cy - py) ** 2) ** 0.5 / scale
        return max(0.0, 1.0 - min(distance_ratio / 2.2, 1.0))

    @staticmethod
    def _size_score(
        current: tuple[float, float, float, float],
        previous: tuple[float, float, float, float],
    ) -> float:
        current_height = max(1.0, current[3] - current[1])
        previous_height = max(1.0, previous[3] - previous[1])
        ratio = min(current_height, previous_height) / max(current_height, previous_height)
        return max(0.0, min(ratio, 1.0))

    @staticmethod
    def _cosine_similarity(left: list[float], right: list[float]) -> float:
        if not left or not right or len(left) != len(right):
            return 0.0
        numerator = sum(a * b for a, b in zip(left, right))
        left_norm = sum(a * a for a in left) ** 0.5
        right_norm = sum(b * b for b in right) ** 0.5
        if left_norm <= 0 or right_norm <= 0:
            return 0.0
        return max(0.0, min(numerator / (left_norm * right_norm), 1.0))


@dataclass
class WeakChildMemory:
    stable_id: int
    last_frame: int
    bbox_xyxy: tuple[float, float, float, float]
    hits: int = 1
    best_confidence: float = 0.0
    promoted_logged: bool = False


@dataclass(frozen=True)
class WeakChildPromotion:
    frame_index: int
    original_track_id: int
    stable_track_id: int
    hits: int
    confidence: float
    spatial_score: float
    size_score: float


class WeakChildCandidatePromoter:
    """Promove crianca fraca somente com persistencia espacial conservadora."""

    def __init__(
        self,
        *,
        min_hits: int = 12,
        ttl_frames: int = 45,
        match_threshold: float = 0.68,
        promoted_confidence: float = 0.62,
        max_adult_iou: float = 0.55,
        max_adult_distance_ratio: float = 2.25,
    ) -> None:
        self.min_hits = min_hits
        self.ttl_frames = ttl_frames
        self.match_threshold = match_threshold
        self.promoted_confidence = promoted_confidence
        self.max_adult_iou = max_adult_iou
        self.max_adult_distance_ratio = max_adult_distance_ratio
        self._next_stable_id = 200000
        self._memories: dict[int, WeakChildMemory] = {}
        self.last_promotions: list[WeakChildPromotion] = []

    def reset(self) -> None:
        self._next_stable_id = 200000
        self._memories.clear()
        self.last_promotions = []

    def promote(
        self,
        weak_children: list[PediatricDetection],
        normal_detections: list[PediatricDetection],
        *,
        frame_index: int,
    ) -> list[PediatricDetection]:
        self.last_promotions = []
        self._expire(frame_index)
        promoted: list[PediatricDetection] = []
        adults = [item for item in normal_detections if item.role == "adult"]
        used_memories: set[int] = set()
        for child in weak_children:
            if any(self._is_blocked_by_adult(child, adult) for adult in adults):
                continue
            memory, score, spatial_score, size_score = self._match(
                child,
                frame_index=frame_index,
                used_memories=used_memories,
            )
            if memory is None or score < self.match_threshold:
                memory = self._create_memory(child, frame_index)
                used_memories.add(memory.stable_id)
                score, spatial_score, size_score = 1.0, 1.0, 1.0
            else:
                used_memories.add(memory.stable_id)
                memory.last_frame = frame_index
                memory.bbox_xyxy = child.bbox_xyxy
                memory.hits += 1
                memory.best_confidence = max(memory.best_confidence, child.confidence)

            if memory.hits >= self.min_hits:
                confidence = max(
                    self.promoted_confidence,
                    min(0.74, memory.best_confidence + 0.50),
                )
                promoted.append(
                    PediatricDetection(
                        child.bbox_xyxy,
                        "child",
                        confidence,
                        memory.stable_id,
                    )
                )
                if not memory.promoted_logged:
                    memory.promoted_logged = True
                    self.last_promotions.append(
                        WeakChildPromotion(
                            frame_index=frame_index,
                            original_track_id=child.track_id,
                            stable_track_id=memory.stable_id,
                            hits=memory.hits,
                            confidence=round(child.confidence, 4),
                            spatial_score=round(spatial_score, 4),
                            size_score=round(size_score, 4),
                        )
                    )
        return promoted

    def _create_memory(
        self,
        child: PediatricDetection,
        frame_index: int,
    ) -> WeakChildMemory:
        stable_id = self._next_stable_id
        self._next_stable_id += 1
        memory = WeakChildMemory(
            stable_id=stable_id,
            last_frame=frame_index,
            bbox_xyxy=child.bbox_xyxy,
            best_confidence=child.confidence,
        )
        self._memories[stable_id] = memory
        return memory

    def _match(
        self,
        child: PediatricDetection,
        *,
        frame_index: int,
        used_memories: set[int],
    ) -> tuple[WeakChildMemory | None, float, float, float]:
        best: tuple[WeakChildMemory | None, float, float, float] = (None, 0.0, 0.0, 0.0)
        for memory in self._memories.values():
            if memory.stable_id in used_memories:
                continue
            if frame_index - memory.last_frame > self.ttl_frames:
                continue
            spatial_score = PediatricIdentityStabilizer._spatial_score(
                child.bbox_xyxy,
                memory.bbox_xyxy,
            )
            size_score = PediatricIdentityStabilizer._size_score(
                child.bbox_xyxy,
                memory.bbox_xyxy,
            )
            score = (0.70 * spatial_score) + (0.30 * size_score)
            if score > best[1]:
                best = memory, score, spatial_score, size_score
        return best

    def _expire(self, frame_index: int) -> None:
        expired = [
            stable_id
            for stable_id, memory in self._memories.items()
            if frame_index - memory.last_frame > self.ttl_frames
        ]
        for stable_id in expired:
            self._memories.pop(stable_id, None)

    def _is_blocked_by_adult(
        self,
        child: PediatricDetection,
        adult: PediatricDetection,
    ) -> bool:
        if bbox_iou(child.bbox_xyxy, adult.bbox_xyxy) > self.max_adult_iou:
            return True
        child_cx, child_cy, child_height = PediatricIdentityStabilizer._center_and_height(
            child.bbox_xyxy
        )
        adult_cx, adult_cy, adult_height = PediatricIdentityStabilizer._center_and_height(
            adult.bbox_xyxy
        )
        scale = max(child_height, adult_height, 1.0)
        distance_ratio = (
            ((child_cx - adult_cx) ** 2 + (child_cy - adult_cy) ** 2) ** 0.5
            / scale
        )
        return distance_ratio <= self.max_adult_distance_ratio


@dataclass
class TrackClassificationState:
    """Estado persistente do especialista para um track de pessoa.

    Substitui o antigo cache "por frame" (`CropRoleCacheEntry`): alem de
    bbox/role/confidence, guarda sinais de estabilidade para decidir quando
    vale a pena gastar outra chamada ao especialista.
    """

    track_id: int
    bbox_xyxy: tuple[float, float, float, float]
    role: str
    confidence: float
    last_classified_frame: int
    last_seen_frame: int
    stable_hits: int = 0
    pending_refresh: bool = False


class PersonCropSpecialistPipeline:
    """Detecta pessoas no frame e deixa o especialista decidir no crop.

    O especialista roda POR TRACK, nao por frame: cada pessoa rastreada tem
    um `TrackClassificationState` reaproveitado enquanto nada relevante
    mudou (`_gate`). Isso preserva o ganho de acuracia do crop (o
    especialista continua sendo a unica fonte do papel adult/child) mas
    reduz bastante o numero de chamadas em CPU, que e o custo dominante do
    pipeline.
    """

    # TTL base (frames) para reclassificar um track ja confirmado.
    BASE_TTL_FRAMES = 12
    # Tracks 'child'/'uncertain' sao alert-relevantes: TTL bem mais curto,
    # mesmo que o TTL base configurado seja maior.
    CHILD_OR_WEAK_TTL_FRAMES = 4
    # Adultos confirmados repetidamente podem esperar bem mais entre
    # reclassificacoes: sao o caso mais comum e o de menor risco.
    STABLE_ADULT_TTL_FRAMES = 48
    STABLE_ADULT_HITS_REQUIRED = 3
    STABLE_ADULT_MIN_CONFIDENCE = 0.50
    # Geometria: abaixo disso a bbox mudou demais para confiar no cache.
    MIN_IOU_FOR_REUSE = 0.35
    # Fora dessa faixa de variacao de area (crop ficou muito maior/menor),
    # forca reclassificacao mesmo com IoU aceitavel.
    MAX_AREA_RATIO_CHANGE = 1.6
    # Track ausente por mais que isso e tratado como "reapareceu".
    REAPPEAR_GAP_FRAMES = 5
    # Abaixo disso nao vale gastar o especialista: crop pequeno demais para
    # ser confiavel.
    MIN_CROP_SIDE_PX = 24
    # Entradas de cache sem nenhuma atividade por muito tempo sao podadas
    # para o dicionario nao crescer sem limite numa sessao longa.
    STALE_PRUNE_FRAMES = 300

    # Ordem de prioridade quando o numero de tracks elegiveis para o
    # especialista excede o orcamento por frame (menor valor = mais urgente).
    _PRIORITY_RANK = {
        "weak_previous_classification": 1,
        "new_track": 2,
        "track_reappeared": 3,
        "size_changed": 4,
        "bbox_shifted": 5,
        "ttl_expired": 6,
    }

    def __init__(
        self,
        *,
        person_model_path: Path,
        specialist_model: Any,
        specialist_names: dict[int, str],
    ) -> None:
        self.person_model_path = person_model_path
        self.person_model = self._load_detect_model(person_model_path)
        self.specialist_model = specialist_model
        self.specialist_names = specialist_names
        self._cache: dict[int, TrackClassificationState] = {}
        # Ultima leitura real do detector de pessoa, reaproveitada nos
        # frames em que o detector e pulado (detector_stride_frames > 1).
        self._last_persons: list[PediatricDetection] | None = None
        self._last_detector_frame: int | None = None
        # Rastro da ultima chamada: track_id -> metadados de decisao, usado
        # para enriquecer a evidencia (fonte da classificacao, idade do
        # cache, motivo do refresh, prioridade).
        self.last_trace: dict[int, dict[str, Any]] = {}
        # Metadados de nivel-de-frame (nao por track): se o detector rodou,
        # quantos tracks elegiveis couberam no orcamento do especialista etc.
        self.last_frame_meta: dict[str, Any] = {}
        self.stats: Counter[str] = Counter()
        # Tempos agregados (O(1) de memoria) do detector de pessoa e do
        # especialista, para telemetria de performance por sessao.
        self.detector_ms = RunningStat()
        self.specialist_ms = RunningStat()

    @staticmethod
    def _load_detect_model(model_path: Path) -> Any:
        from ultralytics import YOLO

        if model_path.is_dir() or model_path.suffix.lower() == ".xml":
            return YOLO(str(model_path), task="detect")
        return YOLO(str(model_path))

    def reset(self) -> None:
        self._cache.clear()
        self._last_persons = None
        self._last_detector_frame = None
        self.last_trace = {}
        self.last_frame_meta = {}
        self.stats = Counter()
        self.detector_ms = RunningStat()
        self.specialist_ms = RunningStat()

    def timing_snapshot(self) -> dict[str, float | None]:
        return {
            **self.detector_ms.snapshot(avg_key="avg_person_detector_ms", peak_key="peak_person_detector_ms"),
            **self.specialist_ms.snapshot(avg_key="avg_specialist_ms", peak_key="peak_specialist_ms"),
        }

    def detect_and_classify(
        self,
        frame: Any,
        *,
        frame_index: int,
        device: str,
        person_confidence: float,
        specialist_accept_confidence: float,
        cache_ttl_frames: int,
        detector_stride_frames: int = 1,
        specialist_budget_per_frame: int | None = None,
        priority_track_ids: set[int] | None = None,
    ) -> tuple[list[PediatricDetection], list[PediatricDetection]]:
        persons, detector_reused = self._run_or_reuse_detector(
            frame,
            frame_index=frame_index,
            device=device,
            person_confidence=person_confidence,
            detector_stride_frames=max(1, int(detector_stride_frames)),
        )
        self._prune_stale(frame_index)
        self.last_trace = {}

        # Fase 1: gate por track. Separa quem so reusa/pula (barato, decide
        # na hora) de quem e elegivel para o especialista ("classify").
        immediate: dict[int, tuple[str, float, TrackClassificationState | None, str | None, int | None]] = {}
        eligible: list[tuple[PediatricDetection, TrackClassificationState | None, str | None]] = []
        for person in persons:
            state = self._cache.get(person.track_id)
            decision, reason = self._gate(person, state, frame_index, cache_ttl_frames)
            if decision == "classify":
                eligible.append((person, state, reason))
                continue
            if decision == "reuse":
                assert state is not None  # _gate so retorna "reuse" com estado existente
                role, confidence = state.role, state.confidence
                cache_age = frame_index - state.last_classified_frame
                state.last_seen_frame = frame_index
                source = "cache_reuse"
            else:  # "skip_cheap": crop pequeno demais, nao vale a pena classificar
                if state is not None:
                    role, confidence = state.role, state.confidence
                    state.last_seen_frame = frame_index
                    cache_age = frame_index - state.last_classified_frame
                else:
                    role, confidence = "uncertain", 0.0
                    cache_age = None
                source = "skipped_small_crop"
            immediate[person.track_id] = (source, confidence, state, reason, cache_age)

        # Fase 2: orcamento. Se cabe todo mundo, roda todo mundo; senao,
        # prioriza e adia o resto (mantem o ultimo estado conhecido).
        if specialist_budget_per_frame is not None and len(eligible) > specialist_budget_per_frame:
            eligible.sort(key=lambda item: self._priority_score(item[0], item[2], priority_track_ids))
            run_now = eligible[: specialist_budget_per_frame]
            deferred = eligible[specialist_budget_per_frame:]
        else:
            run_now = eligible
            deferred = []

        # Fase 3: aplica o especialista so em run_now; deferred reusa o que
        # ja existia (ou fica "uncertain" se for track novo sem cache ainda).
        role_by_track: dict[int, tuple[str, float]] = {}
        for person, state, reason in run_now:
            crop = crop_frame(frame, person.bbox_xyxy)
            if crop is None:
                role, confidence = "uncertain", 0.0
            else:
                role, confidence = self._classify_crop(
                    crop,
                    device=device,
                    accept_confidence=specialist_accept_confidence,
                )
            new_state = self._update_cache(person, role, confidence, frame_index, state)
            role_by_track[person.track_id] = (role, confidence)
            self.stats["specialist_inference"] += 1
            self.last_trace[person.track_id] = {
                "source": "specialist_inference",
                "refresh_reason": reason,
                "track_priority_reason": reason,
                "cache_age_frames": 0,
                "stable_hits": new_state.stable_hits,
                "specialist_deferred_by_budget": False,
            }

        for person, state, reason in deferred:
            if state is not None:
                role, confidence = state.role, state.confidence
                state.last_seen_frame = frame_index
                cache_age = frame_index - state.last_classified_frame
            else:
                role, confidence = "uncertain", 0.0
                cache_age = None
            role_by_track[person.track_id] = (role, confidence)
            self.stats["specialist_deferred_by_budget"] += 1
            self.last_trace[person.track_id] = {
                "source": "specialist_deferred_by_budget",
                "refresh_reason": reason,
                "track_priority_reason": reason,
                "cache_age_frames": cache_age,
                "stable_hits": state.stable_hits if state is not None else 0,
                "specialist_deferred_by_budget": True,
            }

        for track_id, (source, confidence, state, reason, cache_age) in immediate.items():
            self.stats[source] += 1
            role = state.role if state is not None else "uncertain"
            role_by_track[track_id] = (role, confidence)
            self.last_trace[track_id] = {
                "source": source,
                "refresh_reason": reason,
                "track_priority_reason": reason,
                "cache_age_frames": cache_age,
                "stable_hits": state.stable_hits if state is not None else 0,
                "specialist_deferred_by_budget": False,
            }

        self.last_frame_meta = {
            "detector_reused_frame": detector_reused,
            "detector_stride_used": max(1, int(detector_stride_frames)),
            "specialist_budget_configured": specialist_budget_per_frame,
            "specialist_eligible_count": len(eligible),
            "specialist_budget_used": len(run_now),
            "specialist_deferred_count": len(deferred),
        }

        detections = [
            PediatricDetection(person.bbox_xyxy, *role_by_track[person.track_id], person.track_id)
            for person in persons
        ]
        return detections, persons

    def _run_or_reuse_detector(
        self,
        frame: Any,
        *,
        frame_index: int,
        device: str,
        person_confidence: float,
        detector_stride_frames: int,
    ) -> tuple[list[PediatricDetection], bool]:
        """Roda o detector de pessoa ou reaproveita a ultima leitura.

        Em CPU, `detector_stride_frames > 1` faz o detector rodar so a cada
        N chamadas; nos frames intermediarios, as ultimas bboxes/tracks sao
        reutilizadas tal como estavam (seguro: o gate do especialista compara
        geometria contra o cache, e bbox identica nunca dispara refresh por
        engano).
        """
        should_run = (
            self._last_persons is None
            or self._last_detector_frame is None
            or detector_stride_frames <= 1
            or (frame_index - self._last_detector_frame) >= detector_stride_frames
        )
        if not should_run:
            return list(self._last_persons or []), True

        detector_started = time.perf_counter()
        result = self.person_model.track(
            frame,
            persist=True,
            conf=person_confidence,
            device=device,
            tracker="bytetrack.yaml",
            verbose=False,
        )[0]
        self.detector_ms.add((time.perf_counter() - detector_started) * 1000.0)
        persons = self._parse_person_result(result)
        self._last_persons = persons
        self._last_detector_frame = frame_index
        return persons, False

    def _priority_score(
        self,
        person: PediatricDetection,
        reason: str | None,
        priority_track_ids: set[int] | None,
    ) -> tuple[int, float]:
        """Menor score = mais urgente. Usado para cortar no orcamento.

        `reason` e o motivo que o `_gate` ja calculou para essa decisao
        ("new_track", "bbox_shifted", "ttl_expired", ...) -- reaproveitado
        aqui em vez de reinferido, para nao perder a granularidade do gate.
        """
        if priority_track_ids and person.track_id in priority_track_ids:
            return (-1, -person.confidence)
        reason_rank = self._PRIORITY_RANK.get(reason or "ttl_expired", 7)
        return (reason_rank, -person.confidence)

    def _gate(
        self,
        person: PediatricDetection,
        state: TrackClassificationState | None,
        frame_index: int,
        base_ttl_frames: int,
    ) -> tuple[str, str | None]:
        """Decide se reusa cache, pula barato ou chama o especialista.

        Retorna (decisao, motivo) com decisao em
        {"reuse", "classify", "skip_cheap"}.
        """
        width = person.bbox_xyxy[2] - person.bbox_xyxy[0]
        height = person.bbox_xyxy[3] - person.bbox_xyxy[1]
        if width < self.MIN_CROP_SIDE_PX or height < self.MIN_CROP_SIDE_PX:
            return "skip_cheap", "crop_too_small"

        if state is None:
            return "classify", "new_track"

        if frame_index - state.last_seen_frame > self.REAPPEAR_GAP_FRAMES:
            return "classify", "track_reappeared"

        if state.pending_refresh:
            return "classify", "weak_previous_classification"

        if bbox_iou(person.bbox_xyxy, state.bbox_xyxy) < self.MIN_IOU_FOR_REUSE:
            return "classify", "bbox_shifted"

        area_ratio = self._area_ratio(person.bbox_xyxy, state.bbox_xyxy)
        if area_ratio > self.MAX_AREA_RATIO_CHANGE or area_ratio < 1.0 / self.MAX_AREA_RATIO_CHANGE:
            return "classify", "size_changed"

        ttl = self._effective_ttl(state, base_ttl_frames)
        if frame_index - state.last_classified_frame > ttl:
            return "classify", "ttl_expired"

        return "reuse", None

    def _effective_ttl(self, state: TrackClassificationState, base_ttl_frames: int) -> int:
        if state.role != "adult" or state.confidence < self.STABLE_ADULT_MIN_CONFIDENCE:
            return min(base_ttl_frames, self.CHILD_OR_WEAK_TTL_FRAMES)
        if state.stable_hits >= self.STABLE_ADULT_HITS_REQUIRED:
            return max(base_ttl_frames, self.STABLE_ADULT_TTL_FRAMES)
        return base_ttl_frames

    def _update_cache(
        self,
        person: PediatricDetection,
        role: str,
        confidence: float,
        frame_index: int,
        previous: TrackClassificationState | None,
    ) -> TrackClassificationState:
        is_confirmed_adult = role == "adult" and confidence >= self.STABLE_ADULT_MIN_CONFIDENCE
        stable_hits = 0
        if is_confirmed_adult:
            stable_hits = (previous.stable_hits + 1) if (previous is not None and previous.role == "adult") else 1
        state = TrackClassificationState(
            track_id=person.track_id,
            bbox_xyxy=person.bbox_xyxy,
            role=role,
            confidence=confidence,
            last_classified_frame=frame_index,
            last_seen_frame=frame_index,
            stable_hits=stable_hits,
            pending_refresh=(role == "uncertain" or confidence < 0.35),
        )
        self._cache[person.track_id] = state
        return state

    def _prune_stale(self, frame_index: int) -> None:
        stale = [
            track_id
            for track_id, state in self._cache.items()
            if frame_index - state.last_seen_frame > self.STALE_PRUNE_FRAMES
        ]
        for track_id in stale:
            self._cache.pop(track_id, None)

    @staticmethod
    def _area_ratio(
        current: tuple[float, float, float, float],
        previous: tuple[float, float, float, float],
    ) -> float:
        current_area = max(1.0, (current[2] - current[0]) * (current[3] - current[1]))
        previous_area = max(1.0, (previous[2] - previous[0]) * (previous[3] - previous[1]))
        return current_area / previous_area

    def _parse_person_result(self, result: Any) -> list[PediatricDetection]:
        boxes = result.boxes
        names = {
            int(index): str(name).strip().lower()
            for index, name in dict(getattr(result, "names", {}) or {}).items()
        }
        if not names and hasattr(self.person_model, "names"):
            names = {
                int(index): str(name).strip().lower()
                for index, name in dict(self.person_model.names).items()
            }
        if boxes is None:
            return []
        persons: list[PediatricDetection] = []
        for index in range(len(boxes)):
            class_name = names.get(int(boxes.cls[index]), "")
            if class_name != "person":
                continue
            track_id = int(boxes.id[index]) if boxes.id is not None else index + 1
            bbox = tuple(float(value) for value in boxes.xyxy[index].cpu().numpy())
            persons.append(
                PediatricDetection(
                    bbox,
                    "person",
                    float(boxes.conf[index]),
                    track_id,
                )
            )
        return persons

    def _classify_crop(
        self,
        crop: Any,
        *,
        device: str,
        accept_confidence: float,
    ) -> tuple[str, float]:
        specialist_started = time.perf_counter()
        result = self.specialist_model.predict(
            crop,
            conf=0.03,
            device=device,
            verbose=False,
        )[0]
        self.specialist_ms.add((time.perf_counter() - specialist_started) * 1000.0)
        boxes = result.boxes
        if boxes is None or len(boxes) == 0:
            return "uncertain", 0.0
        best_role = "uncertain"
        best_confidence = 0.0
        for index in range(len(boxes)):
            class_name = self.specialist_names.get(int(boxes.cls[index]), "")
            if class_name not in {"child", "adult"}:
                continue
            confidence = float(boxes.conf[index])
            if confidence > best_confidence:
                best_role = class_name
                best_confidence = confidence
        if best_confidence < accept_confidence:
            return "uncertain", best_confidence
        return best_role, best_confidence


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

        self._build_ui(initial_source)

        self.timer = QTimer(self)
        self.timer.timeout.connect(self._next_frame)
        self.setWindowTitle("Pediatria Local - apresentacao")
        self.resize(1280, 760)

        self._service_startup_timer.start(200)

    def _make_session_evidence_dir(self) -> Path:
        return self.report_path.parent / "evidence" / "sessions" / self.session_id

    def _build_ui(self, initial_source: str) -> None:
        central = QWidget()
        layout = QVBoxLayout(central)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)

        title = QLabel("Pediatria Local")
        title.setStyleSheet("font-size: 16px; font-weight: bold;")
        layout.addWidget(title)

        service_row = QHBoxLayout()
        self.service_status_label = QLabel("Servico local: verificando...")
        self.service_status_label.setStyleSheet("color: #888;")
        self.service_button = QPushButton("Iniciar servico")
        self.service_button.clicked.connect(self._on_service_button_clicked)
        service_row.addWidget(self.service_status_label, 1)
        service_row.addWidget(self.service_button)
        layout.addLayout(service_row)

        panel = QGroupBox("Operacao")
        panel.setStyleSheet("QGroupBox { font-weight: bold; }")
        panel_layout = QGridLayout(panel)
        panel_layout.setContentsMargins(8, 8, 8, 8)
        panel_layout.setHorizontalSpacing(8)
        panel_layout.setVerticalSpacing(4)

        self.source_mode = QComboBox()
        self.source_mode.addItems([SOURCE_FILE, SOURCE_URL, SOURCE_USB])
        self.source_mode.currentTextChanged.connect(self._update_source_controls)
        panel_layout.addWidget(QLabel("Fonte"), 0, 0)
        panel_layout.addWidget(self.source_mode, 0, 1)

        self.source_value = QLineEdit(initial_source)
        self.source_value.setPlaceholderText("Escolha um video, URL RTSP ou camera USB")
        self.browse_button = QPushButton("Escolher...")
        self.browse_button.clicked.connect(self._browse_video)
        panel_layout.addWidget(self.source_value, 0, 2, 1, 2)
        panel_layout.addWidget(self.browse_button, 0, 4)

        self.usb_index = QSpinBox()
        self.usb_index.setRange(0, 20)
        panel_layout.addWidget(QLabel("USB"), 0, 5)
        panel_layout.addWidget(self.usb_index, 0, 6)

        self.force_cpu_checkbox = QCheckBox("Forcar CPU")
        self.force_cpu_checkbox.setChecked(self.force_cpu_default)
        panel_layout.addWidget(self.force_cpu_checkbox, 1, 0, 1, 2)

        self.collect_dataset_checkbox = QCheckBox("Modo coleta/treino")
        self.collect_dataset_checkbox.setChecked(self.collect_dataset_default)
        panel_layout.addWidget(self.collect_dataset_checkbox, 1, 2, 1, 2)

        controls = QHBoxLayout()
        self.start_button = QPushButton("Iniciar")
        self.start_button.clicked.connect(self.start_analysis)
        self.stop_button = QPushButton("Parar")
        self.stop_button.clicked.connect(self.stop_analysis)
        self.stop_button.setEnabled(False)
        controls.addWidget(self.start_button)
        controls.addWidget(self.stop_button)
        panel_layout.addLayout(controls, 1, 4, 1, 3)
        panel_layout.setColumnStretch(2, 1)
        layout.addWidget(panel)

        self.pipeline_status = QLabel("ANALISANDO: aguardando inicio")
        self.pipeline_status.setStyleSheet(
            "background: #20242b; color: white; padding: 5px; border-radius: 4px;"
        )
        layout.addWidget(self.pipeline_status)
        self.status = self.pipeline_status

        self.session_summary_label = QLabel("Sessao: 0 frames | 0 alertas")
        self.session_summary_label.setStyleSheet("color: #555;")
        layout.addWidget(self.session_summary_label)

        self.video = QLabel("Selecione uma fonte e clique em Iniciar")
        self.video.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.video.setMinimumSize(720, 420)
        self.video.setStyleSheet("background: #111; color: #ddd;")
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

        self.setCentralWidget(central)
        self._update_source_controls(self.source_mode.currentText())

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
            text = f"Servico local: rodando ({service_launcher.DEFAULT_HOST}:{service_launcher.DEFAULT_PORT})"
            style = "color: #2e7d32;"
        elif running is False:
            text = "Servico local: parado" + (f" - {note}" if note else "")
            style = "color: #c62828;"
        else:
            text = f"Servico local: {note or 'verificando...'}"
            style = "color: #888;"
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

    def _next_frame(self) -> None:
        if self.capture is None or self.service is None:
            return
        loop_started = time.perf_counter()
        ok, frame = self.capture.read()
        if not ok or frame is None:
            if isinstance(self.current_source, str) and "://" not in self.current_source:
                self.capture.set(cv2.CAP_PROP_POS_FRAMES, 0)
                return
            self.stop_analysis()
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

