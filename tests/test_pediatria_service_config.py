from types import SimpleNamespace

from src.jutta_ped.service.pediatria_service import (
    PediatriaService,
    PediatriaServiceConfig,
    cpu_economical_overrides,
)


def test_cpu_economical_overrides_reduces_detector_and_caps_specialist() -> None:
    overrides = cpu_economical_overrides("cpu")
    assert overrides["detector_stride_frames"] > 1
    assert overrides["specialist_budget_per_frame"] is not None
    assert overrides["specialist_budget_per_frame"] > 0


def test_cpu_economical_overrides_is_a_noop_for_gpu() -> None:
    overrides = cpu_economical_overrides("cuda:0")
    assert overrides == {"detector_stride_frames": 1, "specialist_budget_per_frame": None}


def test_config_defaults_preserve_previous_behavior() -> None:
    config = PediatriaServiceConfig(specialist_model_path="model.pt", person_model_path="person.pt")
    assert config.detector_stride_frames == 1
    assert config.specialist_budget_per_frame is None


def _child_companionship(state: str, child_track_id: int = 1, adult_track_id: int | None = 2):
    return SimpleNamespace(
        child_track_id=child_track_id,
        state=state,
        nearest_adult_track_id=adult_track_id,
        nearest_adult_distance_ratio=1.0,
    )


def test_extract_priority_track_ids_flags_child_alone_and_its_nearest_adult() -> None:
    state = SimpleNamespace(children=[_child_companionship("CHILD_ALONE", child_track_id=5, adult_track_id=9)])
    priority = PediatriaService._extract_priority_track_ids(state)
    assert priority == {5, 9}


def test_extract_priority_track_ids_ignores_accompanied_children() -> None:
    state = SimpleNamespace(children=[_child_companionship("ACCOMPANIED", child_track_id=5, adult_track_id=9)])
    priority = PediatriaService._extract_priority_track_ids(state)
    assert priority == set()


def test_extract_priority_track_ids_handles_missing_adult() -> None:
    state = SimpleNamespace(children=[_child_companionship("UNCERTAIN", child_track_id=7, adult_track_id=None)])
    priority = PediatriaService._extract_priority_track_ids(state)
    assert priority == {7}
