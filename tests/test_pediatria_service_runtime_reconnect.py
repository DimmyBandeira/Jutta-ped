from __future__ import annotations

import threading
import time
from collections import Counter
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import cv2
import pytest

from src.jutta_ped.service import runtime as runtime_module
from src.jutta_ped.service.runtime import (
    PediatriaHeadlessSession,
    SessionStartConfig,
    SessionState,
    SimpleAlertLatch,
    camera_status_payload,
    classify_source_kind,
    instance_status_payload,
    operational_status_from_session,
)
from src.jutta_ped.service.telemetry import SessionTelemetry


class FakeFrame:
    """Fica no lugar de um frame numpy: so precisa saber se copiar."""

    def copy(self) -> "FakeFrame":
        return self


class ScriptedCapture:
    """Dublê de cv2.VideoCapture com uma sequência fixa de leituras."""

    def __init__(self, script: list[tuple[bool, Any]], *, isOpened_result: bool = True) -> None:
        self._script = list(script)
        self._is_opened = isOpened_result
        self.released = False
        self.set_calls: list[tuple[int, float]] = []

    def read(self) -> tuple[bool, Any]:
        if not self._script:
            return False, None
        return self._script.pop(0)

    def isOpened(self) -> bool:
        return self._is_opened

    def release(self) -> None:
        self.released = True

    def set(self, prop: int, value: float) -> None:
        self.set_calls.append((prop, value))


class FakeService:
    """Dublê de PediatriaService.process_frame: nao carrega nenhum modelo."""

    def __init__(self, stop_event: threading.Event | None = None, stop_after: int | None = None) -> None:
        self.stop_event = stop_event
        self.stop_after = stop_after
        self.calls = 0

    def process_frame(self, frame: Any, *, frame_id: int) -> SimpleNamespace:
        self.calls += 1
        if self.stop_event is not None and self.stop_after is not None and self.calls >= self.stop_after:
            self.stop_event.set()
        return SimpleNamespace(
            latency_ms=1.0,
            state=SimpleNamespace(stable_state="NO_CHILD", children=[]),
            resolved_detections=[],
            person_detections=[],
        )


def make_bare_session(config: SessionStartConfig, tmp_path: Path) -> PediatriaHeadlessSession:
    """Instancia PediatriaHeadlessSession sem passar por __init__ (sem
    carregar YOLO/OpenVINO de verdade), so com o que _run_loop/_handle_read_failure/
    _reconnect_loop precisam. Mesmo padrao ja usado no repo para testar
    classes com construtores pesados (ver PersonCropSpecialistPipeline nos
    testes de crop pipeline).
    """
    session = PediatriaHeadlessSession.__new__(PediatriaHeadlessSession)
    session.config = config
    session.session_id = "test_session"
    session.camera_id = "test_camera"
    session.evidence_dir = tmp_path / "evidence"
    session.events_log_path = session.evidence_dir / "events.jsonl"
    session.state = SessionState(
        session_id=session.session_id,
        camera_id=session.camera_id,
        source_redacted=config.source,
        stream_id=config.stream_id,
        lease_id=config.lease_id,
    )
    session.last_overlay_frame = None
    session.device = "cpu"
    session.device_reason = "test"
    session.capture = None
    session._source_kind = classify_source_kind(config.source)
    session.thread = None
    session.stop_event = threading.Event()
    session.lock = threading.Lock()
    session.telemetry = SessionTelemetry()
    session.latencies = []
    session.stable_state_counts = Counter()
    session.event_counts = Counter()
    session.status_counts = Counter()
    session.suppression_counts = Counter()
    session.evidence_error_counts = Counter()
    session.person_detector_zero = 0
    session.latch = SimpleAlertLatch(config.cooldown_seconds)
    return session


# ---------------------------------------------------------------------------
# classify_source_kind
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "source,expected",
    [
        (0, "device"),
        (2, "device"),
        ("rtsp://camera.local/stream", "network"),
        ("rtsps://camera.local/stream", "network"),
        ("http://camera.local/mjpeg", "network"),
        ("https://camera.local/stream", "network"),
        ("C:/videos/demo.mp4", "file"),
        ("videos/demo.mp4", "file"),
    ],
)
def test_classify_source_kind(source: str | int, expected: str) -> None:
    assert classify_source_kind(source) == expected


# ---------------------------------------------------------------------------
# _handle_read_failure / _reconnect_loop -- fonte tipo 'file'
# ---------------------------------------------------------------------------


def test_file_source_default_policy_stops_like_before(tmp_path: Path) -> None:
    """Regressao: EOF de arquivo local com o default (video_end_policy=stop)
    precisa continuar exatamente como antes desta rodada -- so encerra a
    sessao, sem tentar reconectar (nao e uma falha de rede)."""
    config = SessionStartConfig(source=str(tmp_path / "video.mp4"), report_dir=tmp_path)
    session = make_bare_session(config, tmp_path)
    session.capture = ScriptedCapture([])

    result = session._handle_read_failure()

    assert result is None
    assert session.state.stop_reason == "eof"
    assert session.state.reconnecting is False
    assert session.state.reconnect_attempts == 0


def test_file_source_loop_policy_restarts_from_frame_zero(tmp_path: Path) -> None:
    config = SessionStartConfig(
        source=str(tmp_path / "video.mp4"),
        report_dir=tmp_path,
        video_end_policy="loop",
    )
    session = make_bare_session(config, tmp_path)
    restart_frame = FakeFrame()
    capture = ScriptedCapture([(True, restart_frame)])
    session.capture = capture

    result = session._handle_read_failure()

    assert result is restart_frame
    assert capture.set_calls == [(cv2.CAP_PROP_POS_FRAMES, 0)]
    event_types = [item.get("event_type") for item in session.recent_events(limit=10)]
    assert "loop_restart" in event_types


def test_reconnect_disabled_falls_through_to_stop_for_network_source(tmp_path: Path) -> None:
    config = SessionStartConfig(
        source="rtsp://camera.local/stream",
        report_dir=tmp_path,
        reconnect_enabled=False,
    )
    session = make_bare_session(config, tmp_path)
    session.capture = ScriptedCapture([])

    result = session._handle_read_failure()

    assert result is None
    assert session.state.stop_reason == "read_failed"
    assert session.state.reconnecting is False


# ---------------------------------------------------------------------------
# _reconnect_loop -- fonte tipo 'network'/'device' (o bug relatado)
# ---------------------------------------------------------------------------


def test_reconnect_loop_succeeds_after_transient_failures(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    config = SessionStartConfig(
        source="rtsp://camera.local/stream",
        report_dir=tmp_path,
        reconnect_backoff_seconds=0.01,
        reconnect_backoff_max_seconds=0.02,
        max_reconnect_attempts=5,
    )
    session = make_bare_session(config, tmp_path)
    session.capture = ScriptedCapture([])

    attempts = {"n": 0}

    def fake_open(source: Any) -> ScriptedCapture:
        attempts["n"] += 1
        if attempts["n"] < 3:
            return ScriptedCapture([], isOpened_result=False)
        return ScriptedCapture([(True, FakeFrame())])

    monkeypatch.setattr(runtime_module, "open_capture_source", fake_open)

    result = session._reconnect_loop()

    assert result is not None
    _capture, frame = result
    assert isinstance(frame, FakeFrame)
    assert attempts["n"] == 3
    assert session.state.reconnecting is False
    assert session.state.reconnect_attempts == 3
    assert session.state.last_reconnect_at is not None
    event_types = [item.get("event_type") for item in session.recent_events(limit=20)]
    assert event_types.count("reconnect_started") == 3
    assert event_types.count("reconnect_failed") == 2
    assert event_types.count("reconnected") == 1


def test_reconnect_loop_exhausts_attempts_and_gives_up(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    config = SessionStartConfig(
        source="rtsp://camera.local/stream",
        report_dir=tmp_path,
        reconnect_backoff_seconds=0.01,
        reconnect_backoff_max_seconds=0.02,
        max_reconnect_attempts=2,
    )
    session = make_bare_session(config, tmp_path)
    session.capture = ScriptedCapture([])
    monkeypatch.setattr(
        runtime_module, "open_capture_source", lambda source: ScriptedCapture([], isOpened_result=False)
    )

    result = session._reconnect_loop()

    assert result is None
    assert session.state.reconnecting is False
    event_types = [item.get("event_type") for item in session.recent_events(limit=20)]
    assert event_types.count("reconnect_started") == 2
    assert event_types.count("reconnect_failed") == 2
    assert "reconnected" not in event_types


def test_handle_read_failure_network_reports_exhaustion(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    config = SessionStartConfig(
        source="rtsp://camera.local/stream",
        report_dir=tmp_path,
        reconnect_backoff_seconds=0.01,
        max_reconnect_attempts=1,
    )
    session = make_bare_session(config, tmp_path)
    session.capture = ScriptedCapture([])
    monkeypatch.setattr(
        runtime_module, "open_capture_source", lambda source: ScriptedCapture([], isOpened_result=False)
    )

    result = session._handle_read_failure()

    assert result is None
    assert session.state.stop_reason == "reconnect_exhausted"
    assert session.state.last_error


def test_reconnect_loop_interrupted_by_stop_event_during_backoff(tmp_path: Path) -> None:
    config = SessionStartConfig(
        source="rtsp://camera.local/stream",
        report_dir=tmp_path,
        reconnect_backoff_seconds=5.0,
        max_reconnect_attempts=-1,
    )
    session = make_bare_session(config, tmp_path)
    session.capture = ScriptedCapture([])
    session.stop_event.set()

    started = time.perf_counter()
    result = session._reconnect_loop()
    elapsed = time.perf_counter() - started

    assert result is None
    assert elapsed < 1.0
    assert session.state.reconnecting is False


# ---------------------------------------------------------------------------
# _run_loop de ponta a ponta: prova que uma falha transiente de rede nao
# derruba mais a sessao inteira (era o bug relatado).
# ---------------------------------------------------------------------------


def test_run_loop_survives_transient_network_failure_and_keeps_processing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    config = SessionStartConfig(
        source="rtsp://camera.local/stream",
        report_dir=tmp_path,
        reconnect_backoff_seconds=0.01,
        reconnect_backoff_max_seconds=0.02,
        max_reconnect_attempts=3,
    )
    session = make_bare_session(config, tmp_path)
    session.capture = ScriptedCapture([(True, FakeFrame()), (False, None)])

    reconnect_capture = ScriptedCapture([(True, FakeFrame()), (True, FakeFrame())])
    monkeypatch.setattr(runtime_module, "open_capture_source", lambda source: reconnect_capture)

    fake_service = FakeService(stop_event=session.stop_event, stop_after=3)
    session.service = fake_service

    session._run_loop()

    assert fake_service.calls == 3
    assert session.state.frames_processed == 3
    assert session.state.reconnect_attempts == 1
    assert session.state.last_reconnect_at is not None
    assert session.state.status == "stopped"
    event_types = [item.get("event_type") for item in session.recent_events(limit=20)]
    assert "reconnected" in event_types


def test_stop_interrupts_reconnect_backoff_quickly(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Regressao central do bug relatado: stop() nao pode ficar preso
    esperando o backoff de reconexao terminar (antes, a sessao simplesmente
    morria sem tentar reconectar; a correcao nao pode trocar 'morre direto'
    por 'trava ao tentar parar')."""
    config = SessionStartConfig(
        source="rtsp://camera.local/stream",
        report_dir=tmp_path,
        reconnect_backoff_seconds=10.0,
        max_reconnect_attempts=-1,
    )
    session = make_bare_session(config, tmp_path)
    session.capture = ScriptedCapture([(False, None)])
    monkeypatch.setattr(
        runtime_module, "open_capture_source", lambda source: ScriptedCapture([], isOpened_result=False)
    )
    session.service = FakeService()

    session.thread = threading.Thread(target=session._run_loop, daemon=True)
    session.thread.start()
    time.sleep(0.2)  # deixa a sessao entrar no backoff de 10s

    started = time.perf_counter()
    session.stop()
    elapsed = time.perf_counter() - started

    assert elapsed < 2.0
    assert session.state.status == "stopped"
    assert session.state.stop_reason == "stop_requested"


# ---------------------------------------------------------------------------
# Status/telemetria refletindo reconexao
# ---------------------------------------------------------------------------


def test_operational_status_reports_reconectando() -> None:
    status = {"camera_id": "cam", "status": "running", "status_final": "ANALISANDO", "reconnecting": True}
    assert operational_status_from_session(status) == "RECONECTANDO"


def test_camera_status_payload_exposes_reconnect_fields() -> None:
    status = {
        "camera_id": "cam",
        "status": "running",
        "status_final": "ANALISANDO",
        "reconnecting": True,
        "reconnect_attempts": 4,
        "stop_reason": None,
    }
    payload = camera_status_payload(status)
    assert payload["reconnecting"] is True
    assert payload["reconnect_attempts"] == 4


def test_instance_status_reflects_reconnecting_as_degraded() -> None:
    status = {
        "session_id": "s1",
        "camera_id": "cam",
        "status": "running",
        "reconnecting": True,
        "reconnect_attempts": 2,
    }
    payload = instance_status_payload(status)
    assert payload["state"] == "degraded"
    assert payload["health"] == "degraded"
    assert payload["reconnect_attempts"] == 2
