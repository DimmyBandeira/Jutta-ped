from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
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


class FakeInstanceSession:
    """Dublê de PediatriaHeadlessSession para os testes do contrato de
    instância: sem câmera/modelo reais, mas com os campos novos (stream_id,
    last_event_at, last_frame_at) que o contrato de plugin exige.
    """

    def __init__(self, config: SessionStartConfig) -> None:
        self.config = config
        self.session_id = "inst_" + str(id(self))
        self.camera_id = safe_name(config.camera_id or source_display_name(config.source))
        self.last_overlay_frame: dict[str, Any] | None = None
        self._events: list[dict[str, Any]] = []
        self._status = {
            "session_id": self.session_id,
            "camera_id": self.camera_id,
            "stream_id": config.stream_id,
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
            "last_event_at": None,
            "last_frame_at": None,
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

    def recent_events(self, limit: int = 50) -> list[dict[str, Any]]:
        return list(self._events[-limit:])

    def telemetry_snapshot(self) -> dict[str, Any]:
        return {"resources": {}, "performance": {}}


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setattr(runtime_module, "PediatriaHeadlessSession", FakeInstanceSession)
    monkeypatch.setattr(app_module, "manager", PediatriaSessionManager())
    return TestClient(app_module.app)


def test_plugin_manifest_matches_repo_file(client: TestClient) -> None:
    response = client.get("/plugin/manifest")
    assert response.status_code == 200
    body = response.json()
    assert body["plugin_id"] == "ia.pediatria"
    assert body["name"] == "IA Pediatria"
    assert "runtime" in body
    assert "ui" in body
    assert body["schemas"]["overlay"] == "schemas/overlay.schema.json"

    manifest_path = Path("plugin/manifest.json")
    on_disk = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert body == on_disk


def test_start_instance_accepts_stream_ref_payload(client: TestClient) -> None:
    response = client.post(
        "/instances",
        json={
            "camera_id": "cam_01",
            "stream_id": "stream_123",
            "stream_ref": "rtsp://stream-protegida-ou-video.mp4",
            "lease_id": "lease_abc",
            "config": {"force_cpu": True, "mode": "normal"},
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["plugin_id"] == "ia.pediatria"
    assert body["camera_id"] == "cam_01"
    assert body["stream_id"] == "stream_123"
    assert body["state"] == "running"
    assert body["health"] == "healthy"
    assert body["analysis_state"] == "ANALISANDO"
    assert "instance_id" in body and body["instance_id"]


def test_start_instance_requires_camera_id_and_stream_ref(client: TestClient) -> None:
    missing_camera = client.post("/instances", json={"stream_ref": "video.mp4"})
    assert missing_camera.status_code == 422

    missing_stream_ref = client.post("/instances", json={"camera_id": "cam_01"})
    assert missing_stream_ref.status_code == 422


def test_get_instance_status_after_start(client: TestClient) -> None:
    started = client.post(
        "/instances",
        json={"camera_id": "cam_02", "stream_ref": "video.mp4"},
    ).json()
    instance_id = started["instance_id"]

    response = client.get(f"/instances/{instance_id}")
    assert response.status_code == 200
    body = response.json()
    assert body["instance_id"] == instance_id
    assert body["state"] == "running"

    listed = client.get("/instances").json()["instances"]
    assert [item["instance_id"] for item in listed] == [instance_id]


def test_get_unknown_instance_returns_404(client: TestClient) -> None:
    response = client.get("/instances/does_not_exist")
    assert response.status_code == 404


def test_stop_instance_transitions_state_to_stopped(client: TestClient) -> None:
    started = client.post(
        "/instances",
        json={"camera_id": "cam_03", "stream_ref": "video.mp4"},
    ).json()
    instance_id = started["instance_id"]

    response = client.post(f"/instances/{instance_id}/stop")
    assert response.status_code == 200
    body = response.json()
    assert body["state"] == "stopped"
    assert body["health"] == "healthy"


def test_stop_unknown_instance_returns_404(client: TestClient) -> None:
    response = client.post("/instances/does_not_exist/stop")
    assert response.status_code == 404


def test_instance_summary_reuses_session_summary(client: TestClient) -> None:
    started = client.post(
        "/instances",
        json={"camera_id": "cam_04", "stream_ref": "video.mp4"},
    ).json()
    instance_id = started["instance_id"]

    response = client.get(f"/instances/{instance_id}/summary")
    assert response.status_code == 200
    body = response.json()
    assert body["plugin_id"] == "ia.pediatria"
    assert body["instance_id"] == instance_id
    assert body["session_id"] == instance_id


def test_overlay_latest_is_well_formed_even_when_empty(client: TestClient) -> None:
    started = client.post(
        "/instances",
        json={"camera_id": "cam_05", "stream_ref": "video.mp4"},
    ).json()
    instance_id = started["instance_id"]

    response = client.get(f"/instances/{instance_id}/overlay/latest")
    assert response.status_code == 200
    body = response.json()
    assert body["type"] == "overlay_bboxes"
    assert body["plugin_id"] == "ia.pediatria"
    assert body["instance_id"] == instance_id
    assert body["objects"] == []
    assert "frame_seq" in body and "captured_at" in body


def test_events_endpoint_returns_empty_list_for_fresh_instance(client: TestClient) -> None:
    started = client.post(
        "/instances",
        json={"camera_id": "cam_06", "stream_ref": "video.mp4"},
    ).json()
    instance_id = started["instance_id"]

    response = client.get(f"/instances/{instance_id}/events")
    assert response.status_code == 200
    body = response.json()
    assert body["instance_id"] == instance_id
    assert body["events"] == []


def test_events_endpoint_returns_404_for_unknown_instance(client: TestClient) -> None:
    response = client.get("/instances/does_not_exist/events")
    assert response.status_code == 404


def test_old_camera_and_session_endpoints_still_work_alongside_instances(client: TestClient) -> None:
    """Criterio 4: endpoints antigos continuam funcionando junto com /instances,
    sobre o mesmo PediatriaSessionManager compartilhado."""
    old_enable = client.post(
        "/cameras/enable",
        json={"source": "video_antigo.mp4", "camera_id": "camera_legado"},
    )
    assert old_enable.status_code == 200
    assert old_enable.json()["active"] is True

    old_start = client.post("/sessions/start", json={"source": "video_sessao.mp4"})
    assert old_start.status_code == 200

    new_instance = client.post(
        "/instances",
        json={"camera_id": "camera_nova", "stream_ref": "video_novo.mp4"},
    )
    assert new_instance.status_code == 200

    cameras = client.get("/cameras").json()["cameras"]
    assert {"camera_legado", safe_name("camera_nova")}.issubset(
        {item["camera_id"] for item in cameras}
    )

    instances = client.get("/instances").json()["instances"]
    assert len(instances) == 3
