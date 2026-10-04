"""Administracao de especialistas no painel: so admin, CSRF, upload seguro, auditoria e uso do servico de instalacao."""
from __future__ import annotations

import io
import json
import re
import zipfile
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select

from src.jutta_ped.especialistas.instalador import Instalador
from src.jutta_ped.painel.models import Auditoria, Papel, Usuario
from src.jutta_ped.painel.panel import mount_panel
from src.jutta_ped.painel.security import hash_senha
from test_especialistas_instalador import PESOS_PT, pacote_zip
from test_especialistas_validador import manifesto

SENHA = "senha-admin-123"


@pytest.fixture()
def painel(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("JUTTA_ADMIN_PASSWORD", SENHA)
    monkeypatch.setenv("JUTTA_ADMIN_LOGIN", "admin")
    app = FastAPI()
    database = mount_panel(app, db_url=f"sqlite:///{(tmp_path / 'painel.db').as_posix()}", runtime_dir=tmp_path / "rt", background=False)
    with TestClient(app, follow_redirects=False) as client:
        yield client, database, app
    database.dispose()


def _token(client: TestClient, path: str = "/login") -> str:
    html = client.get(path).text
    m = re.search(r'name="csrf_token" value="([^"]+)"', html) or re.search(r'"X-CSRF-Token": "([^"]+)"', html)
    assert m, html[:300]
    return m.group(1)


def _login(client: TestClient, login: str = "admin", senha: str = SENHA) -> None:
    r = client.post("/login", data={"login": login, "senha": senha, "csrf_token": _token(client)})
    assert r.status_code in (302, 303), r.text[:200]


def _zip_bytes(tmp_path: Path, nome: str = "p.zip", **kw) -> bytes:
    return pacote_zip(tmp_path, nome, **kw).read_bytes()


def _enviar(client: TestClient, conteudo: bytes, nome: str = "pacote.zip", token: str | None = None):
    token = token or _token(client, "/painel/especialistas")
    return client.post("/painel/especialistas/instalar", data={"csrf_token": token},
                       files={"pacote": (nome, io.BytesIO(conteudo), "application/zip")})


def _acoes(database) -> list[str]:
    with database.session() as db:
        return [a.acao for a in db.scalars(select(Auditoria).order_by(Auditoria.id)).all()]


def _post(client: TestClient, caminho: str):
    return client.post(caminho, data={"csrf_token": _token(client, "/painel/especialistas")})


# --- permissao -------------------------------------------------------------------------------------------

def test_anonimo_vai_para_o_login_e_usuario_comum_recebe_403(painel) -> None:
    client, database, _ = painel
    assert client.get("/painel/especialistas").status_code in (302, 303, 307)
    with database.session() as db:
        db.add(Usuario(login="comum", senha_hash=hash_senha("senha-comum-123"), papel=Papel.comum, ativo=True))
    _login(client, "comum", "senha-comum-123")
    assert client.get("/painel/especialistas").status_code == 403
    token = _token(client, "/painel/monitoramento")
    r = client.post("/painel/especialistas/instalar", data={"csrf_token": token}, files={"pacote": ("x.zip", b"x", "application/zip")})
    assert r.status_code == 403


def test_post_sem_token_csrf_e_recusado(painel, tmp_path: Path) -> None:
    client, _, _ = painel
    _login(client)
    r = client.post("/painel/especialistas/instalar", files={"pacote": ("x.zip", _zip_bytes(tmp_path), "application/zip")})
    assert r.status_code == 403
    assert client.post("/painel/especialistas/recuperar").status_code == 403


def test_pagina_do_admin_lista_vazio_e_tem_link_no_menu(painel) -> None:
    client, _, _ = painel
    _login(client)
    html = client.get("/painel/especialistas").text
    assert "Nenhum especialista instalado" in html and 'href="/painel/especialistas"' in html
    assert "não" in html and "estática" in html           # aviso de escopo da validacao


# --- instalar ---------------------------------------------------------------------------------------------

def test_upload_valido_instala_inativo_e_audita(painel, tmp_path: Path) -> None:
    client, database, app = painel
    _login(client)
    r = _enviar(client, _zip_bytes(tmp_path))
    assert r.status_code == 303 and r.headers["location"] == "/painel/especialistas"
    html = client.get("/painel/especialistas").text
    assert "br.teste.bebe" in html and "instalado" in html and "inativo" in html
    reg = app.state.especialistas.listar()["br.teste.bebe"]
    assert reg["ativa"] is None and reg["versoes"]["1.0.0"]["estado"] == "instalada"
    assert "especialista_instalado" in _acoes(database)
    assert not list((app.state.especialistas.raiz / "uploads").iterdir())      # upload temporario removido


def test_pacote_reprovado_mostra_o_relatorio_e_nao_instala(painel, tmp_path: Path) -> None:
    client, database, app = painel
    _login(client)
    ruim = io.BytesIO()
    with zipfile.ZipFile(ruim, "w") as z:
        z.writestr("manifest.json", json.dumps(manifesto(protocolo=2)))
        z.writestr("modelos/bebe.pt", PESOS_PT)
        z.writestr("instalar.py", "print(1)")
    r = _enviar(client, ruim.getvalue())
    assert r.status_code == 422
    assert "protocolo_incompativel" in r.text and "codigo_executavel" in r.text and "instalar.py" in r.text
    assert app.state.especialistas.listar() == {}
    assert "especialista_instalacao_recusada" in _acoes(database)


def test_arquivo_que_nao_e_zip_e_recusado(painel) -> None:
    client, _, app = painel
    _login(client)
    assert _enviar(client, b"x", nome="modelo.exe").status_code == 422
    r = _enviar(client, b"isto nao e um zip", nome="a.zip")
    assert r.status_code == 422 and app.state.especialistas.listar() == {}


def test_nome_do_arquivo_enviado_nunca_vira_caminho(painel, tmp_path: Path) -> None:
    client, _, app = painel
    _login(client)
    r = _enviar(client, _zip_bytes(tmp_path), nome="../../../escapou.zip")
    assert r.status_code == 303
    assert not list(tmp_path.rglob("escapou*"))
    assert not list((app.state.especialistas.raiz / "uploads").iterdir())


def test_upload_acima_do_limite_e_recusado(painel, tmp_path: Path) -> None:
    client, _, app = painel
    _login(client)
    token = _token(client, "/painel/especialistas")
    app.state.especialistas_upload_max = 100
    r = _enviar(client, _zip_bytes(tmp_path), token=token)
    assert r.status_code == 413
    assert app.state.especialistas.listar() == {}


def test_nome_do_pacote_com_html_e_escapado(painel, tmp_path: Path) -> None:
    client, _, _ = painel
    _login(client)
    m = manifesto()
    m["pacote"]["nome"] = "<script>alert(1)</script>"
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("manifest.json", json.dumps(m))
        z.writestr("modelos/bebe.pt", PESOS_PT)
    assert _enviar(client, buf.getvalue()).status_code == 303
    html = client.get("/painel/especialistas").text
    assert "<script>alert(1)</script>" not in html and "&lt;script&gt;alert(1)&lt;/script&gt;" in html


def test_instalacao_com_outra_operacao_em_andamento_responde_409(painel, tmp_path: Path) -> None:
    client, _, app = painel
    _login(client)
    token = _token(client, "/painel/especialistas")
    with Instalador(app.state.especialistas.raiz)._bloqueio():
        r = _enviar(client, _zip_bytes(tmp_path), token=token)
    assert r.status_code == 409 and "em andamento" in r.text
    assert app.state.especialistas.listar() == {}


# --- ativar / reverter / remover / recuperar --------------------------------------------------------------

def _instalar_duas(client: TestClient, tmp_path: Path) -> None:
    assert _enviar(client, _zip_bytes(tmp_path, "a.zip", versao="1.0.0")).status_code == 303
    assert _enviar(client, _zip_bytes(tmp_path, "b.zip", versao="1.1.0", pesos=PESOS_PT + b"2")).status_code == 303


def test_ativar_reverter_e_remover_pelo_painel(painel, tmp_path: Path) -> None:
    client, database, app = painel
    _login(client)
    _instalar_duas(client, tmp_path)
    assert _post(client, "/painel/especialistas/br.teste.bebe/1.0.0/ativar").status_code == 303
    assert _post(client, "/painel/especialistas/br.teste.bebe/1.1.0/ativar").status_code == 303
    assert app.state.especialistas.listar()["br.teste.bebe"]["ativa"] == "1.1.0"
    assert "Voltar à versão anterior" in client.get("/painel/especialistas").text
    assert _post(client, "/painel/especialistas/br.teste.bebe/reverter").status_code == 303
    assert app.state.especialistas.listar()["br.teste.bebe"]["ativa"] == "1.0.0"
    # remover a ativa e recusado (mensagem na tela); remover a inativa funciona
    _post(client, "/painel/especialistas/br.teste.bebe/1.0.0/remover")
    assert "esta ativa" in client.get("/painel/especialistas").text
    assert "1.0.0" in app.state.especialistas.listar()["br.teste.bebe"]["versoes"]
    assert _post(client, "/painel/especialistas/br.teste.bebe/1.1.0/remover").status_code == 303
    assert "1.1.0" not in app.state.especialistas.listar()["br.teste.bebe"]["versoes"]
    acoes = _acoes(database)
    assert {"especialista_ativado", "especialista_revertido", "especialista_removido", "especialista_removido_falhou"} <= set(acoes)


def test_ativar_versao_inexistente_mostra_erro_sem_quebrar(painel) -> None:
    client, _, _ = painel
    _login(client)
    assert _post(client, "/painel/especialistas/br.nao.existe/1.0.0/ativar").status_code == 303
    assert "nao esta instalado" in client.get("/painel/especialistas").text


def test_ativar_versao_adulterada_e_recusado_e_marca_corrompida(painel, tmp_path: Path) -> None:
    client, _, app = painel
    _login(client)
    _enviar(client, _zip_bytes(tmp_path))
    arq = app.state.especialistas.pacotes / "br.teste.bebe" / "1.0.0" / "manifest.json"
    m = json.loads(arq.read_text(encoding="utf-8"))
    m["atributos"][0]["validade_ms"] = 999999
    arq.write_text(json.dumps(m), encoding="utf-8")
    _post(client, "/painel/especialistas/br.teste.bebe/1.0.0/ativar")
    html = client.get("/painel/especialistas").text
    assert "corrompida" in html and app.state.especialistas.listar()["br.teste.bebe"]["ativa"] is None


def test_recuperar_e_registro_corrompido(painel, tmp_path: Path) -> None:
    client, _, app = painel
    _login(client)
    _enviar(client, _zip_bytes(tmp_path))
    inst = app.state.especialistas
    inst.arq_registro.write_text("{ ilegivel", encoding="utf-8")
    inst.arq_bak.write_text("tambem", encoding="utf-8")
    r = client.get("/painel/especialistas")
    assert r.status_code == 200 and "ilegiveis" in r.text
    assert _post(client, "/painel/especialistas/recuperar").status_code == 303
    html = client.get("/painel/especialistas").text
    assert "reconstru" in html or "br.teste.bebe" in html
    assert "1.0.0" in inst.listar()["br.teste.bebe"]["versoes"]


def test_operacoes_recentes_aparecem_na_tela(painel, tmp_path: Path) -> None:
    client, _, _ = painel
    _login(client)
    _enviar(client, _zip_bytes(tmp_path))
    html = client.get("/painel/especialistas").text
    assert "Operações recentes" in html and "instalado" in html
