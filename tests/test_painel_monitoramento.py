from __future__ import annotations

import re
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.jutta_ped.painel.models import Papel, Usuario
from src.jutta_ped.painel.panel import mount_panel
from src.jutta_ped.painel.routes_monitor import ajustar_host
from src.jutta_ped.painel.security import hash_senha
from painel_helpers import FakeGerente, FakeGo2rtc, SENHA_ADMIN, _dados_intelbras, _hx, _login, _token


@pytest.fixture()
def amb(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("JUTTA_ADMIN_PASSWORD", SENHA_ADMIN)
    go2rtc, gerente = FakeGo2rtc(), FakeGerente()
    app = FastAPI()
    database = mount_panel(
        app, db_url=f"sqlite:///{(tmp_path / 'painel.db').as_posix()}", runtime_dir=tmp_path, manager=gerente,
        config_factory=lambda **kw: SimpleNamespace(**kw), background=False, go2rtc_transport=httpx.MockTransport(go2rtc),
    )
    app.state.painel.ia.dormir = lambda _s: None
    app.state.painel_sse_intervalo = 0.01
    app.state.painel_sse_max_pings = 2
    with TestClient(app, follow_redirects=False, base_url="http://painel.local:8765") as client:
        yield SimpleNamespace(client=client, database=database, go2rtc=go2rtc, gerente=gerente, servicos=app.state.painel)
    database.dispose()


def _cadastrar(a, **extra):
    _login(a.client)
    r = _hx(a.client, "POST", "/painel/cameras", data=_dados_intelbras(**extra))
    assert r.status_code == 200
    return r


# ---------------------------------------------------------------- pagina


def test_monitoramento_vazio_orienta_cadastrar(amb):
    _login(amb.client)
    html = amb.client.get("/painel/monitoramento").text
    assert "Nenhuma câmera cadastrada" in html


def test_card_com_player_go2rtc_usando_host_do_navegador(amb):
    _cadastrar(amb)
    html = amb.client.get("/painel/monitoramento").text
    # config padrao aponta p/ 127.0.0.1, mas o operador abriu o painel em painel.local
    assert 'data-player-url="http://painel.local:1984/stream.html?src=cam_1"' in html
    assert "stream sincronizado" in html and "Tocar todas" in html


def test_card_sem_stream_pronto_nao_oferece_player(amb):
    _login(amb.client)
    amb.go2rtc.online = False
    _hx(amb.client, "POST", "/painel/cameras", data=_dados_intelbras())
    html = amb.client.get("/painel/monitoramento").text
    assert 'data-player-url=""' in html and "Aguardando o stream" in html


def test_camera_de_teste_toca_o_stream_existente(amb):
    _login(amb.client)
    amb.go2rtc.streams["entrada_social_vis"] = "x"
    _hx(amb.client, "POST", "/painel/cameras", data={"nome": "Demo", "fabricante": "go2rtc_teste", "go2rtc_stream_existente": "entrada_social_vis"})
    html = amb.client.get("/painel/monitoramento").text
    assert "stream.html?src=entrada_social_vis" in html and "stream de teste" in html


@pytest.mark.parametrize(
    "url,host_painel,esperado",
    [
        ("http://127.0.0.1:1984", "painel.local", "http://painel.local:1984"),
        ("http://localhost:1984/", "10.0.0.7", "http://10.0.0.7:1984"),
        ("http://127.0.0.1:1984", "127.0.0.1", "http://127.0.0.1:1984"),
        ("http://gateway.hospital:1984", "painel.local", "http://gateway.hospital:1984"),
        ("https://127.0.0.1", "painel.local", "https://painel.local"),
    ],
)
def test_ajustar_host(url, host_painel, esperado):
    request = SimpleNamespace(url=SimpleNamespace(hostname=host_painel))
    assert ajustar_host(url, request) == esperado


def test_navegacao_exige_login(amb):
    for caminho in ("/painel/monitoramento", "/painel/cameras/1/overlay", "/painel/cameras/1/ia-estado", "/painel/stream", "/painel/eventos/recentes"):
        assert amb.client.get(caminho).status_code == 303, caminho


# ---------------------------------------------------------------- estado da IA


def test_estado_da_ia_percorre_o_ciclo_de_vida(amb):
    _login(amb.client)
    amb.go2rtc.online = False
    _hx(amb.client, "POST", "/painel/cameras", data=_dados_intelbras())
    estado = lambda: amb.client.get("/painel/cameras/1/ia-estado").text  # noqa: E731
    assert "Iniciando IA" in estado()                       # orquestrador ainda nao rodou
    amb.servicos.ia.reconcile()
    assert "Aguardando stream" in estado()
    amb.go2rtc.online = True
    amb.servicos.sync.reconcile()
    amb.servicos.ia.reconcile()
    assert "Analisando" in estado()
    amb.gerente.operacional = "CRIANCA_DESACOMPANHADA"
    assert "Criança desacompanhada" in estado()
    amb.gerente.operacional = "CRIANCA_ACOMPANHADA"
    assert "Criança acompanhada" in estado()
    amb.gerente.operacional = "UNCERTAIN"
    assert "Incerto" in estado()


def test_estado_da_ia_mostra_erro_de_partida_e_ia_desligada(amb):
    _login(amb.client)
    _hx(amb.client, "POST", "/painel/cameras", data=_dados_intelbras())
    amb.gerente.falhar = True
    amb.servicos.ia.reconcile()
    assert "Erro na IA" in amb.client.get("/painel/cameras/1/ia-estado").text
    _hx(amb.client, "PUT", "/painel/cameras/1", data={k: v for k, v in _dados_intelbras().items() if k != "ia_pediatria"})
    assert "IA desligada" in amb.client.get("/painel/cameras/1/ia-estado").text


def test_ia_estado_de_camera_inexistente_nao_quebra(amb):
    _login(amb.client)
    r = amb.client.get("/painel/cameras/999/ia-estado")
    assert r.status_code == 200 and "IA desligada" in r.text


# ---------------------------------------------------------------- overlay


def test_overlay_devolve_bboxes_da_sessao_ativa(amb):
    _cadastrar(amb)
    amb.servicos.ia.reconcile()
    corpo = amb.client.get("/painel/cameras/1/overlay").json()
    assert corpo["frame_w"] == 1920 and corpo["frame_h"] == 1080
    assert corpo["objects"][0]["label"] == "child" and corpo["objects"][0]["state"] == "CHILD_ALONE"


def test_overlay_vazio_sem_ia_rodando(amb):
    _cadastrar(amb)                                      # IA ainda nao iniciou
    corpo = amb.client.get("/painel/cameras/1/overlay").json()
    assert corpo["objects"] == [] and corpo["frame_w"] is None
    assert amb.client.get("/painel/cameras/404/overlay").json()["objects"] == []


# ---------------------------------------------------------------- SSE / eventos


def test_stream_sse_conecta_e_envia_retry(amb):
    _login(amb.client)
    resposta = amb.client.get("/painel/stream")
    assert resposta.status_code == 200
    assert resposta.headers["content-type"].startswith("text/event-stream")
    linhas = [linha for linha in resposta.text.splitlines() if linha]
    assert linhas == ["retry: 3000", ": ping", ": ping"]


def test_eventos_recentes_vazio_por_enquanto(amb):
    _login(amb.client)
    assert "Nenhum alerta recente" in amb.client.get("/painel/eventos/recentes").text


def test_comum_ve_monitoramento_e_overlay(amb):
    with amb.database.session() as db:
        db.add(Usuario(login="operador", senha_hash=hash_senha("senha-comum-123"), papel=Papel.comum, ativo=True))
    _login(amb.client, "operador", "senha-comum-123")
    assert amb.client.get("/painel/monitoramento").status_code == 200
    assert amb.client.get("/painel/cameras/1/overlay").status_code == 200


def test_pagina_inclui_scripts_locais_do_live_view(amb):
    _cadastrar(amb)
    html = amb.client.get("/painel/monitoramento").text
    assert "/static/js/live.js" in html and "cdn" not in html.lower() and "unpkg" not in html.lower()
    assert amb.client.get("/static/js/live.js").status_code == 200
