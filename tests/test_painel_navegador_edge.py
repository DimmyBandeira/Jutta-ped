"""Testes de navegador com Edge/Chrome REAIS (Playwright, sem baixar navegador).

Complementam tests/test_painel_navegador_qt.py: o Qt WebEngine nao decodifica H.264, entao so
aqui da para provar que o video do go2rtc toca no card e que o overlay casa com a imagem real.

Usam o go2rtc REAL em 127.0.0.1:1984 apenas para LER o stream `entrada_social_vis` (nada e
criado/alterado nele). Pulam sozinhos sem Playwright, sem Edge/Chrome ou sem o stream.
"""
from __future__ import annotations

import os
import urllib.request
from pathlib import Path

import pytest

pytest.importorskip("playwright.sync_api", reason="playwright nao instalado (pip install playwright)")
from playwright.sync_api import sync_playwright  # noqa: E402

from painel_helpers import GO2RTC_REAL, SENHA_ADMIN, STREAM_REAL, payload_alerta  # noqa: E402


def _go2rtc_tem_o_stream() -> bool:
    try:
        with urllib.request.urlopen(f"{GO2RTC_REAL}/api/streams", timeout=2) as resp:
            return STREAM_REAL.encode() in resp.read()
    except OSError:
        return False


pytestmark = pytest.mark.skipif(not _go2rtc_tem_o_stream(), reason=f"go2rtc real sem o stream {STREAM_REAL} em {GO2RTC_REAL}")


@pytest.fixture(scope="module")
def navegador():
    with sync_playwright() as pw:
        browser, erros = None, []
        for canal in ("msedge", "chrome"):
            try:
                browser = pw.chromium.launch(channel=canal, headless=True, args=["--autoplay-policy=no-user-gesture-required"])
                break
            except Exception as exc:  # noqa: BLE001
                erros.append(f"{canal}: {type(exc).__name__}")
        if browser is None:
            pytest.skip(f"nenhum Edge/Chrome utilizavel ({'; '.join(erros)})")
        yield browser
        browser.close()


@pytest.fixture()
def pagina(navegador, painel_servidor):
    contexto = navegador.new_context(viewport={"width": 1280, "height": 800})
    pg = contexto.new_page()
    pg.erros = []
    pg.on("pageerror", lambda e: pg.erros.append(str(e)))
    pg.goto(f"{painel_servidor.url}/login")
    pg.fill("[name=login]", "admin")
    pg.fill("[name=senha]", SENHA_ADMIN)
    pg.click("button[type=submit]")
    pg.wait_for_url("**/painel/monitoramento")
    yield pg
    contexto.close()


def _frame_do_player(pg):
    pg.wait_for_selector("#player-1")
    frame = pg.query_selector("#player-1").content_frame()
    assert frame is not None
    return frame


_VIDEO_TOCANDO = """() => {
  const v = document.querySelector('video-stream')?.video || document.querySelector('video');
  return !!v && v.readyState >= 2 && v.currentTime > 0 && v.videoWidth > 0;
}"""

_INFO_VIDEO = """() => {
  const v = document.querySelector('video-stream')?.video || document.querySelector('video');
  const r = v.getBoundingClientRect();
  return {w: v.videoWidth, h: v.videoHeight, t: v.currentTime, fit: getComputedStyle(v).objectFit,
          x: r.x, y: r.y, rw: r.width, rh: r.height, fw: innerWidth, fh: innerHeight};
}"""


def _capturar(pg, nome: str) -> None:
    pasta = os.environ.get("JUTTA_TEST_SCREENSHOT_DIR")
    if pasta:
        Path(pasta).mkdir(parents=True, exist_ok=True)
        pg.screenshot(path=str(Path(pasta) / f"{nome}.png"))


def test_video_h264_do_go2rtc_toca_no_card_e_pause_para(pagina):
    pg = pagina
    pg.click("text=Tocar todas")
    assert pg.get_attribute("#player-1", "src") == f"{GO2RTC_REAL}/stream.html?src={STREAM_REAL}"
    frame = _frame_do_player(pg)
    frame.wait_for_function(_VIDEO_TOCANDO, timeout=40000)
    info = frame.evaluate(_INFO_VIDEO)
    print(f"\n[video] tocando no card: {info['w']}x{info['h']} t={info['t']:.1f}s fit={info['fit']}")
    assert info["w"] > 100 and info["h"] > 100

    antes = frame.evaluate(_INFO_VIDEO)["t"]
    pg.wait_for_timeout(1500)
    assert frame.evaluate(_INFO_VIDEO)["t"] > antes          # o relogio do video avanca: esta rodando de fato
    _capturar(pg, "edge_video_tocando")

    pg.click("text=Pausar todas")
    assert pg.get_attribute("#player-1", "src") == "about:blank"
    assert pg.erros == []


def test_overlay_casa_com_a_imagem_real_do_player(pagina, painel_servidor):
    """Desenha uma bbox cobrindo o frame inteiro e confere que ela coincide com a area REAL do video
    (mesmo letterbox que o go2rtc usa), medindo o <video> dentro do iframe."""
    pg = pagina
    painel_servidor.gerente.ativas.clear()
    painel_servidor.app.state.painel.ia.reconcile()
    try:
        # descobre o tamanho real do frame tocando uma vez
        pg.click("text=Tocar todas")
        frame = _frame_do_player(pg)
        frame.wait_for_function(_VIDEO_TOCANDO, timeout=40000)
        real = frame.evaluate(_INFO_VIDEO)
        fw, fh = real["w"], real["h"]
        painel_servidor.gerente.overlay = {
            "type": "overlay_bboxes", "frame_w": fw, "frame_h": fh, "frame_age_ms": 10.0,
            "objects": [{"target_id": 1, "label": "child", "bbox_xyxy": [0, 0, fw, fh], "confidence": 0.93, "state": "CHILD_ALONE"}],
        }
        pg.wait_for_function(
            "() => { const c = document.getElementById('overlay-1'); if (!c.width) return false;"
            " const d = c.getContext('2d').getImageData(0, 0, c.width, c.height).data;"
            " for (let i = 3; i < d.length; i += 4) if (d[i] > 0) return true; return false; }",
            timeout=10000,
        )
        # retangulo do conteudo do video segundo o player (object-fit) vs retangulo desenhado pelo live.js
        info = frame.evaluate(_INFO_VIDEO)
        assert info["fit"] in ("contain", "scale-down"), f"object-fit inesperado do go2rtc: {info['fit']}"
        esc = min(info["rw"] / fw, info["rh"] / fh)
        esperado_x = info["x"] + (info["rw"] - fw * esc) / 2
        esperado_larg = fw * esc
        caixa = pg.evaluate(
            "() => { const c = document.getElementById('overlay-1'); const d = c.getContext('2d').getImageData(0, Math.floor(c.height / 2), c.width, 1).data;"
            " let ini = -1, fim = -1; for (let x = 0; x < c.width; x++) { if (d[x * 4 + 3] > 0) { if (ini < 0) ini = x; fim = x; } }"
            " return {ini, fim, cw: c.width, ch: c.height}; }"
        )
        print(f"\n[overlay] video real: x={esperado_x:.1f} larg={esperado_larg:.1f}; caixa desenhada: {caixa['ini']}..{caixa['fim']}")
        assert abs(caixa["ini"] - esperado_x) <= 3, (caixa, esperado_x)
        assert abs(caixa["fim"] - (esperado_x + esperado_larg)) <= 3, (caixa, esperado_x, esperado_larg)
        _capturar(pg, "edge_video_com_overlay")
        assert pg.erros == []
    finally:
        painel_servidor.gerente.overlay = None


def _novo_operador(navegador, base: str, caminho: str = "/painel/monitoramento"):
    contexto = navegador.new_context(viewport={"width": 1280, "height": 800})
    pg = contexto.new_page()
    pg.erros = []
    pg.on("pageerror", lambda e: pg.erros.append(str(e)))
    pg.goto(f"{base}/login")
    pg.fill("[name=login]", "admin")
    pg.fill("[name=senha]", SENHA_ADMIN)
    pg.click("button[type=submit]")
    pg.wait_for_url("**/painel/monitoramento")
    if caminho != "/painel/monitoramento":
        pg.goto(f"{base}{caminho}")
    return contexto, pg


def _esperar_inscritos(painel_servidor, minimo: int, timeout: float = 15.0) -> None:
    import time

    limite = time.monotonic() + timeout
    eventos = painel_servidor.app.state.painel.eventos
    while eventos.total_inscritos < minimo and time.monotonic() < limite:
        time.sleep(0.1)
    assert eventos.total_inscritos >= minimo, f"SSE conectou {eventos.total_inscritos}/{minimo}"


def test_popup_chega_ao_vivo_nos_dois_operadores_e_ciencia_some_nos_dois(navegador, painel_servidor):
    import threading

    eventos = painel_servidor.app.state.painel.eventos
    base_inscritos = eventos.total_inscritos
    ctx1, op1 = _novo_operador(navegador, painel_servidor.url)
    ctx2, op2 = _novo_operador(navegador, painel_servidor.url, "/painel/cameras")      # outra pagina, outro operador
    try:
        _esperar_inscritos(painel_servidor, base_inscritos + 2)
        # a thread de inferencia da IA publica o alerta (aqui simulada por uma thread)
        threading.Thread(target=eventos.on_runtime_event, args=(payload_alerta(painel_servidor.evid, 101),)).start()
        for pg in (op1, op2):
            pg.wait_for_selector("#popup-sess_nav_101", timeout=8000)
            texto = pg.inner_text("#popup-sess_nav_101")
            assert "Criança desacompanhada" in texto and "Demo" in texto and "93%" in texto
            pg.wait_for_function("() => { const i = document.querySelector('#popup-sess_nav_101 img'); return i && i.complete && i.naturalWidth > 0; }", timeout=8000)
        op1.wait_for_timeout(500)                                                        # fim da animacao de entrada
        _capturar(op1, "edge_popup_ao_vivo")
        # "Alertas recentes" atualiza na hora (sem esperar o polling de 10 s)
        op1.wait_for_function("() => document.getElementById('lista-eventos-recentes').innerText.includes('Demo')", timeout=4000)
        assert op1.evaluate("() => document.getElementById('lista-eventos-recentes').innerText.includes('Desacompanhada')")

        op1.click("#popup-sess_nav_101 >> text=Ciente")
        op1.wait_for_selector("#popup-sess_nav_101", state="detached", timeout=5000)
        op2.wait_for_selector("#popup-sess_nav_101", state="detached", timeout=8000)      # `event: ack` pelo SSE
        with painel_servidor.database.session() as db:
            from sqlalchemy import select

            from src.jutta_ped.painel.models import EventoPediatria

            ev = db.scalar(select(EventoPediatria).where(EventoPediatria.event_id == "sess_nav_101"))
            assert ev.reconhecido_por == "admin" and ev.reconhecido_em is not None
        op2.reload()
        op2.wait_for_selector("#lista-cameras")
        assert op2.query_selector("#popup-sess_nav_101") is None                         # com ciencia, nao volta
        assert op1.erros == [] and op2.erros == []
    finally:
        ctx1.close()
        ctx2.close()


def test_popup_sem_ciencia_sobrevive_a_recarregar_sem_duplicar(navegador, painel_servidor):
    eventos = painel_servidor.app.state.painel.eventos
    base_inscritos = eventos.total_inscritos
    ctx, op = _novo_operador(navegador, painel_servidor.url)
    try:
        _esperar_inscritos(painel_servidor, base_inscritos + 1)
        eventos.on_runtime_event(payload_alerta(painel_servidor.evid, 102))
        op.wait_for_selector("#popup-sess_nav_102", timeout=8000)
        op.reload()
        op.wait_for_selector("#popup-sess_nav_102", timeout=8000)
        op.wait_for_timeout(1500)                                                       # tempo p/ qualquer SSE duplicado chegar
        assert op.locator("#popup-sess_nav_102").count() == 1
        op.click("#popup-sess_nav_102 >> text=Ciente")
        op.wait_for_selector("#popup-sess_nav_102", state="detached", timeout=5000)
    finally:
        ctx.close()
