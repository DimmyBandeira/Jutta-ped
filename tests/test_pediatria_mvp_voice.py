import threading
import time

from modulo.pediatria.mvp_voice import (
    MvpVoiceAnnouncer,
    build_companionship_alert_message,
)


class _Engine:
    def __init__(self) -> None:
        self.messages: list[str] = []
        self.properties: dict[str, object] = {}
        self.spoken = threading.Event()

    def setProperty(self, name: str, value: object) -> None:
        self.properties[name] = value

    def say(self, message: str) -> None:
        self.messages.append(message)
        self.spoken.set()

    def runAndWait(self) -> None:
        return

    def stop(self) -> None:
        return


def _wait_for_messages(engine: _Engine, count: int, timeout: float = 1.0) -> None:
    deadline = time.monotonic() + timeout
    while len(engine.messages) < count and time.monotonic() < deadline:
        engine.spoken.wait(0.01)
        engine.spoken.clear()


def test_build_alert_message_uses_readable_camera_name() -> None:
    assert build_companionship_alert_message("corredor_social") == (
        "Alerta, corredor social. Crianca desacompanhada."
    )


def test_build_separation_alert_message() -> None:
    assert build_companionship_alert_message("corredor", "CHILD_SEPARATED") == (
        "Alerta, corredor. Crianca se afastando do acompanhante."
    )


def test_voice_repeats_single_active_camera_until_deactivated() -> None:
    engine = _Engine()
    factory_calls = 0

    def engine_factory() -> _Engine:
        nonlocal factory_calls
        factory_calls += 1
        return engine

    announcer = MvpVoiceAnnouncer(
        repeat_interval_seconds=0.03,
        engine_factory=engine_factory,
    )

    assert announcer.activate_companionship_alert("camera 1") is True
    assert announcer.activate_companionship_alert("camera 1") is False
    _wait_for_messages(engine, 2)
    assert announcer.deactivate_alert("camera 1") is True
    count_after_close = len(engine.messages)
    time.sleep(0.08)
    announcer.close()

    assert count_after_close >= 2
    assert factory_calls >= 2
    assert len(engine.messages) == count_after_close
    assert set(engine.messages) == {"Alerta, camera 1. Crianca desacompanhada."}


def test_voice_alternates_active_cameras_without_overlap() -> None:
    engine = _Engine()
    announcer = MvpVoiceAnnouncer(
        repeat_interval_seconds=0.02,
        engine_factory=lambda: engine,
    )

    announcer.activate_companionship_alert("camera 1")
    announcer.activate_companionship_alert("camera 2")
    _wait_for_messages(engine, 4)
    announcer.close()

    camera_1 = "Alerta, camera 1. Crianca desacompanhada."
    camera_2 = "Alerta, camera 2. Crianca desacompanhada."
    assert engine.messages[:4] in (
        [camera_1, camera_2, camera_1, camera_2],
        [camera_2, camera_1, camera_2, camera_1],
    )


def test_existing_camera_keeps_current_audio_until_popup_closes() -> None:
    engine = _Engine()
    announcer = MvpVoiceAnnouncer(
        repeat_interval_seconds=0.02,
        engine_factory=lambda: engine,
    )

    announcer.activate_companionship_alert("camera 1", "CHILD_ALONE")
    announcer.activate_companionship_alert("camera 1", "CHILD_SEPARATED")
    _wait_for_messages(engine, 2)
    announcer.close()

    assert set(engine.messages) == {"Alerta, camera 1. Crianca desacompanhada."}


def test_voice_announcer_can_be_disabled() -> None:
    announcer = MvpVoiceAnnouncer(enabled=False, engine_factory=_Engine)

    assert announcer.activate_companionship_alert("camera 1") is False
