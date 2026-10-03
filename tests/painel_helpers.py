"""Dublês e helpers compartilhados pelos testes do painel."""
from __future__ import annotations

import re
from types import SimpleNamespace

import httpx

SENHA_ADMIN = "senha-admin-123"


class FakeGo2rtc:
    """Servidor go2rtc falso (so /api/streams) plugado via httpx.MockTransport."""

    def __init__(self) -> None:
        self.streams: dict[str, str] = {}
        self.online = True
        self.chamadas: list[tuple[str, str]] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if not self.online:
            raise httpx.ConnectError("recusado", request=request)
        if request.url.path != "/api/streams":
            return httpx.Response(404)
        q = request.url.params
        self.chamadas.append((request.method, q.get("name") or q.get("src") or ""))
        if request.method == "GET":
            return httpx.Response(200, json={n: {"producers": [{"url": "***"}]} for n in self.streams})
        if request.method == "PUT":
            self.streams[q["name"]] = q["src"]
            return httpx.Response(200)
        if request.method == "DELETE":
            self.streams.pop(q["src"], None)
            return httpx.Response(200)
        return httpx.Response(405)


class FakeGerente:
    def __init__(self) -> None:
        self.ativas: dict[str, SimpleNamespace] = {}
        self.falhar = False
        self.operacional = "ANALISANDO"

    def enable_camera(self, config):
        if self.falhar:
            raise RuntimeError("modelo indisponivel")
        self.ativas[config.camera_id] = config
        return {}

    def disable_camera(self, camera_id):
        self.ativas.pop(camera_id, None)
        return {}

    def camera_status(self, camera_id):
        ativa = camera_id in self.ativas
        return {"camera_id": camera_id, "active": ativa, "session_id": f"sess_{camera_id}" if ativa else None,
                "operational_status": self.operacional if ativa else "STOPPED"}

    def cameras(self):
        return [{"camera_id": c, "active": True} for c in self.ativas]

    overlay: dict | None = None  # os testes de navegador definem um overlay especifico

    def overlay_latest(self, instance_id):
        if self.overlay is not None:
            return self.overlay
        return {"type": "overlay_bboxes", "frame_w": 1920, "frame_h": 1080, "frame_age_ms": 40.0, "objects": [
            {"target_id": 7, "label": "child", "bbox_xyxy": [10, 20, 110, 220], "confidence": 0.9, "state": "CHILD_ALONE"}]}


def _token(client, path="/login") -> str:
    html = client.get(path).text
    m = re.search(r'name="csrf_token" value="([^"]+)"', html) or re.search(r'"X-CSRF-Token": "([^"]+)"', html)
    assert m, html[:300]
    return m.group(1)


def _login(client, login="admin", senha=SENHA_ADMIN):
    assert client.post("/login", data={"login": login, "senha": senha, "csrf_token": _token(client)}).status_code == 303


def _hx(client, metodo, url, **kw):
    token = _token(client, "/painel/cameras")
    return client.request(metodo, url, headers={"X-CSRF-Token": token, "HX-Request": "true"}, **kw)


def _dados_intelbras(**extra):
    base = {"nome": "Recepção", "fabricante": "intelbras", "ip": "10.0.0.5", "porta": "554", "canal": "1",
            "stream_tipo": "principal", "usuario": "admin", "senha": "p@ss#1", "ia_pediatria": "on"}
    base.update(extra)
    return base


GO2RTC_REAL = "http://127.0.0.1:1984"
STREAM_REAL = "entrada_social_vis"


def porta_livre() -> int:
    import socket

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]
