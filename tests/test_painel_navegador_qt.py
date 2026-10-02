"""Testes de navegador do painel com pytest-qt + Qt WebEngine (Chromium de verdade).

Sobem o painel real (uvicorn numa thread, banco temporario, IA falsa) e o abrem num
QWebEngineView. Cobrem o que os testes de backend nao alcancam: login pelo formulario,
htmx/JS carregando de `/static`, badge de IA atualizando sozinho, overlay desenhado no canvas
(inclusive o mapeamento de letterbox) e o player do go2rtc no iframe.

O teste do player usa o go2rtc REAL em 127.0.0.1:1984 e so le o stream `entrada_social_vis`
(sem criar nem alterar nada nele); pula sozinho se o go2rtc nao estiver no ar.

Flags do Chromium/Qt: ver tests/conftest.py.
"""
from __future__ import annotations

import os
import socket
import threading
import time
import urllib.request
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

pytest.importorskip("PyQt6.QtWebEngineWidgets", reason="PyQt6-WebEngine nao instalado")
pytest.importorskip("pytestqt")

import uvicorn  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from PyQt6.QtCore import QUrl  # noqa: E402
from PyQt6.QtWebEngineCore import QWebEnginePage, QWebEngineProfile  # noqa: E402
from PyQt6.QtWebEngineWidgets import QWebEngineView  # noqa: E402

from painel_helpers import FakeGerente, FakeGo2rtc, SENHA_ADMIN  # noqa: E402
from src.jutta_ped.painel.models import Camera, Fabricante  # noqa: E402
from src.jutta_ped.painel.panel import mount_panel  # noqa: E402

GO2RTC_REAL = "http://127.0.0.1:1984"
STREAM_REAL = "entrada_social_vis"
_ERRO = QWebEnginePage.JavaScriptConsoleMessageLevel.ErrorMessageLevel


def _porta_livre() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def servidor(tmp_path_factory):
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
        go2rtc_transport=httpx.MockTransport(FakeGo2rtc()),  # o painel nao toca no go2rtc real
    )
    app.state.painel.ia.dormir = lambda _s: None
    with database.session() as db:
        db.add(Camera(nome="Demo", fabricante=Fabricante.go2rtc_teste, go2rtc_stream_name=STREAM_REAL, ia_pediatria=True, go2rtc_sync="ok"))
    porta = _porta_livre()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=porta, log_level="warning", timeout_graceful_shutdown=2))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    limite = time.monotonic() + 20
    while not server.started and time.monotonic() < limite:
        time.sleep(0.05)
    assert server.started, "uvicorn nao subiu"
    yield SimpleNamespace(url=f"http://127.0.0.1:{porta}", gerente=gerente, app=app)
    server.should_exit = True
    thread.join(timeout=10)
    database.dispose()
    mp.undo()


class _Pagina(QWebEnginePage):
    def __init__(self, perfil, pai):
        super().__init__(perfil, pai)
        self.console: list[tuple[int, str, str]] = []

    def javaScriptConsoleMessage(self, nivel, mensagem, linha, origem):  # noqa: N802
        self.console.append((nivel, mensagem, f"{origem}:{linha} pagina={self.url().path()}"))


class Navegador:
    def __init__(self, qtbot, view: QWebEngineView, pagina: _Pagina):
        self.qtbot, self.view, self.pagina = qtbot, view, pagina

    def abrir(self, url: str) -> bool:
        with self.qtbot.waitSignal(self.view.loadFinished, timeout=30000) as sinal:
            self.view.load(QUrl(url))
        return bool(sinal.args[0])

    def js(self, codigo: str, timeout: int = 8000):
        resultado: dict = {}
        self.pagina.runJavaScript(codigo, lambda valor: resultado.setdefault("v", valor))
        self.qtbot.waitUntil(lambda: "v" in resultado, timeout=timeout)
        return resultado["v"]

    def esperar(self, expressao: str, timeout: int = 15000, intervalo: int = 100):
        """Reavalia `expressao` no navegador ate ficar verdadeira."""
        limite, ultimo = time.monotonic() + timeout / 1000, None
        while time.monotonic() < limite:
            ultimo = self.js(expressao)
            if ultimo:
                return ultimo
            self.qtbot.wait(intervalo)
        raise AssertionError(f"Condicao nunca ficou verdadeira: {expressao!r} (ultimo valor: {ultimo!r})")

    def entrar(self, base: str, login: str = "admin", senha: str = SENHA_ADMIN) -> None:
        assert self.abrir(f"{base}/login")
        with self.qtbot.waitSignal(self.view.loadFinished, timeout=30000):
            self.js(
                f"document.querySelector('[name=login]').value={login!r};"
                f"document.querySelector('[name=senha]').value={senha!r};"
                "document.querySelector('form').submit(); true"
            )

    def captura(self, nome: str) -> None:
        """Salva um PNG da janela se JUTTA_TEST_SCREENSHOT_DIR estiver definido (inspecao visual)."""
        pasta = os.environ.get("JUTTA_TEST_SCREENSHOT_DIR")
        if pasta:
            Path(pasta).mkdir(parents=True, exist_ok=True)
            self.qtbot.wait(300)
            self.view.grab().save(str(Path(pasta) / f"{nome}.png"))

    def erros_js(self) -> list[str]:
        """Erros de console, exceto o ruido conhecido do SSE: ao navegar, o EventSource da pagina
        anterior e encerrado e o htmx registra o evento de erro (`[object Event]`, origem htmx.min.js)."""
        return [
            f"{m}  @ {o}"
            for nivel, m, o in self.pagina.console
            if nivel == _ERRO and not (m == "[object Event]" and "htmx.min.js" in o)
        ]


@pytest.fixture()
def nav(qtbot):
    view = QWebEngineView()
    perfil = QWebEngineProfile(view)  # perfil isolado (cookies proprios) por teste
    pagina = _Pagina(perfil, view)
    view.setPage(pagina)
    view.resize(1280, 800)
    qtbot.addWidget(view)
    view.show()
    yield Navegador(qtbot, view, pagina)
    view.setPage(None)
    pagina.deleteLater()


# ---------------------------------------------------------------- login e pagina


def test_login_invalido_mostra_erro_e_valido_abre_o_monitoramento(nav, servidor):
    nav.entrar(servidor.url, senha="senha-errada-123")
    assert "Usuário ou senha inválidos" in nav.js("document.body.innerText")
    assert nav.js("location.pathname") == "/login"

    nav.entrar(servidor.url)
    assert nav.js("location.pathname") == "/painel/monitoramento"
    assert "Monitoramento" in nav.js("document.title")
    assert nav.js("typeof htmx") == "object"              # htmx local carregou de /static/vendor
    assert nav.js("document.querySelectorAll('.cam-card').length") == 1
    assert nav.js("typeof livePlay") == "function"
    nav.captura("monitoramento_sem_play")
    assert nav.erros_js() == []


def test_menu_do_admin_e_navegacao_para_cameras(nav, servidor):
    nav.entrar(servidor.url)
    links = nav.js("Array.from(document.querySelectorAll('nav a')).map(a => a.textContent.trim())")
    assert links == ["Monitoramento", "Câmeras", "Relatório", "Destinos", "Configurações", "Usuários"]
    assert nav.abrir(f"{servidor.url}/painel/cameras")
    assert "Demo" in nav.js("document.body.innerText")
    # colunas alinhadas: cada celula cai sob o cabecalho certo
    tabela = nav.js(
        "(() => {const ths = Array.from(document.querySelectorAll('#lista-cameras th')).map(t => t.textContent.trim());"
        " const tds = Array.from(document.querySelectorAll('#lista-cameras tbody tr:first-child td')).map(t => t.innerText.trim());"
        " return {ths, tds};})()"
    )
    assert len(tabela["ths"]) == len(tabela["tds"]), tabela
    por_coluna = dict(zip(tabela["ths"], tabela["tds"]))
    assert por_coluna["Stream"] == "sincronizado" and por_coluna["Destinos"] == "nenhum" and por_coluna["IA"] == "ligada", por_coluna
    nav.captura("cameras_lista")
    assert nav.erros_js() == []


# ---------------------------------------------------------------- badge de IA (htmx)


def test_badge_da_ia_atualiza_sozinho_via_htmx(nav, servidor):
    servidor.gerente.ativas.clear()
    servidor.app.state.painel.ia.reconcile()              # IA "sobe" (falsa) para cam_1
    servidor.gerente.operacional = "ANALISANDO"
    nav.entrar(servidor.url)
    nav.esperar("document.getElementById('ia-estado-1').innerText.includes('Analisando')", timeout=10000)

    servidor.gerente.operacional = "CRIANCA_DESACOMPANHADA"
    nav.esperar("document.getElementById('ia-estado-1').innerText.includes('Criança desacompanhada')", timeout=10000)
    assert nav.js("document.querySelector('#ia-estado-1 .badge').className").endswith("alerta")
    assert nav.erros_js() == []


# ---------------------------------------------------------------- overlay no canvas


_ALFA = (
    "(() => {const c = document.getElementById('overlay-1'); const d = c.getContext('2d').getImageData(%d, %d, 1, 1).data;"
    " return [d[0], d[1], d[2], d[3]];})()"
)


def test_overlay_e_desenhado_no_canvas_respeitando_o_letterbox(nav, servidor):
    servidor.gerente.ativas.clear()
    servidor.app.state.painel.ia.reconcile()
    # frame 4:3 dentro de um card 16:9 => barras laterais; bbox cobre o frame inteiro
    servidor.gerente.overlay = {
        "type": "overlay_bboxes", "frame_w": 960, "frame_h": 720, "frame_age_ms": 10.0,
        "objects": [{"target_id": 1, "label": "child", "bbox_xyxy": [0, 0, 960, 720], "confidence": 0.93, "state": "CHILD_ALONE"}],
    }
    try:
        nav.entrar(servidor.url)
        nav.js("document.querySelector('button[onclick=\"liveTodas(true)\"]').click(); true")   # botao "Tocar todas"
        assert nav.js("document.getElementById('player-1').src") == f"{GO2RTC_REAL}/stream.html?src={STREAM_REAL}"

        pintados = nav.esperar(
            "(() => {const c = document.getElementById('overlay-1'); if (!c.width) return 0;"
            " const d = c.getContext('2d').getImageData(0, 0, c.width, c.height).data; let n = 0;"
            " for (let i = 3; i < d.length; i += 4) if (d[i] > 0) n++; return n;})()",
            timeout=10000,
        )
        assert pintados > 100

        cw, ch = nav.js("(() => {const c = document.getElementById('overlay-1'); return [c.width, c.height];})()")
        escala = min(cw / 960, ch / 720)
        dx = round((cw - 960 * escala) / 2)
        assert dx > 20                                    # ha barras laterais de verdade
        r, g, b, a = nav.js(_ALFA % (dx, ch // 2))
        assert a > 0 and r > 200 and g < 160              # borda esquerda do bbox, na cor de "crianca" (#f87171)
        assert nav.js(_ALFA % (dx - 6, ch // 2))[3] == 0  # dentro da barra: nada desenhado
        assert nav.js(_ALFA % (cw - dx, ch // 2))[3] > 0  # borda direita, simetrica
        assert nav.js(_ALFA % (3, 3))[3] == 0             # canto fora do video

        nav.captura("monitoramento_overlay_letterbox")
        nav.js("livePlay(1, false); true")                # Pause limpa iframe e canvas
        assert nav.js("document.getElementById('player-1').src") == "about:blank"
        nav.esperar(
            "(() => {const c = document.getElementById('overlay-1');"
            " const d = c.getContext('2d').getImageData(0, 0, c.width, c.height).data; return d.every(v => v === 0);})()",
            timeout=5000,
        )
        assert nav.erros_js() == []
    finally:
        servidor.gerente.overlay = None


def test_overlay_velho_nao_e_desenhado(nav, servidor):
    servidor.gerente.ativas.clear()
    servidor.app.state.painel.ia.reconcile()
    servidor.gerente.overlay = {
        "type": "overlay_bboxes", "frame_w": 960, "frame_h": 720, "frame_age_ms": 5000.0,
        "objects": [{"target_id": 1, "label": "adult", "bbox_xyxy": [10, 10, 200, 300], "confidence": 0.8, "state": "ANALISANDO"}],
    }
    try:
        nav.entrar(servidor.url)
        nav.js("livePlay(1, true); true")
        nav.qtbot.wait(1500)                              # varios ciclos de polling (300 ms)
        assert nav.js("document.getElementById('player-1').src").endswith(f"src={STREAM_REAL}")
        assert nav.js(
            "(() => {const c = document.getElementById('overlay-1'); if (!c.width) return true;"
            " const d = c.getContext('2d').getImageData(0, 0, c.width, c.height).data; return d.every(v => v === 0);})()"
        )
        nav.js("livePlay(1, false); true")
    finally:
        servidor.gerente.overlay = None


# ---------------------------------------------------------------- player go2rtc real


def _go2rtc_tem_o_stream() -> bool:
    try:
        with urllib.request.urlopen(f"{GO2RTC_REAL}/api/streams", timeout=2) as resp:
            return STREAM_REAL.encode() in resp.read()
    except OSError:
        return False


@pytest.mark.skipif(not _go2rtc_tem_o_stream(), reason=f"go2rtc real sem o stream {STREAM_REAL} em {GO2RTC_REAL}")
def test_player_do_go2rtc_carrega_no_iframe_do_card(nav, servidor):
    servidor.gerente.ativas.clear()
    nav.entrar(servidor.url)
    nav.js("livePlay(1, true); true")
    # iframe carrega a pagina do go2rtc (a flag de teste desliga a politica de mesma origem)
    nav.esperar(
        "(() => {const f = document.getElementById('player-1'); const d = f.contentDocument;"
        " return !!d && !!d.querySelector('video-stream');})()",
        timeout=20000,
    )
    codecs = nav.js(
        "(() => {const c = (window.RTCRtpReceiver && RTCRtpReceiver.getCapabilities('video')) || {codecs: []};"
        " return {webrtcH264: c.codecs.some(x => /h264/i.test(x.mimeType)),"
        " mseH264: !!window.MediaSource && MediaSource.isTypeSupported('video/mp4; codecs=\"avc1.640029\"')};})()"
    )
    print(f"\n[player] iframe do go2rtc carregou; codecs H.264 no Qt WebEngine: {codecs}")

    tocando = False
    limite = time.monotonic() + 25
    while time.monotonic() < limite and not tocando:
        tocando = bool(nav.js(
            "(() => {const d = document.getElementById('player-1').contentDocument; const el = d && d.querySelector('video-stream');"
            " const v = el && (el.video || el.querySelector('video')); return !!v && v.readyState >= 2 && v.currentTime > 0;})()"
        ))
        nav.qtbot.wait(300)
    if not tocando:
        if not (codecs["webrtcH264"] or codecs["mseH264"]):
            pytest.skip(f"Qt WebEngine sem decodificador H.264 ({codecs}); o stream de demo e H.264 -- pagina e iframe OK, reproducao nao testavel aqui")
        pytest.fail(f"video nao comecou a tocar em 25 s apesar de haver H.264 ({codecs})")
    assert nav.erros_js() == []


@pytest.mark.skipif(not _go2rtc_tem_o_stream(), reason=f"go2rtc real sem o stream {STREAM_REAL} em {GO2RTC_REAL}")
def test_go2rtc_entrega_frame_real_do_stream_e_o_navegador_o_decodifica(nav, servidor):
    """Prova que o stream tem video vivo sem depender de H.264 no navegador: o go2rtc converte
    um frame para JPEG (usa o ffmpeg do proprio gateway) e o Chromium o decodifica."""
    url = f"{GO2RTC_REAL}/api/frame.jpeg?src={STREAM_REAL}"
    with urllib.request.urlopen(url, timeout=30) as resp:
        corpo = resp.read()
    assert corpo[:2].hex() == "ffd8" and len(corpo) > 5000, f"frame invalido ({len(corpo)} bytes)"

    assert nav.abrir(f"{servidor.url}/login")
    nav.js(
        "window.__img = {ok: null}; const i = new Image();"
        " i.onload = () => window.__img = {ok: true, w: i.naturalWidth, h: i.naturalHeight};"
        " i.onerror = () => window.__img = {ok: false};"
        f" i.src = {url!r} + '&t=' + Date.now(); true"
    )
    info = nav.esperar("window.__img.ok !== null && window.__img", timeout=30000)
    print(f"\n[frame] {len(corpo)} bytes JPEG; decodificado no navegador: {info}")
    assert info["ok"] is True and info["w"] > 100 and info["h"] > 100
