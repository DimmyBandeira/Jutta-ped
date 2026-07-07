from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from modulo.pediatria.companionship import CompanionTrack, CompanionshipAnalyzer
from modulo.pediatria.detector_mvp import (
    PediatricDetection,
    PediatricRoleContextAdjuster,
    PediatricsDetectorMvpRunner,
    resolve_role_conflicts,
)


@dataclass(frozen=True)
class PediatriaServiceConfig:
    specialist_model_path: Path
    person_model_path: Path
    confidence: float = 0.25
    device: str = "cpu"
    person_crop_pipeline: bool = True
    person_confidence: float = 0.20
    crop_cache_frames: int = 12
    weak_child_candidates: bool = True
    weak_child_confidence: float = 0.03


@dataclass(frozen=True)
class PediatriaFrameResult:
    parsed_detections: list[PediatricDetection]
    person_detections: list[PediatricDetection]
    resolved_detections: list[PediatricDetection]
    weak_child_detections: list[PediatricDetection]
    weak_child_promotions: list[PediatricDetection]
    state: Any
    latency_ms: float
    identity_merges: list[Any] = field(default_factory=list)
    weak_promotions: list[Any] = field(default_factory=list)


class PediatriaService:
    """Ponto central do pipeline de IA pediatrico, separado da UI Qt."""

    def __init__(
        self,
        config: PediatriaServiceConfig,
        *,
        runner_factory: Callable[[Path], PediatricsDetectorMvpRunner],
        crop_pipeline_factory: Callable[..., Any],
        identity_stabilizer_factory: Callable[[], Any],
        weak_child_promoter_factory: Callable[[], Any],
        role_adjuster_factory: Callable[[], PediatricRoleContextAdjuster] = PediatricRoleContextAdjuster,
        analyzer_factory: Callable[[], CompanionshipAnalyzer] = CompanionshipAnalyzer,
    ) -> None:
        self.config = config
        self._runner_factory = runner_factory
        self._crop_pipeline_factory = crop_pipeline_factory
        self._identity_stabilizer_factory = identity_stabilizer_factory
        self._weak_child_promoter_factory = weak_child_promoter_factory
        self._role_adjuster_factory = role_adjuster_factory
        self._analyzer_factory = analyzer_factory
        self.runner: PediatricsDetectorMvpRunner | None = None
        self.crop_pipeline: Any | None = None
        self.role_adjuster = self._role_adjuster_factory()
        self.analyzer = self._analyzer_factory()
        self.identity_stabilizer = self._identity_stabilizer_factory()
        self.weak_child_promoter = self._weak_child_promoter_factory()

    @property
    def model_names(self) -> dict[int, str]:
        return self.runner.names if self.runner is not None else {}

    def start_session(self) -> None:
        self.runner = self._runner_factory(self.config.specialist_model_path)
        if self.config.person_crop_pipeline:
            if not self.config.person_model_path.exists():
                raise FileNotFoundError(
                    f"Detector de pessoa nao encontrado: {self.config.person_model_path}"
                )
            self.crop_pipeline = self._crop_pipeline_factory(
                person_model_path=self.config.person_model_path,
                specialist_model=self.runner.model,
                specialist_names=self.runner.names,
            )
        else:
            self.crop_pipeline = None
        self.reset_session_state()

    def reset_session_state(self) -> None:
        self.role_adjuster = self._role_adjuster_factory()
        self.analyzer = self._analyzer_factory()
        self.identity_stabilizer.reset()
        self.weak_child_promoter.reset()
        if self.crop_pipeline is not None:
            self.crop_pipeline.reset()

    def set_device(self, device: str) -> None:
        self.config = PediatriaServiceConfig(
            specialist_model_path=self.config.specialist_model_path,
            person_model_path=self.config.person_model_path,
            confidence=self.config.confidence,
            device=device,
            person_crop_pipeline=self.config.person_crop_pipeline,
            person_confidence=self.config.person_confidence,
            crop_cache_frames=self.config.crop_cache_frames,
            weak_child_candidates=self.config.weak_child_candidates,
            weak_child_confidence=self.config.weak_child_confidence,
        )

    def process_frame(self, frame: Any, *, frame_id: int) -> PediatriaFrameResult:
        if self.runner is None:
            raise RuntimeError("PediatriaService.start_session precisa ser chamado antes.")
        started = time.perf_counter()
        parsed_detections: list[PediatricDetection]
        person_detections: list[PediatricDetection]
        result = None
        crop_enabled = self.config.person_crop_pipeline and self.crop_pipeline is not None
        inference_confidence = (
            min(self.config.confidence, self.config.weak_child_confidence)
            if self.config.weak_child_candidates and not crop_enabled
            else self.config.confidence
        )
        if crop_enabled:
            parsed_detections, person_detections = self.crop_pipeline.detect_and_classify(
                frame,
                frame_index=frame_id,
                device=self.config.device,
                person_confidence=self.config.person_confidence,
                specialist_accept_confidence=self.config.confidence,
                cache_ttl_frames=self.config.crop_cache_frames,
            )
        else:
            result = self.runner.model.track(
                frame,
                persist=True,
                conf=inference_confidence,
                device=self.config.device,
                tracker="bytetrack.yaml",
                verbose=False,
            )[0]
            parsed_detections = self.runner._parse_result(result)
            person_detections = []
        normal_detections = [
            item for item in parsed_detections if item.confidence >= self.config.confidence
        ]
        weak_child_detections: list[PediatricDetection] = []
        weak_child_promoted: list[PediatricDetection] = []
        if self.config.weak_child_candidates:
            weak_child_detections = [
                item
                for item in parsed_detections
                if item.role == "child"
                and self.config.weak_child_confidence <= item.confidence < self.config.confidence
            ]
            adult_blockers = [
                item
                for item in parsed_detections
                if item.role == "adult" and item.confidence >= self.config.weak_child_confidence
            ]
            weak_child_promoted = self.weak_child_promoter.promote(
                weak_child_detections,
                adult_blockers,
                frame_index=frame_id,
            )
        resolved = self.role_adjuster.adjust(
            resolve_role_conflicts(normal_detections + weak_child_promoted),
            frame_index=frame_id,
        )
        resolved = self.identity_stabilizer.stabilize(
            resolved,
            frame,
            frame_index=frame_id,
        )
        best_by_track: dict[int, PediatricDetection] = {}
        for item in resolved:
            previous = best_by_track.get(item.track_id)
            if previous is None or item.confidence > previous.confidence:
                best_by_track[item.track_id] = item
        resolved = list(best_by_track.values())
        tracks = [
            CompanionTrack(item.track_id, item.bbox_xyxy, item.role, item.confidence)
            for item in resolved
        ]
        state = self.analyzer.observe(tracks)
        latency_ms = (time.perf_counter() - started) * 1000.0
        return PediatriaFrameResult(
            parsed_detections=parsed_detections,
            person_detections=person_detections,
            resolved_detections=resolved,
            weak_child_detections=weak_child_detections,
            weak_child_promotions=weak_child_promoted,
            state=state,
            latency_ms=latency_ms,
            identity_merges=list(self.identity_stabilizer.last_merges),
            weak_promotions=list(self.weak_child_promoter.last_promotions),
        )
