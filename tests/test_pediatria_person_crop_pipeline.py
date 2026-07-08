from modulo.pediatria.crop_pipeline import PersonCropSpecialistPipeline, TrackClassificationState
from modulo.pediatria.detector_mvp import PediatricDetection
from modulo.pediatria.metrics import RunningStat


def _make_pipeline() -> PersonCropSpecialistPipeline:
    """Constroi o pipeline sem carregar modelos YOLO reais.

    A logica de gate/cache (_gate, _effective_ttl, _update_cache, ...) so
    depende de `_cache`/`last_trace`/`stats`; os modelos ficam None e sao
    trocados por stubs nos testes que exercitam `detect_and_classify`.
    """
    pipeline = PersonCropSpecialistPipeline.__new__(PersonCropSpecialistPipeline)
    pipeline.person_model_path = None
    pipeline.person_model = None
    pipeline.specialist_model = None
    pipeline.specialist_names = {0: "adult", 1: "child"}
    pipeline._cache = {}
    pipeline._last_persons = None
    pipeline._last_detector_frame = None
    pipeline.last_trace = {}
    pipeline.last_frame_meta = {}
    pipeline.stats = __import__("collections").Counter()
    pipeline.detector_ms = RunningStat()
    pipeline.specialist_ms = RunningStat()
    return pipeline


def _person(track_id: int = 1, bbox=(100.0, 100.0, 160.0, 260.0), confidence: float = 0.8) -> PediatricDetection:
    return PediatricDetection(bbox, "person", confidence, track_id)


def test_gate_forces_specialist_for_new_track() -> None:
    pipeline = _make_pipeline()
    decision, reason = pipeline._gate(_person(), None, frame_index=1, base_ttl_frames=12)
    assert (decision, reason) == ("classify", "new_track")


def test_gate_skips_small_crop_without_touching_specialist() -> None:
    pipeline = _make_pipeline()
    tiny_person = _person(bbox=(10.0, 10.0, 20.0, 20.0))
    decision, reason = pipeline._gate(tiny_person, None, frame_index=1, base_ttl_frames=12)
    assert (decision, reason) == ("skip_cheap", "crop_too_small")


def test_gate_reuses_cache_when_track_is_stable() -> None:
    pipeline = _make_pipeline()
    state = TrackClassificationState(
        track_id=1,
        bbox_xyxy=(100.0, 100.0, 160.0, 260.0),
        role="adult",
        confidence=0.9,
        last_classified_frame=10,
        last_seen_frame=10,
        stable_hits=1,
    )
    decision, reason = pipeline._gate(_person(bbox=state.bbox_xyxy), state, frame_index=12, base_ttl_frames=12)
    assert (decision, reason) == ("reuse", None)


def test_gate_forces_refresh_on_bbox_shift() -> None:
    pipeline = _make_pipeline()
    state = TrackClassificationState(
        track_id=1,
        bbox_xyxy=(100.0, 100.0, 160.0, 260.0),
        role="adult",
        confidence=0.9,
        last_classified_frame=10,
        last_seen_frame=10,
        stable_hits=1,
    )
    shifted = _person(bbox=(400.0, 400.0, 460.0, 560.0))
    decision, reason = pipeline._gate(shifted, state, frame_index=11, base_ttl_frames=12)
    assert (decision, reason) == ("classify", "bbox_shifted")


def test_gate_forces_refresh_on_size_change() -> None:
    pipeline = _make_pipeline()
    state = TrackClassificationState(
        track_id=1,
        bbox_xyxy=(100.0, 100.0, 160.0, 260.0),
        role="adult",
        confidence=0.9,
        last_classified_frame=10,
        last_seen_frame=10,
        stable_hits=1,
    )
    # Mesmo centro e IoU ainda acima do minimo de reuso, mas area cresceu ~70%
    # (pessoa se aproximou da camera) -> deve disparar refresh por tamanho.
    grown = _person(bbox=(91.0, 76.0, 169.0, 284.0))
    decision, reason = pipeline._gate(grown, state, frame_index=11, base_ttl_frames=12)
    assert (decision, reason) == ("classify", "size_changed")


def test_gate_forces_refresh_when_pending_refresh_flag_set() -> None:
    pipeline = _make_pipeline()
    state = TrackClassificationState(
        track_id=1,
        bbox_xyxy=(100.0, 100.0, 160.0, 260.0),
        role="uncertain",
        confidence=0.2,
        last_classified_frame=10,
        last_seen_frame=10,
        pending_refresh=True,
    )
    decision, reason = pipeline._gate(_person(bbox=state.bbox_xyxy), state, frame_index=11, base_ttl_frames=12)
    assert (decision, reason) == ("classify", "weak_previous_classification")


def test_gate_forces_refresh_on_reappearance_gap() -> None:
    pipeline = _make_pipeline()
    state = TrackClassificationState(
        track_id=1,
        bbox_xyxy=(100.0, 100.0, 160.0, 260.0),
        role="adult",
        confidence=0.9,
        last_classified_frame=1,
        last_seen_frame=1,
        stable_hits=1,
    )
    decision, reason = pipeline._gate(
        _person(bbox=state.bbox_xyxy),
        state,
        frame_index=1 + PersonCropSpecialistPipeline.REAPPEAR_GAP_FRAMES + 1,
        base_ttl_frames=12,
    )
    assert (decision, reason) == ("classify", "track_reappeared")


def test_effective_ttl_is_short_for_child_or_weak_state() -> None:
    pipeline = _make_pipeline()
    child_state = TrackClassificationState(
        track_id=1,
        bbox_xyxy=(0.0, 0.0, 50.0, 100.0),
        role="child",
        confidence=0.8,
        last_classified_frame=0,
        last_seen_frame=0,
    )
    assert pipeline._effective_ttl(child_state, base_ttl_frames=48) == PersonCropSpecialistPipeline.CHILD_OR_WEAK_TTL_FRAMES


def test_effective_ttl_extends_for_stable_adult() -> None:
    pipeline = _make_pipeline()
    stable_adult = TrackClassificationState(
        track_id=1,
        bbox_xyxy=(0.0, 0.0, 50.0, 100.0),
        role="adult",
        confidence=0.9,
        last_classified_frame=0,
        last_seen_frame=0,
        stable_hits=PersonCropSpecialistPipeline.STABLE_ADULT_HITS_REQUIRED,
    )
    assert pipeline._effective_ttl(stable_adult, base_ttl_frames=12) == PersonCropSpecialistPipeline.STABLE_ADULT_TTL_FRAMES


def test_update_cache_increments_stable_hits_for_consecutive_confirmed_adult() -> None:
    pipeline = _make_pipeline()
    person = _person()
    state = pipeline._update_cache(person, "adult", 0.9, frame_index=1, previous=None)
    assert state.stable_hits == 1
    state = pipeline._update_cache(person, "adult", 0.9, frame_index=2, previous=state)
    assert state.stable_hits == 2


def test_update_cache_resets_stable_hits_when_role_changes() -> None:
    pipeline = _make_pipeline()
    person = _person()
    state = pipeline._update_cache(person, "adult", 0.9, frame_index=1, previous=None)
    assert state.stable_hits == 1
    state = pipeline._update_cache(person, "child", 0.7, frame_index=2, previous=state)
    assert state.stable_hits == 0
    assert state.pending_refresh is False


def test_update_cache_marks_pending_refresh_for_weak_result() -> None:
    pipeline = _make_pipeline()
    person = _person()
    state = pipeline._update_cache(person, "uncertain", 0.1, frame_index=1, previous=None)
    assert state.pending_refresh is True


def test_detect_and_classify_reduces_specialist_calls_for_a_stable_track(monkeypatch) -> None:
    """Fim a fim: uma pessoa parada/estavel por 60 frames deve gerar bem
    menos que 60 chamadas ao especialista (medida do ganho da rodada)."""
    pipeline = _make_pipeline()
    pipeline.person_model = object()
    fixed_person = _person(bbox=(100.0, 100.0, 160.0, 260.0), confidence=0.9)

    monkeypatch.setattr(pipeline, "_parse_person_result", lambda result: [fixed_person])

    specialist_calls = {"count": 0}

    def fake_classify_crop(crop, *, device, accept_confidence):
        specialist_calls["count"] += 1
        return "adult", 0.9

    monkeypatch.setattr(pipeline, "_classify_crop", fake_classify_crop)
    monkeypatch.setattr(
        "modulo.pediatria.crop_pipeline.crop_frame",
        lambda frame, bbox: object(),
    )

    total_frames = 60
    for frame_index in range(1, total_frames + 1):
        pipeline.person_model = type("FakeModel", (), {"track": lambda self, *a, **k: [None]})()
        pipeline.detect_and_classify(
            frame=object(),
            frame_index=frame_index,
            device="cpu",
            person_confidence=0.2,
            specialist_accept_confidence=0.3,
            cache_ttl_frames=12,
        )

    assert specialist_calls["count"] < total_frames // 3
    assert pipeline.stats["specialist_inference"] == specialist_calls["count"]
    assert pipeline.stats["cache_reuse"] > 0


def test_detect_and_classify_populates_last_trace_with_source_and_reason(monkeypatch) -> None:
    pipeline = _make_pipeline()
    fixed_person = _person()
    monkeypatch.setattr(pipeline, "_parse_person_result", lambda result: [fixed_person])
    monkeypatch.setattr(pipeline, "_classify_crop", lambda crop, **kwargs: ("adult", 0.9))
    monkeypatch.setattr("modulo.pediatria.crop_pipeline.crop_frame", lambda frame, bbox: object())
    pipeline.person_model = type("FakeModel", (), {"track": lambda self, *a, **k: [None]})()

    pipeline.detect_and_classify(
        frame=object(),
        frame_index=1,
        device="cpu",
        person_confidence=0.2,
        specialist_accept_confidence=0.3,
        cache_ttl_frames=12,
    )

    trace = pipeline.last_trace[fixed_person.track_id]
    assert trace["source"] == "specialist_inference"
    assert trace["refresh_reason"] == "new_track"


def _fake_model(track_calls: list[int]):
    def track(self, *args, **kwargs):
        track_calls.append(1)
        return [None]

    return type("FakeModel", (), {"track": track})()


def test_detector_stride_skips_person_model_track_on_intermediate_frames(monkeypatch) -> None:
    pipeline = _make_pipeline()
    fixed_person = _person()
    monkeypatch.setattr(pipeline, "_parse_person_result", lambda result: [fixed_person])
    monkeypatch.setattr(pipeline, "_classify_crop", lambda crop, **kwargs: ("adult", 0.9))
    monkeypatch.setattr("modulo.pediatria.crop_pipeline.crop_frame", lambda frame, bbox: object())

    track_calls: list[int] = []
    pipeline.person_model = _fake_model(track_calls)

    reused_flags = []
    for frame_index in range(1, 7):  # stride=3 -> detector roda nos frames 1 e 4
        pipeline.detect_and_classify(
            frame=object(),
            frame_index=frame_index,
            device="cpu",
            person_confidence=0.2,
            specialist_accept_confidence=0.3,
            cache_ttl_frames=12,
            detector_stride_frames=3,
        )
        reused_flags.append(pipeline.last_frame_meta["detector_reused_frame"])

    assert len(track_calls) == 2  # frames 1 e 4 apenas
    assert reused_flags == [False, True, True, False, True, True]


def test_specialist_budget_limits_calls_and_defers_the_rest(monkeypatch) -> None:
    pipeline = _make_pipeline()
    people = [_person(track_id=i, bbox=(i * 200.0, 100.0, i * 200.0 + 60.0, 260.0)) for i in range(1, 6)]
    monkeypatch.setattr(pipeline, "_parse_person_result", lambda result: people)
    monkeypatch.setattr(pipeline, "_classify_crop", lambda crop, **kwargs: ("adult", 0.9))
    monkeypatch.setattr("modulo.pediatria.crop_pipeline.crop_frame", lambda frame, bbox: object())
    pipeline.person_model = _fake_model([])

    pipeline.detect_and_classify(
        frame=object(),
        frame_index=1,
        device="cpu",
        person_confidence=0.2,
        specialist_accept_confidence=0.3,
        cache_ttl_frames=12,
        specialist_budget_per_frame=2,
    )

    assert pipeline.stats["specialist_inference"] == 2
    assert pipeline.stats["specialist_deferred_by_budget"] == 3
    assert pipeline.last_frame_meta["specialist_eligible_count"] == 5
    assert pipeline.last_frame_meta["specialist_budget_used"] == 2
    assert pipeline.last_frame_meta["specialist_deferred_count"] == 3
    deferred_traces = [t for t in pipeline.last_trace.values() if t["source"] == "specialist_deferred_by_budget"]
    assert len(deferred_traces) == 3
    assert all(t["specialist_deferred_by_budget"] for t in deferred_traces)


def test_priority_track_ids_win_budget_over_plain_new_tracks(monkeypatch) -> None:
    pipeline = _make_pipeline()
    # track 1 fica "velho" (ttl_expired) mas marcado como prioritario;
    # tracks 2 e 3 sao novos (rank melhor por padrao) mas sem prioridade.
    pipeline._cache[1] = TrackClassificationState(
        track_id=1,
        bbox_xyxy=(100.0, 100.0, 160.0, 260.0),
        role="adult",
        confidence=0.9,
        last_classified_frame=0,
        last_seen_frame=0,
    )
    people = [
        _person(track_id=1, bbox=(100.0, 100.0, 160.0, 260.0)),
        _person(track_id=2, bbox=(400.0, 100.0, 460.0, 260.0)),
        _person(track_id=3, bbox=(700.0, 100.0, 760.0, 260.0)),
    ]
    monkeypatch.setattr(pipeline, "_parse_person_result", lambda result: people)
    monkeypatch.setattr(pipeline, "_classify_crop", lambda crop, **kwargs: ("adult", 0.9))
    monkeypatch.setattr("modulo.pediatria.crop_pipeline.crop_frame", lambda frame, bbox: object())
    pipeline.person_model = _fake_model([])

    pipeline.detect_and_classify(
        frame=object(),
        frame_index=100,  # bem depois do TTL base -> track 1 vira "ttl_expired"
        device="cpu",
        person_confidence=0.2,
        specialist_accept_confidence=0.3,
        cache_ttl_frames=12,
        specialist_budget_per_frame=1,
        priority_track_ids={1},
    )

    assert pipeline.last_trace[1]["source"] == "specialist_inference"
    assert pipeline.last_trace[2]["source"] == "specialist_deferred_by_budget"
    assert pipeline.last_trace[3]["source"] == "specialist_deferred_by_budget"
