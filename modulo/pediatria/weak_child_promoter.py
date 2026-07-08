from __future__ import annotations

from dataclasses import dataclass

from .detector_mvp import PediatricDetection, bbox_iou
from .identity_stabilizer import PediatricIdentityStabilizer


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
