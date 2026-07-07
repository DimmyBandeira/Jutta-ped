from modulo.pediatria import mvp_popup
from modulo.pediatria.mvp_popup import AlertEventLatch


def test_camera_only_alerts_once_until_episode_is_resolved() -> None:
    latch = AlertEventLatch(cooldown_seconds=0.0)

    assert latch.claim("cam1", "CHILD_SEPARATED", 21) is True
    assert latch.claim("cam1", "CHILD_SEPARATED", 21) is False
    assert latch.claim("cam1", "CHILD_ALONE", 21) is False
    assert latch.claim("cam1", "CHILD_SEPARATED", 32) is False

    latch.resolve_camera("cam1")

    assert latch.claim("cam1", "CHILD_SEPARATED", 21) is True


def test_cameras_are_independent() -> None:
    latch = AlertEventLatch()

    assert latch.claim("cam1", "CHILD_ALONE", 1) is True
    assert latch.claim("cam2", "CHILD_ALONE", 1) is True


def test_presentation_rearm_requires_persistent_resolved_frames() -> None:
    # Mirrors the MVP runner policy: brief UNCERTAIN oscillations do not rearm.
    latch = AlertEventLatch(cooldown_seconds=0.0)
    resolved_frames = 0

    assert latch.claim("cam1", "CHILD_SEPARATED", 21) is True
    for _ in range(44):
        resolved_frames += 1
        if resolved_frames >= 45:
            latch.resolve_camera("cam1")
    assert latch.claim("cam1", "CHILD_SEPARATED", 21) is False

    resolved_frames += 1
    if resolved_frames >= 45:
        latch.resolve_camera("cam1")
    assert latch.claim("cam1", "CHILD_SEPARATED", 21) is True


def test_same_camera_respects_cooldown_even_if_track_changes(monkeypatch) -> None:
    now = [100.0]
    monkeypatch.setattr(mvp_popup.time, "monotonic", lambda: now[0])
    latch = AlertEventLatch(cooldown_seconds=120.0)

    assert latch.claim("cam1", "CHILD_SEPARATED", 21) is True
    latch.resolve_camera("cam1")

    now[0] += 30.0
    assert latch.claim("cam1", "CHILD_SEPARATED", 454) is False

    now[0] += 90.0
    assert latch.claim("cam1", "CHILD_SEPARATED", 454) is True
