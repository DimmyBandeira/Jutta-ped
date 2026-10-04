"""Tela de especialistas num navegador real (Edge/Chrome via Playwright): listagem, upload recusado e acoes de versao.

Nao precisa de go2rtc nem de video: usa o painel real (uvicorn em thread) da fixture `painel_servidor`.
Pula sozinho sem Playwright ou sem Edge/Chrome. Defina JUTTA_CAPTURAS=<pasta> para salvar capturas de tela.
"""
from __future__ import annotations

import io
import json
import os
import zipfile
from pathlib import Path

import pytest

pytest.importorskip("playwright.sync_api", reason="playwright nao instalado (pip install playwright)")
from playwright.sync_api import sync_playwright  # noqa: E402

from painel_helpers import SENHA_ADMIN  # noqa: E402
from test_especialistas_instalador import PESOS_PT, pacote_zip  # noqa: E402
from test_especialistas_validador import manifesto  # noqa: E402


@pytest.fixture(scope="module")
def navegador():
    with sync_playwright() as pw:
        browser, erros = None, []
        for canal in ("msedge", "chrome"):
            try:
                browser = pw.chromium.launch(channel=canal, headless=True)
                break
            except Exception as exc:  # noqa: BLE001
                erros.append(f"{canal}: {type(exc).__name__}")
        if browser is None:
            pytest.skip(f"nenhum Edge/Chrome utilizavel ({'; '.join(erros)})")
        yield browser
        browser.close()


@pytest.fixture()
def pagina(navegador, painel_servidor):
    contexto = navegador.new_context(viewport={"width": 1280, "height": 900})
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


def _captura(pg, nome: str) -> None:
    pasta = os.environ.get("JUTTA_CAPTURAS")
    if pasta:
        Path(pasta).mkdir(parents=True, exist_ok=True)
        pg.screenshot(path=str(Path(pasta) / f"{nome}.png"), full_page=True)


def _enviar(pg, nome: str, conteudo: bytes) -> None:
    pg.set_input_files("input[name=pacote]", files=[{"name": nome, "mimeType": "application/zip", "buffer": conteudo}])
    pg.click("text=Validar e instalar")
    pg.wait_for_load_state("networkidle")


def _sem_rolagem_horizontal(pg) -> bool:
    return pg.evaluate("document.documentElement.scrollWidth <= document.documentElement.clientWidth")


def test_fluxo_completo_no_navegador(pagina, painel_servidor, tmp_path: Path) -> None:
    pg, base = pagina, painel_servidor.url
    pg.click("nav >> text=Especialistas")
    pg.wait_for_url("**/painel/especialistas")
    assert pg.locator("h3", has_text="Especialistas").is_visible()
    assert pg.locator("text=Nenhum especialista instalado").is_visible()
    assert pg.locator("text=Validar e instalar").is_visible()
    assert _sem_rolagem_horizontal(pg)
    _captura(pg, "1_vazio")

    # upload recusado: protocolo errado + arquivo com codigo
    ruim = io.BytesIO()
    with zipfile.ZipFile(ruim, "w") as z:
        z.writestr("manifest.json", json.dumps(manifesto(protocolo=2)))
        z.writestr("modelos/bebe.pt", PESOS_PT)
        z.writestr("instalar.py", "print(1)")
    _enviar(pg, "ruim.zip", ruim.getvalue())
    assert pg.locator("text=reprovado pelo validador").is_visible()
    assert pg.locator("td", has_text="protocolo_incompativel").is_visible()
    assert pg.locator("td", has_text="codigo_executavel").is_visible()
    assert _sem_rolagem_horizontal(pg)
    _captura(pg, "2_recusado")

    # duas versoes validas, ativar, trocar e voltar
    _enviar(pg, "a.zip", pacote_zip(tmp_path, "a.zip", "1.0.0").read_bytes())
    assert pg.locator("text=instalado. Ele ainda está inativo").is_visible()
    _enviar(pg, "b.zip", pacote_zip(tmp_path, "b.zip", "1.1.0", pesos=PESOS_PT + b"2").read_bytes())
    assert pg.locator("table.tab-versoes tr", has_text="1.1.0").is_visible() and pg.locator("table.tab-versoes tr", has_text="1.0.0").is_visible()
    _captura(pg, "3_instalados")

    pg.on("dialog", lambda d: d.accept())
    pg.locator("table.tab-versoes tr", has_text="1.0.0").get_by_role("button", name="Ativar").click()
    pg.wait_for_load_state("networkidle")
    assert pg.locator("text=1.0.0 é agora a versão ativa").is_visible()
    pg.locator("table.tab-versoes tr", has_text="1.1.0").get_by_role("button", name="Ativar").click()
    pg.wait_for_load_state("networkidle")
    assert pg.locator("table.tab-versoes tr", has_text="1.1.0").locator(".badge", has_text="ativa").is_visible()
    assert pg.get_by_role("button", name="Voltar à versão anterior").is_visible()
    _captura(pg, "4_ativa")

    pg.get_by_role("button", name="Voltar à versão anterior").click()
    pg.wait_for_load_state("networkidle")
    assert pg.locator("table.tab-versoes tr", has_text="1.0.0").locator(".badge", has_text="ativa").is_visible()
    # a versao ativa nao oferece Remover; a inativa sim (com confirmacao)
    assert pg.locator("table.tab-versoes tr", has_text="1.0.0").get_by_role("button", name="Remover").count() == 0
    pg.locator("table.tab-versoes tr", has_text="1.1.0").get_by_role("button", name="Remover").click()
    pg.wait_for_load_state("networkidle")
    assert pg.locator("text=1.1.0 removido").is_visible()
    _captura(pg, "5_removida")
    assert not pg.erros, pg.erros
    assert _sem_rolagem_horizontal(pg)


def test_usuario_comum_nao_ve_o_menu_nem_acessa(navegador, painel_servidor) -> None:
    from src.jutta_ped.painel.models import Papel, Usuario
    from src.jutta_ped.painel.security import hash_senha

    with painel_servidor.database.session() as db:
        db.add(Usuario(login="comum_nav", senha_hash=hash_senha("senha-comum-123"), papel=Papel.comum, ativo=True))
    contexto = navegador.new_context()
    pg = contexto.new_page()
    pg.goto(f"{painel_servidor.url}/login")
    pg.fill("[name=login]", "comum_nav")
    pg.fill("[name=senha]", "senha-comum-123")
    pg.click("button[type=submit]")
    pg.wait_for_url("**/painel/monitoramento")
    assert pg.locator("nav >> text=Especialistas").count() == 0
    resp = pg.goto(f"{painel_servidor.url}/painel/especialistas")
    assert resp.status == 403
    contexto.close()
