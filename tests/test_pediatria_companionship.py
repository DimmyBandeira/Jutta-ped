import pytest

from modulo.pediatria.companionship import (
    CompanionTrack,
    CompanionshipAnalyzer,
    role_from_evidence,
)
from modulo.pediatria.config import CompanionshipConfig
from modulo.pediatria.contracts import VisualChildSignal


def _track(
    track_id: int,
    *,
    x: float,
    role: str,
    confidence: float = 0.9,
    height: float = 100.0,
) -> CompanionTrack:
    return CompanionTrack(
        track_id=track_id,
        bbox_xyxy=(x, 0.0, x + 40.0, height),
        role=role,
        confidence=confidence,
    )


def _analyzer(**overrides: object) -> CompanionshipAnalyzer:
    return CompanionshipAnalyzer(
        CompanionshipConfig(
            relationship_confirmation_frames=1,
            state_persistence_frames=1,
            adult_occlusion_grace_frames=0,
            **overrides,
        )
    )


def test_classifies_child_near_adult_as_accompanied() -> None:
    result = _analyzer().observe(
        [_track(1, x=0, role="child"), _track(2, x=80, role="adult")]
    )

    assert result.raw_state == "ACCOMPANIED"
    assert result.stable_state == "ACCOMPANIED"
    assert result.children[0].nearest_adult_track_id == 2


def test_classifies_child_far_from_adult_as_separated() -> None:
    analyzer = _analyzer()
    analyzer.observe([_track(1, x=0, role="child"), _track(2, x=80, role="adult")])
    result = analyzer.observe(
        [_track(1, x=0, role="child"), _track(2, x=400, role="adult")]
    )

    assert result.raw_state == "CHILD_SEPARATED"
    assert result.children[0].nearest_adult_distance_ratio == pytest.approx(4.0)


def test_does_not_call_far_unbound_adult_a_separation() -> None:
    analyzer = CompanionshipAnalyzer(
        CompanionshipConfig(
            relationship_confirmation_frames=1,
            state_persistence_frames=1,
            adult_occlusion_grace_frames=2,
        )
    )
    result = analyzer.observe(
        [_track(1, x=0, role="child"), _track(2, x=400, role="adult")]
    )

    assert result.raw_state == "UNCERTAIN"


def test_far_unbound_adult_becomes_child_alone_after_grace() -> None:
    analyzer = CompanionshipAnalyzer(
        CompanionshipConfig(
            state_persistence_frames=1,
            adult_occlusion_grace_frames=2,
            unbound_child_alone_grace_frames=2,
        )
    )
    tracks = [_track(1, x=0, role="child"), _track(2, x=400, role="adult")]

    assert analyzer.observe(tracks).raw_state == "UNCERTAIN"
    assert analyzer.observe(tracks).raw_state == "UNCERTAIN"
    assert analyzer.observe(tracks).raw_state == "CHILD_ALONE"


def test_separation_tracks_the_bound_adult_not_an_unrelated_near_adult() -> None:
    analyzer = _analyzer()
    analyzer.observe([_track(1, x=0, role="child"), _track(2, x=80, role="adult")])

    result = analyzer.observe(
        [
            _track(1, x=0, role="child"),
            _track(2, x=400, role="adult"),
            _track(3, x=60, role="adult"),
        ]
    )

    assert result.raw_state == "CHILD_SEPARATED"
    assert result.children[0].nearest_adult_track_id == 2


def test_bound_child_role_survives_temporary_adult_classification() -> None:
    analyzer = _analyzer()
    analyzer.observe([_track(1, x=0, role="child"), _track(2, x=80, role="adult")])

    result = analyzer.observe(
        [_track(1, x=0, role="adult", confidence=0.3), _track(2, x=400, role="adult")]
    )

    assert result.raw_state == "CHILD_SEPARATED"
    assert result.children[0].child_track_id == 1


def test_missing_bound_adult_becomes_separation_after_grace() -> None:
    analyzer = CompanionshipAnalyzer(
        CompanionshipConfig(
            relationship_confirmation_frames=1,
            state_persistence_frames=1,
            adult_occlusion_grace_frames=2,
        )
    )
    analyzer.observe([_track(1, x=0, role="child"), _track(2, x=80, role="adult")])

    assert analyzer.observe([_track(1, x=0, role="adult")]).raw_state == "UNCERTAIN"
    assert analyzer.observe([_track(1, x=0, role="adult")]).raw_state == "UNCERTAIN"
    assert (
        analyzer.observe([_track(1, x=0, role="adult")]).raw_state
        == "CHILD_SEPARATED"
    )


def test_classifies_child_without_adult_after_occlusion_grace_as_alone() -> None:
    analyzer = CompanionshipAnalyzer(
        CompanionshipConfig(
            state_persistence_frames=1,
            adult_occlusion_grace_frames=2,
            unbound_child_alone_grace_frames=2,
        )
    )

    assert analyzer.observe([_track(1, x=0, role="child")]).raw_state == "UNCERTAIN"
    assert analyzer.observe([_track(1, x=0, role="child")]).raw_state == "UNCERTAIN"
    assert analyzer.observe([_track(1, x=0, role="child")]).raw_state == "CHILD_ALONE"


def test_low_confidence_roles_do_not_create_false_state() -> None:
    result = _analyzer().observe(
        [
            _track(1, x=0, role="child", confidence=0.4),
            _track(2, x=80, role="adult", confidence=0.4),
        ]
    )

    assert result.raw_state == "UNCERTAIN"


def test_state_requires_configured_persistence() -> None:
    analyzer = CompanionshipAnalyzer(
        CompanionshipConfig(
            relationship_confirmation_frames=1,
            state_persistence_frames=2,
            adult_occlusion_grace_frames=0,
        )
    )
    tracks = [_track(1, x=0, role="child"), _track(2, x=80, role="adult")]

    assert analyzer.observe(tracks).stable_state == "UNCERTAIN"
    assert analyzer.observe(tracks).stable_state == "ACCOMPANIED"


def test_rejects_invalid_bbox_and_role() -> None:
    with pytest.raises(ValueError, match="BBox"):
        CompanionTrack(1, (0.0, 0.0, 0.0, 10.0), "child", 0.9)
    with pytest.raises(ValueError, match="Role"):
        CompanionTrack(1, (0.0, 0.0, 10.0, 10.0), "visitor", 0.9)


def test_role_prefers_valid_visual_evidence_and_falls_back_to_score() -> None:
    visual = VisualChildSignal(0.9, 0.05, 0.05, 0.9, "child")
    assert role_from_evidence(
        stable_score=0.1,
        diagnostic_hint="PROPOSED_ADULT",
        visual=visual,
    ) == ("child", 0.9)
    assert role_from_evidence(
        stable_score=0.8,
        diagnostic_hint="PROPOSED_CHILD",
    ) == ("child", 0.8)
    assert role_from_evidence(
        stable_score=None,
        diagnostic_hint="INSUFFICIENT",
    ) == ("uncertain", 0.0)
