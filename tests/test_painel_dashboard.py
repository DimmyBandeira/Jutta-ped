"""Dashboard do sistema-mae: indicadores reais, historico de alertas (zero real), filtros de alertas e relatorio."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

from painel_helpers import _hx
from painel_receitas_helpers import adm, amb, montar  # noqa: F401
from sqlalchemy import select

from src.jutta_ped.painel.models import Camera, EventoPediatria

AGORA = datetime(2026, 10, 4, 15, 30)


def _evento(amb, event_id: str, quando: datetime, camera_id: int = 1, nome: str = "Cam", suprimido: bool = False, ciente: bool = False) -> None:
    with amb.database.session() as db:
        db.add(EventoPediatria(event_id=event_id, camera_id=camera_id, camera_nome=nome, session_id="s", estado="CHILD_ALONE", confianca=0.9,
                               suprimido=suprimido, criado_em=quando, reconhecido_em=quando if ciente else None, reconhecido_por="admin" if ciente else None))


def _fixar_relogio(amb) -> None:
    amb.servicos.eventos._relogio = lambda: AGORA


def _ind(amb) -> dict:
    assert amb.client.get("/painel/indicadores").status_code == 200
    return amb.contexto("partials/indicadores.html")["ind"]


# ------------------------------------------------------------------------------------------------ historico

def test_historico_vazio_tem_zero_real_em_todos_os_periodos(amb) -> None:
    amb.login_admin()
    _fixar_relogio(amb)
    for periodo, unidade, n in (("24h", "hora", 24), ("7d", "dia", 7), ("30d", "dia", 30)):
        j = amb.client.get(f"/painel/alertas/historico?periodo={periodo}").json()
        assert (j["periodo"], j["unidade"], j["camera"], j["total"], j["vazio"]) == (periodo, unidade, None, 0, True)
        assert len(j["pontos"]) == n and all(p["total"] == 0 for p in j["pontos"])
        assert set(j["pontos"][0]) == {"inicio", "rotulo", "total"}
    j = amb.client.get("/painel/alertas/historico").json()                                   # padrao: 24h
    assert j["periodo"] == "24h" and j["pontos"][0]["inicio"] == "2026-10-03T16:00:00" and j["pontos"][-1]["inicio"] == "2026-10-04T15:00:00"
    assert j["pontos"][-1]["rotulo"] == "15h"
    d = amb.client.get("/painel/alertas/historico?periodo=7d").json()
    assert d["pontos"][0]["inicio"] == "2026-09-28T00:00:00" and d["pontos"][-1]["inicio"] == "2026-10-04T00:00:00" and d["pontos"][-1]["rotulo"] == "04/10"


def test_historico_agrupa_por_hora_e_por_dia_sem_contar_suprimidos(amb) -> None:
    amb.login_admin()
    _fixar_relogio(amb)
    _evento(amb, "a", datetime(2026, 10, 4, 15, 10), camera_id=1)
    _evento(amb, "b", datetime(2026, 10, 4, 15, 20), camera_id=2)
    _evento(amb, "c", datetime(2026, 10, 4, 14, 59), camera_id=1)
    _evento(amb, "d", datetime(2026, 10, 3, 17, 0), camera_id=1)
    _evento(amb, "e", datetime(2026, 10, 3, 15, 59), camera_id=1)          # fora da janela de 24 h, dentro de 7 d
    _evento(amb, "f", datetime(2026, 10, 4, 15, 5), camera_id=1, suprimido=True)
    _evento(amb, "g", datetime(2026, 9, 10, 8, 0), camera_id=1)            # 24 dias atras: dentro de 30 d
    _evento(amb, "h", datetime(2026, 8, 1, 8, 0), camera_id=1)             # fora de 30 d
    h = amb.client.get("/painel/alertas/historico?periodo=24h").json()
    por = {p["inicio"]: p["total"] for p in h["pontos"]}
    assert h["total"] == 4 and h["vazio"] is False
    assert (por["2026-10-04T15:00:00"], por["2026-10-04T14:00:00"], por["2026-10-03T17:00:00"]) == (2, 1, 1)
    d = amb.client.get("/painel/alertas/historico?periodo=7d").json()
    por = {p["inicio"]: p["total"] for p in d["pontos"]}
    assert d["total"] == 5 and por["2026-10-04T00:00:00"] == 3 and por["2026-10-03T00:00:00"] == 2
    assert amb.client.get("/painel/alertas/historico?periodo=30d").json()["total"] == 6
    c = amb.client.get("/painel/alertas/historico?periodo=24h&camera=1").json()
    assert c["camera"] == 1 and c["total"] == 3
    assert amb.client.get("/painel/alertas/historico?periodo=24h&camera=2").json()["total"] == 1
    assert amb.client.get("/painel/alertas/historico?periodo=24h&camera=77").json()["vazio"] is True


def test_historico_parametros_invalidos_dao_422_com_detail(amb) -> None:
    amb.login_admin()
    for q in ("periodo=1h", "periodo=24h&camera=abc", "periodo=24h&camera=-1", "periodo="):
        r = amb.client.get(f"/painel/alertas/historico?{q}")
        assert r.status_code == 422 and isinstance(r.json()["detail"], str), q


# ------------------------------------------------------------------------------------------------ indicadores

def test_video_disponivel_nao_e_analise_ativa(adm) -> None:
    ind = _ind(adm)
    assert ind == {"cameras_total": 0, "streams_sincronizados": 0, "analise_em_execucao": 0, "alertas_pendentes": 0, "ia_disponivel": True,
                   "go2rtc": "desconhecido", "go2rtc_verificado_ha_s": None}
    adm.criar_camera()                                                          # cadastra e sincroniza o stream no go2rtc
    adm.criar_camera(nome="Corredor", ip="10.0.0.9")
    ind = _ind(adm)
    assert (ind["cameras_total"], ind["streams_sincronizados"], ind["analise_em_execucao"]) == (2, 2, 0)      # video sim, IA ainda nao
    adm.rodar_ia()
    ind = _ind(adm)
    assert (ind["cameras_total"], ind["streams_sincronizados"], ind["analise_em_execucao"]) == (2, 2, 2)
    adm.gerente.operacional = "ERROR"
    assert _ind(adm)["analise_em_execucao"] == 0 and _ind(adm)["streams_sincronizados"] == 2                 # IA com erro nao conta
    adm.gerente.operacional = "ANALISANDO"
    with adm.database.session() as db:
        db.scalars(select(Camera).where(Camera.nome == "Corredor")).one().go2rtc_sync = "erro"
    assert _ind(adm)["streams_sincronizados"] == 1


def test_camera_de_teste_conta_como_stream_disponivel(adm) -> None:
    adm.go2rtc.streams["entrada_social_vis"] = "x"
    assert _hx(adm.client, "POST", "/painel/cameras", data={"nome": "Demo", "fabricante": "go2rtc_teste", "go2rtc_stream_existente": "entrada_social_vis"}).status_code == 200
    ind = _ind(adm)
    assert ind["streams_sincronizados"] == 1 and ind["analise_em_execucao"] == 0


def test_alertas_pendentes_ignoram_suprimidos_e_os_com_ciencia(adm) -> None:
    _evento(adm, "p1", AGORA)
    _evento(adm, "p2", AGORA)
    _evento(adm, "s1", AGORA, suprimido=True)
    _evento(adm, "c1", AGORA, ciente=True)
    assert _ind(adm)["alertas_pendentes"] == 2
    assert _hx(adm.client, "POST", "/painel/popups/p1/ack").status_code == 200
    assert _ind(adm)["alertas_pendentes"] == 1


def test_sem_gerente_de_ia_o_indicador_diz_indisponivel(tmp_path: Path, monkeypatch) -> None:
    a = montar(tmp_path, monkeypatch, com_ia=False)
    try:
        a.login_admin()
        a.criar_camera()
        ind = _ind(a)
        assert ind["ia_disponivel"] is False and ind["analise_em_execucao"] == 0 and ind["cameras_total"] == 1
        assert a.client.get("/painel/monitoramento").status_code == 200
    finally:
        a._fechar()


# ------------------------------------------------------------------------------------------------ alertas e monitoramento

def test_eventos_recentes_filtra_por_camera_e_pendentes(adm) -> None:
    _evento(adm, "a", AGORA, camera_id=1)
    _evento(adm, "b", AGORA, camera_id=2, ciente=True)
    _evento(adm, "c", AGORA, camera_id=2)
    _evento(adm, "s", AGORA, camera_id=2, suprimido=True)

    def pega(qs: str) -> dict:
        assert adm.client.get(f"/painel/eventos/recentes{qs}").status_code == 200
        return adm.contexto("partials/eventos_recentes.html")

    ctx = pega("")
    assert sorted(a.event_id for a in ctx["alertas"]) == ["a", "b", "c"] and ctx["filtro_camera"] is None and ctx["so_pendentes"] is False
    ctx = pega("?camera=2")
    assert sorted(a.event_id for a in ctx["alertas"]) == ["b", "c"] and ctx["filtro_camera"] == 2
    ctx = pega("?pendentes=1")
    assert sorted(a.event_id for a in ctx["alertas"]) == ["a", "c"] and ctx["so_pendentes"] is True
    ctx = pega("?camera=2&pendentes=1")
    assert [a.event_id for a in ctx["alertas"]] == ["c"]
    assert [a.event_id for a in pega("?camera=abc")["alertas"]] == ["c", "b", "a"]          # filtro invalido e ignorado


def test_relatorio_pagina_e_filtros(adm) -> None:
    cam = adm.criar_camera()
    _evento(adm, "a", datetime(2026, 10, 1, 10, 0), camera_id=cam, nome="Recepção")
    _evento(adm, "b", datetime(2026, 10, 2, 23, 59), camera_id=cam, nome="Recepção", ciente=True)
    _evento(adm, "c", datetime(2026, 10, 3, 9, 0), camera_id=99, nome="Outra")
    _evento(adm, "s", datetime(2026, 10, 3, 9, 1), camera_id=cam, nome="Recepção", suprimido=True)
    assert adm.client.get("/painel/relatorio").status_code == 200
    ctx = adm.contexto("relatorio.html")
    assert ctx["cameras"] == [{"id": cam, "nome": "Recepção"}] and ctx["ativo"] == "relatorio"

    def ids(qs: str) -> list[str]:
        r = adm.client.get(f"/painel/relatorio/alertas{qs}")
        assert r.status_code == 200, r.text[:200]
        return [a.event_id for a in adm.contexto("partials/alertas_tabela.html")["alertas"]]

    assert ids("") == ["c", "b", "a"]                                                      # suprimidos ficam de fora por padrao
    assert ids(f"?camera_id={cam}") == ["b", "a"]
    assert ids("?situacao=pendente") == ["c", "a"] and ids("?situacao=reconhecido") == ["b"] and ids("?situacao=suprimido") == ["s"]
    assert ids("?de=2026-10-02&ate=2026-10-02") == ["b"]                                   # 'ate' sem hora inclui o dia inteiro
    assert ids("?de=2026-10-02") == ["c", "b"] and ids("?ate=2026-10-01") == ["a"]
    assert ids(f"?camera_id={cam}&situacao=pendente&de=2026-09-01&ate=2026-10-30") == ["a"]
    assert "Nenhum alerta" in adm.client.get("/painel/relatorio/alertas?de=2027-01-01").text
    for ruim in ("?camera_id=x", "?situacao=zzz", "?de=ontem", "?de=2026-10-05&ate=2026-10-01"):
        r = adm.client.get(f"/painel/relatorio/alertas{ruim}")
        assert r.status_code == 422 and "badge" in r.text, ruim


def test_monitoramento_traz_campos_extras_por_camera(adm) -> None:
    cam = adm.criar_camera()
    assert adm.client.get("/painel/monitoramento").status_code == 200
    ctx = adm.contexto("monitoramento.html")
    [c] = ctx["cameras"]
    assert (c.id, c.nome, c.receita_label, c.sombra_ativa, c.sombra_estado, c.sombra_erro) == (cam, "Recepção", None, False, "desligada", None)
    assert all(hasattr(c, campo) for campo in ("player_url", "ia_ativa", "ia_estado", "ia_erro", "sync_texto"))
    assert ctx["cameras_opcoes"] == [{"id": cam, "nome": "Recepção"}]
