from __future__ import annotations

import argparse
import colorsys
import json
import os
import sys
import time
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import cv2
from PyQt6.QtCore import Qt, QTimer
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


def performance_profile_for_device(device: str, source_fps: float) -> dict[str, int | float | str]:
    """Perfil conservador sem pular tracking: CPU reduz somente ritmo de leitura/display."""

    is_cpu = str(device or "").lower() == "cpu"
    display_fps = 12.0 if is_cpu else min(max(float(source_fps or 30.0), 1.0), 30.0)
    return {
        "device": device,
        "target_analysis_fps": display_fps,
        "display_fps": display_fps,
        "analysis_stride": 1,
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
class CropRoleCacheEntry:
    role: str
    confidence: float
    bbox_xyxy: tuple[float, float, float, float]
    frame_index: int


class PersonCropSpecialistPipeline:
    """Detecta pessoas no frame e deixa o especialista decidir no crop."""

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
        self._cache: dict[int, CropRoleCacheEntry] = {}

    @staticmethod
    def _load_detect_model(model_path: Path) -> Any:
        from ultralytics import YOLO

        if model_path.is_dir() or model_path.suffix.lower() == ".xml":
            return YOLO(str(model_path), task="detect")
        return YOLO(str(model_path))

    def reset(self) -> None:
        self._cache.clear()

    def detect_and_classify(
        self,
        frame: Any,
        *,
        frame_index: int,
        device: str,
        person_confidence: float,
        specialist_accept_confidence: float,
        cache_ttl_frames: int,
    ) -> tuple[list[PediatricDetection], list[PediatricDetection]]:
        result = self.person_model.track(
            frame,
            persist=True,
            conf=person_confidence,
            device=device,
            tracker="bytetrack.yaml",
            verbose=False,
        )[0]
        persons = self._parse_person_result(result)
        detections: list[PediatricDetection] = []
        for person in persons:
            cached = self._get_cached_role(
                person,
                frame_index=frame_index,
                cache_ttl_frames=cache_ttl_frames,
            )
            if cached is not None:
                detections.append(
                    PediatricDetection(
                        person.bbox_xyxy,
                        cached.role,
                        cached.confidence,
                        person.track_id,
                    )
                )
                continue
            crop = crop_frame(frame, person.bbox_xyxy)
            if crop is None:
                role, confidence = "uncertain", 0.0
            else:
                role, confidence = self._classify_crop(
                    crop,
                    device=device,
                    accept_confidence=specialist_accept_confidence,
                )
            self._cache[person.track_id] = CropRoleCacheEntry(
                role=role,
                confidence=confidence,
                bbox_xyxy=person.bbox_xyxy,
                frame_index=frame_index,
            )
            detections.append(
                PediatricDetection(
                    person.bbox_xyxy,
                    role,
                    confidence,
                    person.track_id,
                )
            )
        return detections, persons

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

    def _get_cached_role(
        self,
        person: PediatricDetection,
        *,
        frame_index: int,
        cache_ttl_frames: int,
    ) -> CropRoleCacheEntry | None:
        cached = self._cache.get(person.track_id)
        if cached is None:
            return None
        if frame_index - cached.frame_index > cache_ttl_frames:
            return None
        if bbox_iou(person.bbox_xyxy, cached.bbox_xyxy) < 0.35:
            return None
        return cached

    def _classify_crop(
        self,
        crop: Any,
        *,
        device: str,
        accept_confidence: float,
    ) -> tuple[str, float]:
        result = self.specialist_model.predict(
            crop,
            conf=0.03,
            device=device,
            verbose=False,
        )[0]
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
        self.performance_profile: dict[str, int | float | str] = {}
        self.runner: PediatricsDetectorMvpRunner | None = None
        self.crop_pipeline: PersonCropSpecialistPipeline | None = None
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
        self.evidence_dir = self._make_session_evidence_dir()
        self.events_log_path = self.evidence_dir / "events.jsonl"
        self.voice = MvpVoiceAnnouncer(
            enabled=voice_enabled,
            repeat_interval_seconds=voice_repeat_seconds,
        )
        self._build_ui(initial_source)

        self.timer = QTimer(self)
        self.timer.timeout.connect(self._next_frame)
        self.setWindowTitle("Pediatria Local - apresentacao")
        self.resize(1280, 760)

    def _make_session_evidence_dir(self) -> Path:
        stamp = self.session_started_at.strftime("%Y%m%d_%H%M%S")
        return self.report_path.parent / f"evidencias_{stamp}"

    def _build_ui(self, initial_source: str) -> None:
        central = QWidget()
        layout = QVBoxLayout(central)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(6)

        title = QLabel("Pediatria Local - analise visual de acompanhamento")
        title.setStyleSheet("font-size: 17px; font-weight: bold;")
        layout.addWidget(title)

        subtitle = QLabel(
            "Demonstracao local com modelo pediatrico. Nenhum alerta e enviado ao WebGuardiao."
        )
        subtitle.setStyleSheet("color: #666;")
        layout.addWidget(subtitle)

        panel = QGroupBox("Configuracao da rodada")
        panel.setStyleSheet("QGroupBox { font-weight: bold; }")
        panel_layout = QGridLayout(panel)
        panel_layout.setContentsMargins(8, 8, 8, 8)
        panel_layout.setHorizontalSpacing(12)
        panel_layout.setVerticalSpacing(4)

        left_form = QFormLayout()
        left_form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        left_form.setHorizontalSpacing(8)
        left_form.setVerticalSpacing(4)
        right_form = QFormLayout()
        right_form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        right_form.setHorizontalSpacing(8)
        right_form.setVerticalSpacing(4)

        self.source_mode = QComboBox()
        self.source_mode.addItems([SOURCE_FILE, SOURCE_URL, SOURCE_USB])
        self.source_mode.currentTextChanged.connect(self._update_source_controls)
        left_form.addRow("Fonte:", self.source_mode)

        source_row = QHBoxLayout()
        self.source_value = QLineEdit(initial_source)
        self.source_value.setPlaceholderText("Escolha um video local")
        self.browse_button = QPushButton("Escolher video...")
        self.browse_button.clicked.connect(self._browse_video)
        source_row.addWidget(self.source_value, 1)
        source_row.addWidget(self.browse_button)
        left_form.addRow("Arquivo / URL:", source_row)

        self.usb_index = QSpinBox()
        self.usb_index.setRange(0, 20)
        left_form.addRow("USB:", self.usb_index)

        model_row = QHBoxLayout()
        self.model_value = QLineEdit(str(self.model_path))
        self.model_browse_button = QPushButton("Escolher modelo...")
        self.model_browse_button.clicked.connect(self._browse_specialist_model)
        model_row.addWidget(self.model_value, 1)
        model_row.addWidget(self.model_browse_button)
        left_form.addRow("Especialista:", model_row)

        person_model_row = QHBoxLayout()
        self.person_model_value = QLineEdit(str(self.person_model_path))
        self.person_model_browse_button = QPushButton("Escolher pessoa...")
        self.person_model_browse_button.clicked.connect(self._browse_person_model)
        person_model_row.addWidget(self.person_model_value, 1)
        person_model_row.addWidget(self.person_model_browse_button)
        left_form.addRow("Detector pessoa:", person_model_row)

        self.person_crop_checkbox = QCheckBox(
            "Pessoa -> crop -> especialista"
        )
        self.person_crop_checkbox.setChecked(self.person_crop_pipeline_default)
        right_form.addRow("Fluxo:", self.person_crop_checkbox)

        self.force_cpu_checkbox = QCheckBox(
            "Forcar CPU nesta rodada, mesmo se houver GPU"
        )
        self.force_cpu_checkbox.setChecked(self.force_cpu_default)
        right_form.addRow("Dispositivo:", self.force_cpu_checkbox)

        self.weak_child_checkbox = QCheckBox(
            "Usar candidatos infantis fracos persistentes"
        )
        self.weak_child_checkbox.setChecked(self.weak_child_candidates_default)
        right_form.addRow("Sensibilidade:", self.weak_child_checkbox)

        self.collect_dataset_checkbox = QCheckBox(
            "Coletar crops/frames para dataset de revisao"
        )
        self.collect_dataset_checkbox.setChecked(self.collect_dataset_default)
        right_form.addRow("Modo treino:", self.collect_dataset_checkbox)

        self.dataset_interval = QSpinBox()
        self.dataset_interval.setRange(1, 600)
        self.dataset_interval.setValue(self.dataset_sample_interval_frames)
        right_form.addRow("Coleta N frames:", self.dataset_interval)

        self.dataset_max_per_track = QSpinBox()
        self.dataset_max_per_track.setRange(1, 1000)
        self.dataset_max_per_track.setValue(self.dataset_max_crops_per_track)
        right_form.addRow("Max crops/track:", self.dataset_max_per_track)

        controls = QHBoxLayout()
        self.start_button = QPushButton("Iniciar analise")
        self.start_button.clicked.connect(self.start_analysis)
        self.stop_button = QPushButton("Parar")
        self.stop_button.clicked.connect(self.stop_analysis)
        self.stop_button.setEnabled(False)
        controls.addWidget(self.start_button)
        controls.addWidget(self.stop_button)
        controls.addStretch()
        right_form.addRow("", controls)

        panel_layout.addLayout(left_form, 0, 0)
        panel_layout.addLayout(right_form, 0, 1)
        panel_layout.setColumnStretch(0, 3)
        panel_layout.setColumnStretch(1, 2)
        layout.addWidget(panel)

        self.status = QLabel("Pronto para selecionar uma fonte.")
        self.status.setStyleSheet(
            "background: #20242b; color: white; padding: 5px; border-radius: 4px;"
        )
        layout.addWidget(self.status)

        self.video = QLabel("Selecione uma fonte e clique em Iniciar analise")
        self.video.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.video.setMinimumSize(900, 500)
        self.video.setStyleSheet("background: #111; color: #ddd;")
        self.video.setScaledContents(True)
        layout.addWidget(self.video, 1)
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
            selected_model_path = resolve_model_path(
                self.model_value.text(),
                self.model_path,
            )
            if self.runner is None or selected_model_path != self._loaded_specialist_model_path:
                self.status.setText(f"Carregando especialista: {selected_model_path.name}...")
                QApplication.processEvents()
                self.runner = PediatricsDetectorMvpRunner(selected_model_path)
                self.model_path = selected_model_path
                self._loaded_specialist_model_path = selected_model_path
                self.crop_pipeline = None
                self._loaded_person_model_path = None
            if self.person_crop_checkbox.isChecked():
                selected_person_model_path = resolve_model_path(
                    self.person_model_value.text(),
                    self.person_model_path,
                )
                if not selected_person_model_path.exists():
                    raise FileNotFoundError(
                        f"Detector de pessoa nao encontrado: {selected_person_model_path}"
                    )
                if (
                    self.crop_pipeline is None
                    or selected_person_model_path != self._loaded_person_model_path
                ):
                    self.status.setText(
                        f"Carregando detector pessoa: {selected_person_model_path.name}..."
                    )
                    QApplication.processEvents()
                    self.crop_pipeline = PersonCropSpecialistPipeline(
                        person_model_path=selected_person_model_path,
                        specialist_model=self.runner.model,
                        specialist_names=self.runner.names,
                    )
                    self.person_model_path = selected_person_model_path
                    self._loaded_person_model_path = selected_person_model_path
            else:
                self.crop_pipeline = None
        except (OSError, ValueError, FileNotFoundError) as exc:
            if capture is not None:
                capture.release()
            QMessageBox.warning(self, "Fonte indisponivel", str(exc))
            self.status.setText(f"Falha ao iniciar: {exc}")
            return

        self.stop_analysis()
        self.capture = capture
        self.current_source = source
        self.analyzer = CompanionshipAnalyzer()
        self.role_adjuster = PediatricRoleContextAdjuster()
        self.identity_stabilizer.reset()
        self.weak_child_promoter.reset()
        if self.crop_pipeline is not None:
            self.crop_pipeline.reset()
        self.alert_latch.clear()
        self.frame_index = 0
        self.popup_count = 0
        self.states.clear()
        self.latencies.clear()
        self._resolved_frames_by_camera.clear()
        self._last_suppressed_log.clear()
        self.session_started_at = datetime.now()
        self.evidence_dir = self._make_session_evidence_dir()
        self.evidence_dir.mkdir(parents=True, exist_ok=True)
        self.events_log_path = self.evidence_dir / "events.jsonl"
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
                sample_interval_frames=self.dataset_interval.value(),
                max_crops_per_track=self.dataset_max_per_track.value(),
            )
        fps = capture.get(cv2.CAP_PROP_FPS) or 30.0
        self.performance_profile = performance_profile_for_device(self.device, fps)
        self.timer.start(max(1, int(1000 / float(self.performance_profile["display_fps"]))))
        self.start_button.setEnabled(False)
        self.stop_button.setEnabled(True)
        self.force_cpu_checkbox.setEnabled(False)
        self.model_value.setEnabled(False)
        self.model_browse_button.setEnabled(False)
        self.person_crop_checkbox.setEnabled(False)
        self.person_model_value.setEnabled(False)
        self.person_model_browse_button.setEnabled(False)
        self.weak_child_checkbox.setEnabled(False)
        self.collect_dataset_checkbox.setEnabled(False)
        self.dataset_interval.setEnabled(False)
        self.dataset_max_per_track.setEnabled(False)
        self._append_session_log(
            {
                "event_type": "analysis_started",
                "source": source_name,
                "raw_source": redact_source_value(source),
                "model": str(self.model_path),
                "confidence": self.confidence,
                "source_fps": fps,
                "requested_device": self.requested_device,
                "resolved_device": self.device,
                "device_reason": self.device_reason,
                "force_cpu": self.force_cpu_checkbox.isChecked(),
                "person_crop_pipeline": self.person_crop_checkbox.isChecked(),
                "person_model": str(self.person_model_path),
                "weak_child_candidates": self.weak_child_checkbox.isChecked(),
                "weak_child_confidence": self.weak_child_confidence,
                "weak_child_promote_frames": self.weak_child_promote_frames,
                "performance_profile": self.performance_profile,
                "dataset_collection": (
                    self.dataset_collector.summary()
                    if self.dataset_collector is not None
                    else None
                ),
            }
        )
        self.status.setText(
            f"Analisando: {source_name} | dispositivo={self.device} | "
            f"perfil={self.device_reason} | analise~{self.performance_profile['target_analysis_fps']} fps"
        )

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
        if self.capture is None or self.runner is None:
            return
        ok, frame = self.capture.read()
        if not ok or frame is None:
            if isinstance(self.current_source, str) and "://" not in self.current_source:
                self.capture.set(cv2.CAP_PROP_POS_FRAMES, 0)
                return
            self.stop_analysis()
            return

        self.frame_index += 1
        clean_frame = frame.copy()
        weak_child_enabled = self.weak_child_checkbox.isChecked()
        crop_pipeline_enabled = (
            self.person_crop_checkbox.isChecked()
            and self.crop_pipeline is not None
        )
        inference_confidence = (
            min(self.confidence, self.weak_child_confidence)
            if weak_child_enabled and not crop_pipeline_enabled
            else self.confidence
        )

        inference_started = time.perf_counter()
        try:
            if crop_pipeline_enabled:
                parsed_detections, person_detections = self.crop_pipeline.detect_and_classify(
                    frame,
                    frame_index=self.frame_index,
                    device=self.device,
                    person_confidence=self.person_confidence,
                    specialist_accept_confidence=self.confidence,
                    cache_ttl_frames=self.crop_cache_frames,
                )
                result = None
            else:
                result = self.runner.model.track(
                    frame,
                    persist=True,
                    conf=inference_confidence,
                    device=self.device,
                    tracker="bytetrack.yaml",
                    verbose=False,
                )[0]
                person_detections = []
        except Exception as exc:
            reason = str(exc).lower()
            if self.device != "cpu" and (
                "cuda" in reason or "out of memory" in reason or "cublas" in reason
            ):
                self.device = "cpu"
                self.device_reason = "gpu_indisponivel_fallback_cpu"
                fps = self.capture.get(cv2.CAP_PROP_FPS) if self.capture is not None else 30.0
                self.performance_profile = performance_profile_for_device(self.device, fps or 30.0)
                self.timer.start(max(1, int(1000 / float(self.performance_profile["display_fps"]))))
                self.status.setText(
                    "GPU indisponivel ou com pouca memoria. Reprocessando em CPU "
                    f"com analise~{self.performance_profile['target_analysis_fps']} fps."
                )
                if crop_pipeline_enabled:
                    parsed_detections, person_detections = self.crop_pipeline.detect_and_classify(
                        frame,
                        frame_index=self.frame_index,
                        device=self.device,
                        person_confidence=self.person_confidence,
                        specialist_accept_confidence=self.confidence,
                        cache_ttl_frames=self.crop_cache_frames,
                    )
                    result = None
                else:
                    result = self.runner.model.track(
                        frame,
                        persist=True,
                        conf=inference_confidence,
                        device=self.device,
                        tracker="bytetrack.yaml",
                        verbose=False,
                    )[0]
                    person_detections = []
            else:
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
                return
        self.latencies.append((time.perf_counter() - inference_started) * 1000.0)
        if not crop_pipeline_enabled:
            parsed_detections = self.runner._parse_result(result)
            person_detections = []
        normal_detections = [
            item for item in parsed_detections if item.confidence >= self.confidence
        ]
        weak_child_detections: list[PediatricDetection] = []
        weak_child_promoted: list[PediatricDetection] = []
        if weak_child_enabled:
            weak_child_detections = [
                item
                for item in parsed_detections
                if item.role == "child"
                and self.weak_child_confidence <= item.confidence < self.confidence
            ]
            adult_blockers = [
                item
                for item in parsed_detections
                if item.role == "adult" and item.confidence >= self.weak_child_confidence
            ]
            weak_child_promoted = self.weak_child_promoter.promote(
                weak_child_detections,
                adult_blockers,
                frame_index=self.frame_index,
            )
            source_name_for_weak = source_display_name(
                self.current_source or "pediatria_local"
            )
            for promotion in self.weak_child_promoter.last_promotions:
                self._append_session_log(
                    {
                        "event_type": "weak_child_promoted",
                        "source": source_name_for_weak,
                        "frame_index": self.frame_index,
                        "original_track_id": promotion.original_track_id,
                        "stable_track_id": promotion.stable_track_id,
                        "hits": promotion.hits,
                        "confidence": promotion.confidence,
                        "spatial_score": promotion.spatial_score,
                        "size_score": promotion.size_score,
                        "weak_child_confidence": self.weak_child_confidence,
                        "promoted_confidence": self.weak_child_promoter.promoted_confidence,
                    }
                )
        resolved = self.role_adjuster.adjust(
            resolve_role_conflicts(normal_detections + weak_child_promoted),
            frame_index=self.frame_index,
        )
        resolved = self.identity_stabilizer.stabilize(
            resolved,
            clean_frame,
            frame_index=self.frame_index,
        )
        resolved = dedupe_detections_by_track(resolved)
        if self.identity_stabilizer.last_merges:
            source_name_for_merge = source_display_name(
                self.current_source or "pediatria_local"
            )
            for merge in self.identity_stabilizer.last_merges:
                self._append_session_log(
                    {
                        "event_type": "identity_merge",
                        "source": source_name_for_merge,
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
        tracks = [
            CompanionTrack(item.track_id, item.bbox_xyxy, item.role, item.confidence)
            for item in resolved
        ]
        state = self.analyzer.observe(tracks)
        self.states[state.stable_state] += 1
        source_name = source_display_name(self.current_source or "pediatria_local")
        if self.dataset_collector is not None:
            self.dataset_collector.collect(
                frame_index=self.frame_index,
                clean_frame=clean_frame,
                detections=resolved,
                state=state,
            )
        self._append_frame_diagnostic_if_due(
            state=state,
            detections=resolved,
            person_detections=person_detections,
            weak_child_detections=weak_child_detections,
            weak_child_promotions=weak_child_promoted,
            source_name=source_name,
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
                evidence_paths = self._save_alert_evidence(
                    source_name=source_name,
                    child=child,
                    state=state,
                    detections=resolved,
                    clean_frame=clean_frame,
                    evidence_frame=evidence_frame,
                )
                self._append_event_log(
                    "popup_alert",
                    source_name=source_name,
                    state=state,
                    child=child,
                    detections=resolved,
                    evidence_paths=evidence_paths,
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
                )
        else:
            self._resolved_frames_by_camera[source_name] += 1
            if self._resolved_frames_by_camera[source_name] >= 45:
                self.alert_latch.resolve_camera(source_name)

        self.runner._annotate(frame, resolved, state.stable_state, self.frame_index)
        self.video.setPixmap(self._frame_to_pixmap(frame))

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

    def _save_alert_evidence(
        self,
        *,
        source_name: str,
        child: PediatricDetection,
        state: Any,
        detections: list[PediatricDetection],
        clean_frame: Any,
        evidence_frame: Any,
    ) -> dict[str, str]:
        timestamp = datetime.now()
        stem = (
            f"{timestamp.strftime('%Y%m%d_%H%M%S_%f')[:-3]}"
            f"__{safe_name(source_name)}__track_{child.track_id}__{state.stable_state}"
        )
        camera_dir = self.evidence_dir / safe_name(source_name)
        camera_dir.mkdir(parents=True, exist_ok=True)
        paths = {
            "frame": camera_dir / f"{stem}_frame.jpg",
            "alert_bbox": camera_dir / f"{stem}_bbox.jpg",
            "crop": camera_dir / f"{stem}_crop.jpg",
            "metadata": camera_dir / f"{stem}.json",
        }
        cv2.imwrite(str(paths["frame"]), clean_frame)
        cv2.imwrite(str(paths["alert_bbox"]), evidence_frame)
        crop = crop_frame(clean_frame, child.bbox_xyxy)
        if crop is not None:
            cv2.imwrite(str(paths["crop"]), crop)
        else:
            paths.pop("crop", None)

        metadata = self._build_event_payload(
            event_type="popup_alert",
            source_name=source_name,
            state=state,
            child=child,
            detections=detections,
            evidence_paths={key: str(value) for key, value in paths.items() if key != "metadata"},
        )
        paths["metadata"].write_text(
            json.dumps(metadata, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        return {key: str(value) for key, value in paths.items()}

    def _append_event_log(
        self,
        event_type: str,
        *,
        source_name: str,
        state: Any,
        child: PediatricDetection | None,
        detections: list[PediatricDetection],
        evidence_paths: dict[str, str] | None = None,
    ) -> None:
        payload = self._build_event_payload(
            event_type=event_type,
            source_name=source_name,
            state=state,
            child=child,
            detections=detections,
            evidence_paths=evidence_paths or {},
        )
        self.events_log_path.parent.mkdir(parents=True, exist_ok=True)
        with self.events_log_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False) + "\n")

    def _append_session_log(self, payload: dict[str, Any]) -> None:
        item = {
            "created_at": datetime.now().isoformat(timespec="milliseconds"),
            **payload,
        }
        self.events_log_path.parent.mkdir(parents=True, exist_ok=True)
        with self.events_log_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(item, ensure_ascii=False) + "\n")

    def _append_suppressed_alert_if_due(
        self,
        *,
        source_name: str,
        child: PediatricDetection,
        state: Any,
        detections: list[PediatricDetection],
    ) -> None:
        key = (source_name, child.track_id, state.stable_state)
        now = time.monotonic()
        if now - self._last_suppressed_log.get(key, 0.0) < 10.0:
            return
        self._last_suppressed_log[key] = now
        payload = self._build_event_payload(
            event_type="popup_suppressed",
            source_name=source_name,
            state=state,
            child=child,
            detections=detections,
            evidence_paths={},
        )
        payload["suppression_reason"] = "active_episode_or_camera_track_cooldown"
        self.events_log_path.parent.mkdir(parents=True, exist_ok=True)
        with self.events_log_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False) + "\n")

    def _append_frame_diagnostic_if_due(
        self,
        *,
        state: Any,
        detections: list[PediatricDetection],
        person_detections: list[PediatricDetection],
        weak_child_detections: list[PediatricDetection],
        weak_child_promotions: list[PediatricDetection],
        source_name: str,
    ) -> None:
        interval = self.diagnostic_log_interval_frames
        if interval <= 0 or self.frame_index % interval != 0:
            return
        payload = self._build_event_payload(
            event_type="frame_diagnostic",
            source_name=source_name,
            state=state,
            child=None,
            detections=detections,
            evidence_paths={},
        )
        payload["role_counts"] = role_counts(detections)
        payload["person_detector_count"] = len(person_detections)
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
            for item in self.identity_stabilizer.last_merges
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
        evidence_paths: dict[str, str],
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
        selected_child = None
        if child is not None:
            selected_child = {
                "track_id": child.track_id,
                "role": child.role,
                "confidence": child.confidence,
                "bbox_xyxy": list(child.bbox_xyxy),
            }
        return {
            "event_type": event_type,
            "created_at": datetime.now().isoformat(timespec="milliseconds"),
            "source": source_name,
            "frame_index": self.frame_index,
            "requested_device": self.requested_device,
            "resolved_device": self.device,
            "device_reason": self.device_reason,
            "raw_state": state.raw_state,
            "stable_state": state.stable_state,
            "reason": state.reason,
            "selected_child": selected_child,
            "children": children,
            "detections": [
                {
                    "track_id": item.track_id,
                    "role": item.role,
                    "confidence": item.confidence,
                    "bbox_xyxy": list(item.bbox_xyxy),
                }
                for item in detections
            ],
            "evidence_paths": evidence_paths,
        }

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
