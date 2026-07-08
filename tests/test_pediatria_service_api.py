from __future__ import annotations

from datetime import datetime
from typing import Any

import pytest
from fastapi.testclient import TestClient

from src.jutta_ped.api import app as app_module
from src.jutta_ped.service import runtime as runtime_module
from src.jutta_ped.service.runtime import (
    PediatriaSessionManager,
    SessionStartConfig,
    safe_name,
    source_display_name,
)


class FakeHeadlessSession:
    """Substitui PediatriaHeadlessSession nos testes de API: sem camera/modelo reais."""

    def __init__(self, config: SessionStartConfig) -> None:
        self.config = config
        self.session_id = "fake_" + str(id(self))
        self.camera_id = safe_name(config.camera_id or source_display_name(config.source))
        self._status = {
            "session_id": self.session_id,
            "camera_id": self.camera_id,
            "source_redacted": config.source,
            "status": "starting",
            "status_final": "ANALISANDO",
            "started_at": datetime.now().isoformat(timespec="milliseconds"),
            "stopped_at": None,
            "frames_processed": 0,
            "popup_count": 0,
            "suppressed_count": 0,
            "last_error": None,
            "evidence_dir": str(config.report_dir / "evidence" / "sessions" / self.session_id),
            "events_log": "",
            "summary_path": "",
        }

    def start(self) -> None:
        self._status["status"] = "running"

    def stop(self) -> None:
        self._status["status"] = "stopped"
        self._status["stopped_at"] = datetime.now().isoformat(timespec="milliseconds")

    def status(self) -> dict[str, Any]:
        return dict(self._status)

    def summary(self) -> dict[str, Any]:
        return dict(self._status)


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setattr(runtime_module, "PediatriaHeadlessSession", FakeHeadlessSession)
    monkeypatch.setattr(app_module, "manager", PediatriaSessionManager())
    return TestClient(app_module.app)


def test_camera_status_defaults_to_stopped_when_unknown(client: TestClient) -> None:
    response = client.get("/cameras/unknown_camera/status")
    assert response.status_code == 200
    body = response.json()
    assert body["camera_id"] == "unknown_camera"
    assert body["active"] is False
    assert body["operational_status"] == "STOPPED"


def test_enable_camera_starts_session_and_lists_it(client: TestClient) -> None:
    response = client.post(
        "/cameras/enable",
        json={"source": "C:/videos/demo.mp4", "camera_id": "entrada_social"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["camera_id"] == "entrada_social"
    assert body["active"] is True
    assert body["operational_status"] == "ANALISANDO"
    assert body["session_id"]

    listed = client.get("/cameras").json()["cameras"]
    assert [item["camera_id"] for item in listed] == ["entrada_social"]

    status = client.get("/cameras/entrada_social/status").json()
    assert status["active"] is True
    assert status["session_id"] == body["session_id"]


def test_enable_camera_is_idempotent_while_running(client: TestClient) -> None:
    first = client.post("/cameras/enable", json={"source": 0, "camera_id": "cam_usb"}).json()
    second = client.post("/cameras/enable", json={"source": 0, "camera_id": "cam_usb"}).json()
    assert first["session_id"] == second["session_id"]


def test_disable_camera_stops_session(client: TestClient) -> None:
    client.post("/cameras/enable", json={"source": 0, "camera_id": "cam_usb"})
    response = client.post("/cameras/cam_usb/disable")
    assert response.status_code == 200
    body = response.json()
    assert body["active"] is False
    assert body["operational_status"] == "STOPPED"

    status = client.get("/cameras/cam_usb/status").json()
    assert status["active"] is False


def test_disable_unknown_camera_is_a_noop(client: TestClient) -> None:
    response = client.post("/cameras/never_started/disable")
    assert response.status_code == 200
    assert response.json()["active"] is False


def test_enable_camera_accepts_stream_ref_instead_of_source(client: TestClient) -> None:
    response = client.post(
        "/cameras/enable",
        json={"stream_ref": "rtsp://camera.local/stream", "camera_id": "entrada_stream_ref"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["camera_id"] == "entrada_stream_ref"
    assert body["active"] is True


def test_camera_enable_request_prefers_stream_ref_over_source_when_both_given() -> None:
    payload = app_module.CameraEnableRequest(
        source="C:/videos/legacy.mp4",
        stream_ref="rtsp://camera.local/preferred",
    )
    config = app_module._config_from_camera_request(payload)
    assert config.source == "rtsp://camera.local/preferred"


def test_camera_enable_request_falls_back_to_source_without_stream_ref() -> None:
    payload = app_module.CameraEnableRequest(source="C:/videos/legacy.mp4")
    config = app_module._config_from_camera_request(payload)
    assert config.source == "C:/videos/legacy.mp4"


def test_enable_camera_requires_source_or_stream_ref(client: TestClient) -> None:
    response = client.post("/cameras/enable", json={"camera_id": "sem_fonte"})
    assert response.status_code == 422
