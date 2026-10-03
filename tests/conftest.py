"""Configuracao global dos testes.

Testes de navegador (Qt WebEngine) precisam que, ANTES de qualquer QApplication ser criada
na sessao: (1) QtWebEngineWidgets esteja importado, (2) AA_ShareOpenGLContexts esteja ligado
e (3) as flags do Chromium estejam no ambiente. Por isso isso vive aqui, e nao no modulo de teste.
Sao flags so de teste: sem GPU, sem sandbox, autoplay livre e sem a politica de mesma origem,
para que o teste consiga inspecionar o iframe do go2rtc (outra porta = outra origem).
"""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault(
    "QTWEBENGINE_CHROMIUM_FLAGS",
    "--disable-gpu --no-sandbox --autoplay-policy=no-user-gesture-required --disable-web-security --disable-site-isolation-trials",
)

try:  # Qt e WebEngine sao opcionais para quem so roda os testes de backend
    from PyQt6 import QtWebEngineWidgets  # noqa: F401
    from PyQt6.QtCore import QCoreApplication, Qt

    QCoreApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts)
except ImportError:
    pass


import pytest  # noqa: E402


@pytest.fixture(scope="module")
def painel_servidor(tmp_path_factory):
    """Painel real (uvicorn em thread) com banco temporario, IA falsa e go2rtc simulado NO LADO DO
    SERVIDOR (o painel nao toca no go2rtc real). Uma camera de teste aponta para um stream que ja
    existe no go2rtc real. Compartilhado pelos testes de navegador (Qt WebEngine e Edge/Chrome)."""
    import threading
    import time
    from types import SimpleNamespace

    import httpx
    import uvicorn
    from fastapi import FastAPI
    from painel_helpers import SENHA_ADMIN, STREAM_REAL, FakeGerente, FakeGo2rtc, porta_livre

    from src.jutta_ped.painel.models import Camera, Fabricante
    from src.jutta_ped.painel.panel import mount_panel

    tmp = tmp_path_factory.mktemp("painel_navegador")
    mp = pytest.MonkeyPatch()
    mp.setenv("JUTTA_ADMIN_PASSWORD", SENHA_ADMIN)
    gerente = FakeGerente()
    app = FastAPI()
    database = mount_panel(
        app,
        db_url=f"sqlite:///{(tmp / 'painel.db').as_posix()}",
        runtime_dir=tmp,
        manager=gerente,
        config_factory=lambda **kw: SimpleNamespace(**kw),
        background=False,
        go2rtc_transport=httpx.MockTransport(FakeGo2rtc()),
        evidence_roots=[tmp / "evid"],
    )
    app.state.painel.ia.dormir = lambda _s: None
    with database.session() as db:
        db.add(Camera(nome="Demo", fabricante=Fabricante.go2rtc_teste, go2rtc_stream_name=STREAM_REAL, ia_pediatria=True, go2rtc_sync="ok"))
    porta = porta_livre()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=porta, log_level="warning", timeout_graceful_shutdown=2))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    limite = time.monotonic() + 20
    while not server.started and time.monotonic() < limite:
        time.sleep(0.05)
    assert server.started, "uvicorn nao subiu"
    yield SimpleNamespace(url=f"http://127.0.0.1:{porta}", gerente=gerente, app=app, database=database, evid=tmp / "evid")
    server.should_exit = True
    thread.join(timeout=10)
    database.dispose()
    mp.undo()
