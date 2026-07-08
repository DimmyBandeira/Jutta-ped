from __future__ import annotations

import time
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .detector_mvp import PediatricDetection, bbox_iou
from .metrics import RunningStat


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
