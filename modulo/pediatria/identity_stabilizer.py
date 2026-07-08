from __future__ import annotations

import colorsys
from dataclasses import dataclass
from typing import Any

import cv2

from .crop_pipeline import crop_frame
from .detector_mvp import PediatricDetection


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
