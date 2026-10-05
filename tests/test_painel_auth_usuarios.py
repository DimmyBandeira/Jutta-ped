from __future__ import annotations

import re
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select

from src.jutta_ped.painel.models import Auditoria, Papel, Usuario
from src.jutta_ped.painel.panel import mount_panel
from src.jutta_ped.painel.security import LimitadorLogin, hash_senha, verificar_senha

SENHA_ADMIN = "senha-admin-123"


@pytest.fixture()
def painel(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("JUTTA_ADMIN_PASSWORD", SENHA_ADMIN)
    monkeypatch.setenv("JUTTA_ADMIN_LOGIN", "admin")
    app = FastAPI()
    database = mount_panel(app, db_url=f"sqlite:///{(tmp_path / 'painel.db').as_posix()}", runtime_dir=tmp_path, background=False)
    with TestClient(app, follow_redirects=False) as client:
        yield client, database
    database.dispose()


def _csrf(client: TestClient, path: str = "/login") -> str:
    html = client.get(path).text
    match = re.search(r'name="csrf_token" value="([^"]+)"', html) or re.search(r'"X-CSRF-Token": "([^"]+)"', html)
    assert match, html[:400]
    return match.group(1)


def _login(client: TestClient, login: str = "admin", senha: str = SENHA_ADMIN):
    token = _csrf(client)
    return client.post("/login", data={"login": login, "senha": senha, "csrf_token": token})


def _criar_usuario(database, login: str, papel: Papel = Papel.comum, senha: str = "senha-comum-123", ativo: bool = True) -> int:
    with database.session() as db:
        usuario = Usuario(login=login, senha_hash=hash_senha(senha), papel=papel, ativo=ativo)
        db.add(usuario)
        db.flush()
        return usuario.id


# ---------------------------------------------------------------- seguranca


def test_hash_senha_verifica_e_nao_guarda_texto_puro():
    armazenado = hash_senha("segredo-forte")
    assert "segredo-forte" not in armazenado
    assert verificar_senha("segredo-forte", armazenado)
    assert not verificar_senha("outra", armazenado)
    assert not verificar_senha("x", "lixo-sem-formato")


def test_limitador_bloqueia_apos_falhas_e_libera_com_o_tempo():
    agora = [0.0]
    lim = LimitadorLogin(max_falhas=3, bloqueio_segundos=60, relogio=lambda: agora[0])
    chave = ("ana", "1.1.1.1")
    for _ in range(3):
        assert not lim.bloqueado(chave)
        lim.falhou(chave)
    assert lim.bloqueado(chave)
    agora[0] = 61.0
    assert not lim.bloqueado(chave)


# ---------------------------------------------------------------- login


def test_admin_inicial_criado_uma_vez(painel):
    _client, database = painel
    with database.session() as db:
        admins = db.scalars(select(Usuario)).all()
    assert [(u.login, u.papel) for u in admins] == [("admin", Papel.admin)]


def test_paginas_exigem_login(painel):
    client, _ = painel
    resposta = client.get("/painel/monitoramento")
    assert resposta.status_code == 303
    assert resposta.headers["location"] == "/login"
    assert client.get("/painel/usuarios").status_code == 303


def test_htmx_sem_sessao_recebe_401_com_redirect(painel):
    client, _ = painel
    resposta = client.get("/painel/usuarios", headers={"HX-Request": "true"})
    assert resposta.status_code == 401
    assert resposta.headers["HX-Redirect"] == "/login"


def test_login_correto_abre_sessao_e_registra_auditoria(painel):
    client, database = painel
    resposta = _login(client)
    assert resposta.status_code == 303
    assert resposta.headers["location"] == "/painel/monitoramento"
    pagina = client.get("/painel/monitoramento")
    assert pagina.status_code == 200
    assert "Monitoramento" in pagina.text
    with database.session() as db:
        assert "login" in [a.acao for a in db.scalars(select(Auditoria)).all()]


def test_login_invalido_nao_revela_qual_campo_errou(painel):
    client, _ = painel
    errado_usuario = _login(client, login="nao-existe", senha="qualquer-coisa")
    errada_senha = _login(client, senha="senha-errada-123")
    assert errado_usuario.status_code == errada_senha.status_code == 401
    assert "Usuário ou senha inválidos" in errado_usuario.text
    assert "Usuário ou senha inválidos" in errada_senha.text


def test_login_sem_csrf_e_recusado(painel):
    client, _ = painel
    resposta = client.post("/login", data={"login": "admin", "senha": SENHA_ADMIN})
    assert resposta.status_code == 403


def test_usuario_inativo_nao_entra(painel):
    client, database = painel
    _criar_usuario(database, "bloqueado", ativo=False)
    assert _login(client, "bloqueado", "senha-comum-123").status_code == 401


def test_bloqueio_apos_varias_falhas(painel):
    client, _ = painel
    for _ in range(5):
        assert _login(client, senha="errada-errada").status_code == 401
    assert _login(client).status_code == 429  # mesmo com a senha certa, enquanto bloqueado


def test_logout_encerra_sessao(painel):
    client, _ = painel
    _login(client)
    token = _csrf(client, "/painel/monitoramento")
    assert client.post("/logout", data={"csrf_token": token}).status_code == 303
    assert client.get("/painel/monitoramento").status_code == 303


# ---------------------------------------------------------------- papeis


def test_usuario_comum_acessa_monitoramento_mas_nao_areas_admin(painel):
    client, database = painel
    _criar_usuario(database, "operador")
    assert _login(client, "operador", "senha-comum-123").status_code == 303
    for caminho in ("/painel/monitoramento", "/painel/cameras", "/painel/relatorio"):
        assert client.get(caminho).status_code == 200, caminho
    for caminho in ("/painel/usuarios", "/painel/destinos", "/painel/configuracoes"):
        assert client.get(caminho).status_code == 403, caminho


def test_navegacao_do_comum_nao_mostra_links_admin(painel):
    client, database = painel
    _criar_usuario(database, "operador")
    _login(client, "operador", "senha-comum-123")
    html = client.get("/painel/monitoramento").text
    assert "/painel/usuarios" not in html and "/painel/configuracoes" not in html


def test_navegacao_do_admin_mostra_links_admin(painel):
    client, _ = painel
    _login(client)
    html = client.get("/painel/monitoramento").text
    assert "/painel/usuarios" in html and "/painel/configuracoes" in html and "/painel/receitas" in html and "/painel/processamento" in html
    assert "/painel/destinos" not in html      # pagina ainda em construcao: fora do menu


# ---------------------------------------------------------------- CRUD usuarios


def _hx(client: TestClient, metodo: str, url: str, **kwargs):
    token = _csrf(client, "/painel/usuarios")
    return client.request(metodo, url, headers={"X-CSRF-Token": token, "HX-Request": "true"}, **kwargs)


def test_admin_cria_edita_e_remove_usuario(painel):
    client, database = painel
    _login(client)
    criado = _hx(client, "POST", "/painel/usuarios", data={"login": "Maria", "papel": "comum", "senha": "senha-maria-1"})
    assert criado.status_code == 200 and "maria" in criado.text and 'hx-swap-oob="true"' in criado.text
    with database.session() as db:
        maria = db.scalar(select(Usuario).where(Usuario.login == "maria"))
        assert maria.papel == Papel.comum and verificar_senha("senha-maria-1", maria.senha_hash)
        maria_id = maria.id

    editado = _hx(client, "PUT", f"/painel/usuarios/{maria_id}", data={"papel": "admin", "senha": "", "ativo": "on"})
    assert editado.status_code == 200
    with database.session() as db:
        maria = db.get(Usuario, maria_id)
        assert maria.papel == Papel.admin and verificar_senha("senha-maria-1", maria.senha_hash)  # senha mantida

    assert _hx(client, "DELETE", f"/painel/usuarios/{maria_id}").status_code == 200
    with database.session() as db:
        assert db.get(Usuario, maria_id) is None
        acoes = [a.acao for a in db.scalars(select(Auditoria)).all()]
    assert {"usuario_criado", "usuario_editado", "usuario_removido"} <= set(acoes)


@pytest.mark.parametrize(
    "dados,trecho",
    [
        ({"login": "ab", "papel": "comum", "senha": "senha-longa-1"}, "Login deve ter"),
        ({"login": "valido", "papel": "comum", "senha": "curta"}, "pelo menos 8"),
        ({"login": "valido", "papel": "root", "senha": "senha-longa-1"}, "Papel inválido"),
        ({"login": "admin", "papel": "comum", "senha": "senha-longa-1"}, "Já existe"),
    ],
)
def test_validacoes_de_criacao(painel, dados, trecho):
    client, _ = painel
    _login(client)
    resposta = _hx(client, "POST", "/painel/usuarios", data=dados)
    assert trecho in resposta.text
    assert resposta.headers["HX-Retarget"] == "#form-area"


def test_nao_remove_nem_rebaixa_o_ultimo_admin(painel):
    client, database = painel
    _login(client)
    with database.session() as db:
        admin_id = db.scalar(select(Usuario.id).where(Usuario.login == "admin"))
    remover = _hx(client, "DELETE", f"/painel/usuarios/{admin_id}")
    assert "próprio usuário" in remover.text
    # outro admin existe -> o primeiro pode ser rebaixado; sem outro, nao
    rebaixar = _hx(client, "PUT", f"/painel/usuarios/{admin_id}", data={"papel": "comum", "ativo": "on"})
    assert "último administrador" in rebaixar.text
    desativar = _hx(client, "PUT", f"/painel/usuarios/{admin_id}", data={"papel": "admin"})
    assert "último administrador" in desativar.text
    with database.session() as db:
        admin = db.get(Usuario, admin_id)
        assert admin.papel == Papel.admin and admin.ativo


def test_admin_remove_outro_admin_quando_ha_mais_de_um(painel):
    client, database = painel
    _login(client)
    outro = _criar_usuario(database, "outro-admin", Papel.admin)
    assert _hx(client, "DELETE", f"/painel/usuarios/{outro}").status_code == 200
    with database.session() as db:
        assert db.get(Usuario, outro) is None


def test_mutacao_sem_csrf_e_recusada(painel):
    client, _ = painel
    _login(client)
    resposta = client.post("/painel/usuarios", data={"login": "x1y2z3", "papel": "comum", "senha": "senha-longa-1"})
    assert resposta.status_code == 403


def test_usuario_desativado_perde_a_sessao_na_hora(painel):
    client, database = painel
    uid = _criar_usuario(database, "operador")
    _login(client, "operador", "senha-comum-123")
    assert client.get("/painel/monitoramento").status_code == 200
    with database.session() as db:
        db.get(Usuario, uid).ativo = False
    assert client.get("/painel/monitoramento").status_code == 303


def test_arquivos_estaticos_locais_estao_disponiveis(painel):
    client, _ = painel
    assert client.get("/static/vendor/htmx.min.js").status_code == 200
    assert client.get("/static/css/app.css").status_code == 200
