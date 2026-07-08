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

_KEEP_CURRENT = object()

# Perfil "CPU economico": detector roda a cada 2 frames analisados e o
# especialista tem teto de 3 classificacoes por frame. Usado tanto pela UI
# (start_analysis / fallback GPU->CPU) quanto pelo servico headless
# (PediatriaHeadlessSession), para as duas camadas nao divergirem.
_CPU_DETECTOR_STRIDE_FRAMES = 2
_CPU_SPECIALIST_BUDGET_PER_FRAME = 3


def cpu_economical_overrides(device: str) -> dict[str, int | None]:
    """Cadencias de detector/especialista recomendadas para o device.

    CPU ganha stride>1 e teto de especialista; qualquer outro device
    (cuda:*, etc.) mantem o comportamento anterior (sem stride, sem teto).
    """
    if str(device or "").strip().lower() == "cpu":
        return {
            "detector_stride_frames": _CPU_DETECTOR_STRIDE_FRAMES,
            "specialist_budget_per_frame": _CPU_SPECIALIST_BUDGET_PER_FRAME,
        }
    return {"detector_stride_frames": 1, "specialist_budget_per_frame": None}


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
    # Detector de pessoa roda a cada N frames analisados (1 = todo frame).
    # Em CPU, >1 reduz o custo dominante do pipeline sem desligar o crop.
    detector_stride_frames: int = 1
    # Teto de chamadas ao especialista por frame analisado. None = sem teto
    # (comportamento anterior). Em cenas cheias evita gastar CPU igualmente
    # em todo track elegivel; o excedente e adiado e priorizado depois.
    specialist_budget_per_frame: int | None = None


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
        # Tracks relevantes para alerta na ultima observacao (crianca
        # sozinha/separada/incerta e o adulto mais proximo dela). Usado para
        # priorizar o orcamento do especialista no PROXIMO frame: o que
        # quase virou alerta nao pode ficar sem reclassificar por budget.
        self._priority_track_ids: set[int] = set()

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
        self._priority_track_ids = set()
        if self.crop_pipeline is not None:
            self.crop_pipeline.reset()

    def set_device(
        self,
        device: str,
        *,
        detector_stride_frames: int | None = None,
        specialist_budget_per_frame: Any = _KEEP_CURRENT,
    ) -> None:
        """Troca o device do modelo. Opcionalmente reajusta as cadencias de
        detector/especialista (ex.: fallback GPU->CPU deve adotar o perfil
        economico de CPU, nao manter os valores do device anterior).

        `specialist_budget_per_frame` aceita `None` como valor real ("sem
        teto"), entao usa um sentinel proprio para distinguir de
        "nao alterar" no default.
        """
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
            detector_stride_frames=(
                detector_stride_frames
                if detector_stride_frames is not None
                else self.config.detector_stride_frames
            ),
            specialist_budget_per_frame=(
                self.config.specialist_budget_per_frame
                if specialist_budget_per_frame is _KEEP_CURRENT
                else specialist_budget_per_frame
            ),
        )

    _ALERT_RISK_STATES = {"CHILD_ALONE", "CHILD_SEPARATED", "UNCERTAIN"}

    @classmethod
    def _extract_priority_track_ids(cls, state: Any) -> set[int]:
        """Tracks que merecem o especialista primeiro no PROXIMO frame.

        Crianca em estado de risco (sozinha/separada/incerta) e o adulto
        mais proximo dela nao podem ficar sem reclassificar so porque o
        orcamento do especialista foi curto nessa cena.
        """
        priority: set[int] = set()
        for child in getattr(state, "children", []):
            if child.state not in cls._ALERT_RISK_STATES:
                continue
            priority.add(child.child_track_id)
            if child.nearest_adult_track_id is not None:
                priority.add(child.nearest_adult_track_id)
        return priority

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
                detector_stride_frames=self.config.detector_stride_frames,
                specialist_budget_per_frame=self.config.specialist_budget_per_frame,
                priority_track_ids=self._priority_track_ids,
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
        self._priority_track_ids = self._extract_priority_track_ids(state)
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
