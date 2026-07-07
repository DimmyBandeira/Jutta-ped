from modulo.pediatria.detector_mvp import (
    PediatricDetection,
    PediatricRoleContextAdjuster,
)


def _det(
    track_id: int,
    role: str,
    confidence: float,
    bbox: tuple[float, float, float, float],
) -> PediatricDetection:
    return PediatricDetection(bbox, role, confidence, track_id)


def test_role_context_preserves_child_memory_for_same_track() -> None:
    adjuster = PediatricRoleContextAdjuster(
        child_memory_min_confidence=0.30,
        child_memory_min_hits=2,
        child_memory_ttl_frames=30,
    )

    adjuster.adjust([_det(13, "child", 0.34, (10, 10, 60, 140))], frame_index=1)
    adjuster.adjust([_det(13, "child", 0.47, (10, 10, 60, 140))], frame_index=2)
    adjusted = adjuster.adjust(
        [_det(13, "adult", 0.78, (12, 10, 62, 140))],
        frame_index=3,
    )

    assert adjusted[0].role == "child"
    assert adjusted[0].confidence >= 0.62


def test_role_context_promotes_small_same_depth_adult_candidate() -> None:
    adjuster = PediatricRoleContextAdjuster()

    adjusted = adjuster.adjust(
        [
            _det(1, "adult", 0.82, (10, 20, 110, 220)),
            _det(8, "adult", 0.70, (240, 105, 300, 220)),
        ],
        frame_index=10,
    )

    assert [item.role for item in adjusted] == ["adult", "child"]


def test_role_context_does_not_promote_small_candidate_at_different_depth() -> None:
    adjuster = PediatricRoleContextAdjuster()

    adjusted = adjuster.adjust(
        [
            _det(1, "adult", 0.82, (10, 20, 110, 420)),
            _det(8, "adult", 0.70, (240, 105, 300, 220)),
        ],
        frame_index=10,
    )

    assert [item.role for item in adjusted] == ["adult", "adult"]


def test_role_context_recovers_child_after_track_id_changes() -> None:
    adjuster = PediatricRoleContextAdjuster(enable_scene_scale_promotion=True)

    seed = adjuster.adjust(
        [
            _det(9, "adult", 0.82, (600, 80, 710, 244)),
            _det(8, "adult", 0.72, (760, 150, 820, 242)),
        ],
        frame_index=10,
    )
    adjusted = adjuster.adjust(
        [_det(21, "adult", 0.78, (750, 130, 832, 238))],
        frame_index=40,
    )

    assert [item.role for item in seed] == ["adult", "child"]
    assert adjusted[0].role == "child"


def test_role_context_does_not_promote_larger_adult_from_scene_memory() -> None:
    adjuster = PediatricRoleContextAdjuster(enable_scene_scale_promotion=True)

    adjuster.adjust([_det(8, "child", 0.72, (760, 150, 820, 242))], frame_index=10)
    adjuster.adjust([_det(8, "child", 0.76, (758, 148, 822, 244))], frame_index=11)
    adjusted = adjuster.adjust(
        [_det(21, "adult", 0.82, (720, 70, 850, 240))],
        frame_index=40,
    )

    assert adjusted[0].role == "adult"


def test_role_context_expires_scene_scale_memory() -> None:
    adjuster = PediatricRoleContextAdjuster(
        scene_memory_ttl_frames=10,
        enable_scene_scale_promotion=True,
    )

    adjuster.adjust([_det(8, "child", 0.72, (760, 150, 820, 242))], frame_index=1)
    adjuster.adjust([_det(8, "child", 0.76, (758, 148, 822, 244))], frame_index=2)
    adjusted = adjuster.adjust(
        [_det(21, "adult", 0.78, (750, 130, 832, 238))],
        frame_index=20,
    )

    assert adjusted[0].role == "adult"


def test_role_context_does_not_reuse_stale_track_id_at_another_depth() -> None:
    adjuster = PediatricRoleContextAdjuster(child_memory_ttl_frames=5)

    adjuster.adjust([_det(8, "child", 0.72, (760, 150, 820, 242))], frame_index=1)
    adjuster.adjust([_det(8, "child", 0.76, (758, 148, 822, 244))], frame_index=2)
    adjusted = adjuster.adjust(
        [_det(8, "adult", 0.80, (640, 10, 710, 125))],
        frame_index=20,
    )

    assert adjusted[0].role == "adult"


def test_role_context_does_not_promote_from_scene_scale_by_default() -> None:
    adjuster = PediatricRoleContextAdjuster()

    seed = adjuster.adjust(
        [
            _det(9, "adult", 0.82, (600, 80, 710, 244)),
            _det(8, "adult", 0.72, (760, 150, 820, 242)),
        ],
        frame_index=10,
    )
    adjusted = adjuster.adjust(
        [_det(21, "adult", 0.78, (750, 130, 832, 238))],
        frame_index=40,
    )

    assert [item.role for item in seed] == ["adult", "child"]
    assert adjusted[0].role == "adult"
