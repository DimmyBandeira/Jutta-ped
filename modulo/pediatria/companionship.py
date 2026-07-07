from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from math import hypot

from .config import CompanionshipConfig
from .contracts import VisualChildSignal

VALID_ROLES = {"child", "adult", "uncertain"}
VALID_STATES = {
    "NO_CHILD",
    "ACCOMPANIED",
    "CHILD_SEPARATED",
    "CHILD_ALONE",
    "UNCERTAIN",
}


def role_from_evidence(
    *,
    stable_score: float | None,
    diagnostic_hint: str,
    visual: VisualChildSignal | None = None,
) -> tuple[str, float]:
    """Converte evidencias existentes em papel, sem estimar idade exata."""

    if visual is not None and visual.reason is None:
        if visual.label in {"child", "adult"}:
            return visual.label, visual.quality
    if stable_score is None:
        return "uncertain", 0.0
    if diagnostic_hint == "PROPOSED_CHILD":
        return "child", stable_score
    if diagnostic_hint == "PROPOSED_ADULT":
        return "adult", 1.0 - stable_score
    return "uncertain", abs(stable_score - 0.5) * 2.0


@dataclass(frozen=True)
class CompanionTrack:
    track_id: int
    bbox_xyxy: tuple[float, float, float, float]
    role: str
    confidence: float

    def __post_init__(self) -> None:
        if self.role not in VALID_ROLES:
            raise ValueError(f"Role pediatrico invalido: {self.role}")
        x1, y1, x2, y2 = self.bbox_xyxy
        if x2 <= x1 or y2 <= y1:
            raise ValueError("BBox pediatrica invalida.")

    @property
    def footpoint(self) -> tuple[float, float]:
        x1, _, x2, y2 = self.bbox_xyxy
        return ((x1 + x2) / 2.0, y2)

    @property
    def height(self) -> float:
        return self.bbox_xyxy[3] - self.bbox_xyxy[1]


@dataclass(frozen=True)
class ChildCompanionship:
    child_track_id: int
    state: str
    nearest_adult_track_id: int | None
    nearest_adult_distance_ratio: float | None


@dataclass(frozen=True)
class CompanionshipResult:
    raw_state: str
    stable_state: str
    children: tuple[ChildCompanionship, ...]
    reason: str


class CompanionshipAnalyzer:
    """Avalia companhia por frame e estabiliza o estado por camera."""

    def __init__(self, config: CompanionshipConfig | None = None) -> None:
        self.config = config or CompanionshipConfig()
        self._stable_state = "UNCERTAIN"
        self._pending_state: str | None = None
        self._pending_count = 0
        self._adult_missing_frames: dict[int, int] = defaultdict(int)
        self._near_pair_frames: dict[tuple[int, int], int] = defaultdict(int)
        self._bound_adults: dict[int, int] = {}

    def observe(self, tracks: list[CompanionTrack]) -> CompanionshipResult:
        bound_adult_ids = set(self._bound_adults.values())
        normalized = [
            CompanionTrack(
                track.track_id,
                track.bbox_xyxy,
                (
                    "child"
                    if track.track_id in self._bound_adults
                    else "adult"
                    if track.track_id in bound_adult_ids
                    else track.role
                ),
                (
                    max(track.confidence, self.config.min_role_confidence)
                    if track.track_id in self._bound_adults
                    or track.track_id in bound_adult_ids
                    else track.confidence
                ),
            )
            for track in tracks
        ]
        confident = [
            track
            for track in normalized
            if track.confidence >= self.config.min_role_confidence
        ]
        children = [track for track in confident if track.role == "child"]
        adults = [track for track in confident if track.role == "adult"]
        uncertain_count = sum(track.role == "uncertain" for track in normalized)

        child_results = tuple(
            self._evaluate_child(child, adults) for child in children
        )
        raw_state, reason = self._scene_state(
            child_results=child_results,
            adult_count=len(adults),
            uncertain_count=uncertain_count,
        )
        stable_state = self._stabilize(raw_state)
        return CompanionshipResult(raw_state, stable_state, child_results, reason)

    def _evaluate_child(
        self,
        child: CompanionTrack,
        adults: list[CompanionTrack],
    ) -> ChildCompanionship:
        adults_by_id = {adult.track_id: adult for adult in adults}
        bound_adult_id = self._bound_adults.get(child.track_id)
        if bound_adult_id is not None:
            return self._evaluate_bound_child(
                child,
                adults_by_id.get(bound_adult_id),
                bound_adult_id,
            )

        nearest_adult: CompanionTrack | None = None
        nearest_ratio: float | None = None
        for adult in adults:
            ratio = self._distance_ratio(child, adult)
            if nearest_ratio is None or ratio < nearest_ratio:
                nearest_adult = adult
                nearest_ratio = ratio

        if nearest_ratio is None:
            self._adult_missing_frames[child.track_id] += 1
            state = (
                "UNCERTAIN"
                if self._adult_missing_frames[child.track_id]
                <= self.config.unbound_child_alone_grace_frames
                else "CHILD_ALONE"
            )
        else:
            if nearest_ratio <= self.config.adult_near_distance_ratio:
                self._adult_missing_frames[child.track_id] = 0
                pair = (child.track_id, nearest_adult.track_id)
                self._clear_child_pair_candidates(child.track_id, except_pair=pair)
                self._near_pair_frames[pair] += 1
                if (
                    self._near_pair_frames[pair]
                    >= self.config.relationship_confirmation_frames
                ):
                    self._bound_adults[child.track_id] = nearest_adult.track_id
                    state = "ACCOMPANIED"
                else:
                    state = "UNCERTAIN"
            else:
                self._adult_missing_frames[child.track_id] += 1
                self._clear_child_pair_candidates(child.track_id)
                state = (
                    "UNCERTAIN"
                    if self._adult_missing_frames[child.track_id]
                    <= self.config.unbound_child_alone_grace_frames
                    else "CHILD_ALONE"
                )

        return ChildCompanionship(
            child_track_id=child.track_id,
            state=state,
            nearest_adult_track_id=(
                nearest_adult.track_id if nearest_adult is not None else None
            ),
            nearest_adult_distance_ratio=nearest_ratio,
        )

    def _evaluate_bound_child(
        self,
        child: CompanionTrack,
        adult: CompanionTrack | None,
        adult_track_id: int,
    ) -> ChildCompanionship:
        if adult is None:
            self._adult_missing_frames[child.track_id] += 1
            state = (
                "UNCERTAIN"
                if self._adult_missing_frames[child.track_id]
                <= self.config.adult_occlusion_grace_frames
                else "CHILD_SEPARATED"
            )
            ratio = None
        else:
            self._adult_missing_frames[child.track_id] = 0
            ratio = self._distance_ratio(child, adult)
            if ratio <= self.config.adult_near_distance_ratio:
                state = "ACCOMPANIED"
            elif ratio >= self.config.adult_separated_distance_ratio:
                state = "CHILD_SEPARATED"
            else:
                state = "UNCERTAIN"

        return ChildCompanionship(
            child_track_id=child.track_id,
            state=state,
            nearest_adult_track_id=adult_track_id,
            nearest_adult_distance_ratio=ratio,
        )

    @staticmethod
    def _distance_ratio(child: CompanionTrack, adult: CompanionTrack) -> float:
        distance = hypot(
            child.footpoint[0] - adult.footpoint[0],
            child.footpoint[1] - adult.footpoint[1],
        )
        return distance / child.height

    def _clear_child_pair_candidates(
        self,
        child_track_id: int,
        *,
        except_pair: tuple[int, int] | None = None,
    ) -> None:
        for pair in tuple(self._near_pair_frames):
            if pair[0] == child_track_id and pair != except_pair:
                self._near_pair_frames.pop(pair, None)

    @staticmethod
    def _scene_state(
        *,
        child_results: tuple[ChildCompanionship, ...],
        adult_count: int,
        uncertain_count: int,
    ) -> tuple[str, str]:
        child_states = {child.state for child in child_results}
        if "CHILD_ALONE" in child_states:
            return "CHILD_ALONE", "Crianca persistente sem adulto visivel."
        if "CHILD_SEPARATED" in child_states:
            return "CHILD_SEPARATED", "Crianca persistentemente distante do adulto."
        if "ACCOMPANIED" in child_states and child_states == {"ACCOMPANIED"}:
            return "ACCOMPANIED", "Todas as criancas possuem adulto proximo."
        if child_results:
            return "UNCERTAIN", "Companhia infantil insuficiente ou conflitante."
        if adult_count and not uncertain_count:
            return "NO_CHILD", "Somente adultos confiaveis detectados."
        return "UNCERTAIN", "Sem evidencia infantil ou adulta suficiente."

    def _stabilize(self, raw_state: str) -> str:
        if raw_state not in VALID_STATES:
            raise ValueError(f"Estado pediatrico invalido: {raw_state}")
        if raw_state == self._stable_state:
            self._pending_state = None
            self._pending_count = 0
            return self._stable_state
        if raw_state != self._pending_state:
            self._pending_state = raw_state
            self._pending_count = 1
            if self.config.state_persistence_frames <= 1:
                self._stable_state = raw_state
                self._pending_state = None
                self._pending_count = 0
            return self._stable_state
        self._pending_count += 1
        if self._pending_count >= self.config.state_persistence_frames:
            self._stable_state = raw_state
            self._pending_state = None
            self._pending_count = 0
        return self._stable_state
