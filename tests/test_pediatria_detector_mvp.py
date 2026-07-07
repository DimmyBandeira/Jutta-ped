from modulo.pediatria.detector_mvp import (
    PediatricDetection,
    bbox_iou,
    resolve_role_conflicts,
)


def _detection(role: str, confidence: float, *, x: float = 0.0) -> PediatricDetection:
    return PediatricDetection((x, 0.0, x + 100.0, 200.0), role, confidence, 1)


def test_bbox_iou_handles_overlap_and_disjoint_boxes() -> None:
    assert bbox_iou((0, 0, 10, 10), (0, 0, 10, 10)) == 1.0
    assert bbox_iou((0, 0, 10, 10), (20, 20, 30, 30)) == 0.0


def test_close_child_adult_conflict_becomes_uncertain() -> None:
    resolved = resolve_role_conflicts(
        [_detection("adult", 0.85), _detection("child", 0.74)]
    )

    assert len(resolved) == 1
    assert resolved[0].role == "uncertain"


def test_clear_role_wins_over_overlapping_role() -> None:
    resolved = resolve_role_conflicts(
        [_detection("child", 0.90), _detection("adult", 0.50)]
    )

    assert len(resolved) == 1
    assert resolved[0].role == "child"


def test_separate_people_are_preserved() -> None:
    resolved = resolve_role_conflicts(
        [_detection("child", 0.90), _detection("adult", 0.80, x=300.0)]
    )

    assert {item.role for item in resolved} == {"child", "adult"}
