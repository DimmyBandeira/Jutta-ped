from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Any

import numpy as np


@dataclass(frozen=True)
class Signal:
    name: str
    family: str
    score: float
    confidence: float
    raw: float
    valid: bool


@dataclass(frozen=True)
class PoseQuality:
    state: str
    valid_keypoints: int
    has_shoulders: bool
    has_hips: bool
    has_ankles: bool
    has_head: bool
    seated: bool


@dataclass(frozen=True)
class VisualChildSignal:
    child_prob: float
    adult_prob: float
    uncertain_prob: float
    quality: float
    label: str
    reason: str | None = None


@dataclass(frozen=True)
class ScoreResult:
    instant_score: float | None
    stable_score: float | None
    state: str
    valid_signals: tuple[str, ...]
    valid_families: tuple[str, ...]
    family_scores: dict[str, float]
    diagnostic_hint: str


@dataclass
class TrackState:
    buffer_frames: int
    scores: deque[float] = field(init=False)
    state: str = "UNCERTAIN"
    pending_state: str | None = None
    pending_count: int = 0
    frames_observed: int = 0
    frames_with_score: int = 0
    instant_scores: list[float] = field(default_factory=list)
    signal_raw_history: dict[str, list[float]] = field(
        default_factory=lambda: defaultdict(list)
    )
    signal_valid_counts: dict[str, int] = field(
        default_factory=lambda: defaultdict(int)
    )
    family_valid_counts: dict[str, int] = field(
        default_factory=lambda: defaultdict(int)
    )
    pose_quality_counts: dict[str, int] = field(
        default_factory=lambda: defaultdict(int)
    )
    family_score_history: dict[str, list[float]] = field(
        default_factory=lambda: defaultdict(list)
    )
    diagnostic_hint_counts: dict[str, int] = field(
        default_factory=lambda: defaultdict(int)
    )
    visual_history: list[VisualChildSignal] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.scores = deque(maxlen=self.buffer_frames)

    def stable_score(self) -> float | None:
        if not self.scores:
            return None
        return float(np.mean(self.scores))

    def raw_stats(self) -> dict[str, dict[str, Any]]:
        return {
            name: {
                "mean": round(float(np.mean(values)), 4),
                "min": round(float(np.min(values)), 4),
                "max": round(float(np.max(values)), 4),
                "n": len(values),
            }
            for name, values in self.signal_raw_history.items()
            if values
        }
