"""Receitas do painel: autorizacao/CSRF, persistencia versionada imutavel, validacao contra especialistas ativos, associacao atomica."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from painel_receitas_helpers import BEBE, COR, adm, amb, dados_receita  # noqa: F401
from sqlalchemy import create_engine, inspect, select, text
from test_motor_sombra import _ativar_bebe_v2

from src.jutta_ped.especialistas.executor import Executor, contrato_leve
from src.jutta_ped.motor.receita import hash_config
from src.jutta_ped.painel.db import Database
from src.jutta_ped.painel.models import Auditoria, Camera, Receita, ReceitaVersao


def _acoes(amb) -> list[str]:
    with amb.database.session() as db:
        return [a.acao for a in db.scalars(select(Auditoria).order_by(Auditoria.id)).all()]


def _versoes(amb, nome: str | None = None) -> list[ReceitaVersao]:
    with amb.database.session() as db:
        q = select(ReceitaVersao).order_by(ReceitaVersao.id)
        if nome:
            q = q.join(Receita).where(Receita.nome == nome)
        return list(db.scalars(q).all())


def _flash(amb) -> dict:
    amb.client.get("/painel/receitas")
    return amb.contexto("receitas.html")["flash"]


# ------------------------------------------------------------------------------------------------ autorizacao e CSRF

ROTAS_ADMIN_GET = ["/painel/receitas", "/painel/receitas/nova", "/painel/receitas/1/editar", "/painel/processamento"]
ROTAS_ADMIN_POST = ["/painel/receitas", "/painel/receitas/1/versoes", "/painel/cameras/1/receita", "/painel/cameras/1/sombra"]


@pytest.mark.parametrize("rota", ROTAS_ADMIN_GET)
def test_anonimo_vai_para_o_login_nas_rotas_admin(amb, rota) -> None:
    r = amb.client.get(rota)
    assert r.status_code == 303 and r.headers["location"] == "/login"


@pytest.mark.parametrize("rota", ROTAS_ADMIN_GET)
def test_usuario_comum_recebe_403_nas_rotas_admin(amb, rota) -> None:
    amb.login_comum()
    assert amb.client.get(rota).status_code == 403


@pytest.mark.parametrize("rota", ROTAS_ADMIN_POST)
def test_usuario_comum_nao_muta(adm, rota) -> None:
    adm.client.post("/logout", data={"csrf_token": adm.token()})
    adm.login_comum()
    r = adm.post(rota, {"nome": "x", "acao": "ligar", "receita_versao_id": "1"})
    assert r.status_code == 403
    assert adm.contar(Receita) == 0


@pytest.mark.parametrize("rota", ROTAS_ADMIN_POST)
def test_mutacao_sem_token_csrf_e_recusada(adm, rota) -> None:
    r = adm.post(rota, dados_receita(), token=False)
    assert r.status_code == 403
    assert adm.contar(Receita) == 0


def test_rotas_de_leitura_exigem_login_mas_nao_admin(adm) -> None:
    adm.client.post("/logout", data={"csrf_token": adm.token()})
    adm.login_comum()
    for rota in ("/painel/receitas-resumo", "/painel/sombra/feed", "/painel/indicadores", "/painel/alertas/historico", "/painel/eventos/recentes",
                 "/painel/relatorio/alertas"):
        assert adm.client.get(rota).status_code == 200, rota


# ------------------------------------------------------------------------------------------------ migracao

def _colunas(db: Database, tabela: str) -> set[str]:
    return {c["name"] for c in inspect(db.engine).get_columns(tabela)}


def test_migracao_0003_cria_tabelas_e_colunas(tmp_path: Path) -> None:
    db = Database(f"sqlite:///{tmp_path / 'novo.db'}")
    db.migrar()
    assert {"id", "nome", "descricao", "criada_em"} <= _colunas(db, "receitas")
    assert {"id", "receita_id", "versao", "definicao_json", "config_hash", "criado_por", "criado_em"} <= _colunas(db, "receita_versoes")
    assert {"receita_versao_id", "sombra_ativa"} <= _colunas(db, "cameras")
    with db.engine.connect() as con:
        assert con.execute(text("select version_num from alembic_version")).scalar() == "0004"
    db.dispose()


def _cfg():
    from alembic.config import Config
    cfg = Config()
    cfg.set_main_option("script_location", str(Path(__file__).parents[1] / "src/jutta_ped/painel/migracoes"))
    return cfg


def test_migracao_0003_preserva_dados_e_vinculos_de_destino(tmp_path: Path) -> None:
    """Banco em 0002 com camera + destino + vinculo: a 0003 nao pode apagar os vinculos (o batch do SQLite recriaria `cameras`)."""
    from alembic import command
    url = f"sqlite:///{tmp_path / 'v2.db'}"
    eng = create_engine(url)
    cfg = _cfg()
    with eng.begin() as con:
        cfg.attributes["connection"] = con
        con.exec_driver_sql("PRAGMA foreign_keys=ON")
        command.upgrade(cfg, "0002")
        con.execute(text("insert into destinos (id, nome, tipo, ativo) values (1, 'D', 'webhook', 0)"))
        con.execute(text("insert into cameras (id, nome, fabricante, porta, canal, stream_tipo, ia_pediatria, go2rtc_sync, criado_em) "
                         "values (1, 'Cam', 'intelbras', 554, 1, 'principal', 1, 'ok', '2026-01-01 10:00:00')"))
        con.execute(text("insert into camera_destinos (camera_id, destino_id) values (1, 1)"))
    with eng.begin() as con:
        cfg.attributes["connection"] = con
        con.exec_driver_sql("PRAGMA foreign_keys=ON")
        command.upgrade(cfg, "head")
    with eng.connect() as con:
        assert con.execute(text("select count(*) from camera_destinos")).scalar() == 1
        assert con.execute(text("select nome, receita_versao_id, sombra_ativa from cameras")).one() == ("Cam", None, 0)
    with eng.begin() as con:
        cfg.attributes["connection"] = con
        con.exec_driver_sql("PRAGMA foreign_keys=ON")
        command.downgrade(cfg, "0002")
    with eng.connect() as con:
        assert con.execute(text("select count(*) from camera_destinos")).scalar() == 1
        assert "receitas" not in inspect(con).get_table_names()
    eng.dispose()


# ------------------------------------------------------------------------------------------------ criacao e versionamento

def test_criar_receita_grava_v1_com_hash_e_audita(adm) -> None:
    r = adm.post("/painel/receitas", dados_receita())
    assert r.status_code == 303 and r.headers["location"] == "/painel/receitas"
    [v] = _versoes(adm)
    definicao = json.loads(v.definicao_json)
    assert (v.versao, definicao["id"], definicao["versao"]) == (1, "uniforme-bebe", "1") and v.criado_por == "admin"
    assert v.config_hash == hash_config(definicao, ("uniforme-bebe", "1")) and v.config_hash.startswith("sha256:")
    assert [x["especialista"] for x in definicao["vinculos"]] == [BEBE, COR]
    assert definicao["vinculos"][0] == {"especialista": BEBE, "orcamento_ms": 500, "margem_recorte": 0}
    assert definicao["regra"] == {"combinacao": "todas", "condicoes": [{"especialista": BEBE, "atributo": "bebe_no_colo", "igual": "sim"},
                                                                       {"especialista": COR, "atributo": "cor_uniforme", "igual": "azul"}]}
    assert "receita_criada" in _acoes(adm)
    assert _flash(adm)["tipo"] == "ok"


def test_pagina_de_receitas_segue_o_contrato(adm) -> None:
    vid = adm.criar_receita()
    cam = adm.criar_camera()
    adm.associar_legado(cam, vid)                                       # multi-especialista: so existe como associacao legada
    adm.client.get("/painel/receitas")
    ctx = adm.contexto("receitas.html")
    [rec] = ctx["receitas"]
    assert rec["nome"] == "uniforme-bebe" and rec["versao"] == 1 and rec["valida"] is True and rec["motivos"] == []
    assert rec["cameras"] == ["Recepção"] and {e["id"] for e in rec["especialistas"]} == {BEBE, COR}
    assert set(rec["versoes"][0]) >= {"id", "versao", "config_hash", "criado_em", "criado_por", "resumo"}
    ids = {e["id"]: e for e in ctx["especialistas_ativos"]}
    assert ids[COR]["versao"] == "1.0.0" and {"nome": "cor_uniforme", "tipo": "categoria", "categorias": ["azul", "verde", "outro"]} in ids[COR]["atributos"]
    [c] = ctx["cameras_receita"]
    assert (c["id"], c["receita_versao_id"], c["receita_label"], c["valida"], c["sombra_ativa"], c["sombra_estado"]) == (cam, vid, "uniforme-bebe v1", True, False, "desligada")
    assert ctx["opcoes_versao"] == [{"id": vid, "label": "uniforme-bebe v1"}]


def test_nova_versao_e_imutavel_e_sequencial(adm) -> None:
    adm.criar_receita()
    v1_antes = _versoes(adm)[0]
    rid = adm.contar(Receita) and _versoes(adm)[0].receita_id
    r = adm.post(f"/painel/receitas/{rid}/versoes", dados_receita(cond_igual=["sim", "verde", "", ""], descricao="v2"))
    assert r.status_code == 303
    v1, v2 = _versoes(adm)
    assert (v1.versao, v2.versao) == (1, 2) and json.loads(v2.definicao_json)["versao"] == "2"
    assert v1.definicao_json == v1_antes.definicao_json and v1.config_hash == v1_antes.config_hash      # a v1 nao mudou
    assert v1.config_hash != v2.config_hash
    assert json.loads(v2.definicao_json)["regra"]["condicoes"][1]["igual"] == "verde"
    with pytest.raises(ValueError, match="imutavel"):
        with adm.database.session() as db:
            db.get(ReceitaVersao, v1.id).definicao_json = "{}"
    assert _versoes(adm)[0].definicao_json == v1_antes.definicao_json


def test_editar_traz_a_ultima_versao_preenchida(adm) -> None:
    adm.criar_receita()
    rid = _versoes(adm)[0].receita_id
    assert adm.client.get(f"/painel/receitas/{rid}/editar").status_code == 200
    ctx = adm.contexto("receitas_form.html")
    f = ctx["form"]
    assert ctx["editando"] is True and f["rid"] == rid and f["nome"] == "uniforme-bebe" and f["combinacao"] == "todas"
    assert f["vinculos"][BEBE] == {"usar": True, "orcamento_ms": "500", "margem_recorte": "0"}
    assert len(f["condicoes"]) == 4 and f["condicoes"][1] == {"especialista": COR, "atributo": "cor_uniforme", "igual": "azul", "validade_ms": ""}
    assert f["condicoes"][3]["especialista"] == ""
    assert adm.client.get("/painel/receitas/nova").status_code == 200
    assert adm.contexto("receitas_form.html")["editando"] is False


def test_linhas_de_condicao_vazias_sao_ignoradas_e_tipos_convertidos(adm) -> None:
    r = adm.post("/painel/receitas", dados_receita(
        nome="so-uma", vinc_usar=[BEBE], cond_esp=["", BEBE, "", ""], cond_attr=["", "qtd_bebes", "", ""], cond_igual=["", "2", "", ""], cond_validade=["", "1000", "", ""]))
    assert r.status_code == 303, adm.contexto("receitas_form.html")
    [v] = _versoes(adm)
    assert json.loads(v.definicao_json)["regra"]["condicoes"] == [{"especialista": BEBE, "atributo": "qtd_bebes", "igual": 2, "validade_ms": 1000}]


@pytest.mark.parametrize("mexer, trecho", [
    ({"nome": "AB"}, "Nome inválido"),
    ({"nome": "Com Espaco"}, "Nome inválido"),
    ({"cond_attr": ["nao_existe", "cor_uniforme", "", ""]}, "inexistente"),
    ({"cond_igual": ["talvez", "azul", "", ""]}, "sim"),
    ({"cond_igual": ["sim", "roxo", "", ""]}, "categorias"),
    ({f"vinc_orcamento__{BEBE}": "abc"}, "não é um número"),
    ({f"vinc_orcamento__{BEBE}": "nan"}, "não é um número"),
    ({f"vinc_margem__{BEBE}": "7"}, "margem_recorte"),
    ({"vinc_usar": []}, "ao menos um especialista"),
    ({"cond_esp": ["", "", "", ""], "cond_attr": ["", "", "", ""], "cond_igual": ["", "", "", ""]}, "ao menos uma condição"),
    ({"cond_esp": [BEBE, COR, "", ""], "cond_attr": ["bebe_no_colo", "", "", ""]}, "condição 2"),
    ({"cond_esp": ["br.nao.instalado", COR, "", ""]}, "sem vinculo"),
    ({"combinacao": "tanto-faz"}, "Combinação"),
    ({"cond_validade": ["abc", "", "", ""]}, "validade"),
    ({"cond_validade": ["999999", "", "", ""]}, "validade_ms"),
    ({"descricao": "x" * 301}, "Descrição"),
])
def test_receita_invalida_vira_erro_legivel_e_nada_e_gravado(adm, mexer, trecho) -> None:
    r = adm.post("/painel/receitas", dados_receita(**mexer))
    assert r.status_code == 422, r.text[:300]
    ctx = adm.contexto("receitas_form.html")
    assert ctx["erro"] and ctx["problemas"] and any(trecho in p for p in ctx["problemas"]), ctx["problemas"]
    assert ctx["editando"] is False and ctx["form"]["nome"] == mexer.get("nome", "uniforme-bebe")
    assert adm.contar(Receita) == 0 and adm.contar(ReceitaVersao) == 0


def test_numero_invalido_em_atributo_numerico_e_erro_legivel(adm) -> None:
    r = adm.post("/painel/receitas", dados_receita(vinc_usar=[BEBE], cond_esp=[BEBE, "", "", ""], cond_attr=["qtd_bebes", "", "", ""], cond_igual=["muitos", "", "", ""]))
    assert r.status_code == 422
    assert any("não é um número" in p and "qtd_bebes" in p for p in adm.contexto("receitas_form.html")["problemas"])
    assert adm.contar(Receita) == 0


def test_form_invalido_preserva_os_valores_digitados(adm) -> None:
    adm.post("/painel/receitas", dados_receita(nome="digitado-ok", **{f"vinc_orcamento__{BEBE}": "xyz", "descricao": "minha descricao"}))
    f = adm.contexto("receitas_form.html")["form"]
    assert f["nome"] == "digitado-ok" and f["descricao"] == "minha descricao" and f["vinculos"][BEBE]["orcamento_ms"] == "xyz" and f["vinculos"][BEBE]["usar"]
    assert f["condicoes"][0] == {"especialista": BEBE, "atributo": "bebe_no_colo", "igual": "sim", "validade_ms": ""}


def test_nome_duplicado_e_recusado_sem_alterar_a_existente(adm) -> None:
    adm.criar_receita()
    antes = [(v.id, v.definicao_json) for v in _versoes(adm)]
    r = adm.post("/painel/receitas", dados_receita(cond_igual=["nao", "azul", "", ""]))
    assert r.status_code == 422 and any("Já existe" in p for p in adm.contexto("receitas_form.html")["problemas"])
    assert [(v.id, v.definicao_json) for v in _versoes(adm)] == antes and adm.contar(Receita) == 1


def test_nova_versao_invalida_nao_cria_nada(adm) -> None:
    adm.criar_receita()
    rid = _versoes(adm)[0].receita_id
    r = adm.post(f"/painel/receitas/{rid}/versoes", dados_receita(cond_attr=["nao_existe", "cor_uniforme", "", ""]))
    assert r.status_code == 422 and adm.contexto("receitas_form.html")["editando"] is True
    assert len(_versoes(adm)) == 1
    assert adm.post("/painel/receitas/999/versoes", dados_receita()).status_code == 303
    assert _flash(adm)["tipo"] == "erro"


def test_sem_especialista_ativo_a_receita_e_recusada(amb) -> None:
    amb.login_admin()
    r = amb.post("/painel/receitas", dados_receita())
    assert r.status_code == 422 and any("sem versao ativa" in p for p in amb.contexto("receitas_form.html")["problemas"])
    assert amb.contar(Receita) == 0


# ------------------------------------------------------------------------------------------------ contrato leve

def test_contrato_leve_igual_ao_do_executor_e_sem_carregar_modelos(adm) -> None:
    completo = Executor(adm.inst, backends={"pt": adm.backend, "openvino": adm.backend}).contrato(BEBE)
    cargas = len(adm.backend.cargas)
    leve = contrato_leve(adm.inst, BEBE)
    assert leve == completo and len(adm.backend.cargas) == cargas
    assert contrato_leve(adm.inst, "br.inexistente") is None


def test_validacao_nas_rotas_nao_carrega_modelo(adm) -> None:
    cargas = len(adm.backend.cargas)
    adm.criar_receita()
    adm.client.get("/painel/receitas")
    adm.client.get("/painel/receitas-resumo")
    assert len(adm.backend.cargas) == cargas


# ------------------------------------------------------------------------------------------------ associacao


def test_associar_valida_e_remover(adm) -> None:
    vid, cam = adm.criar_receita(nome="so-bebe", vinc_usar=[BEBE], cond_esp=[BEBE, "", "", ""], cond_attr=["bebe_no_colo", "", "", ""], cond_igual=["sim", "", "", ""]), adm.criar_camera()
    r = adm.post(f"/painel/cameras/{cam}/receita", {"receita_versao_id": str(vid)})
    assert r.status_code == 303 and adm.camera(cam).receita_versao_id == vid and "camera_receita_associada" in _acoes(adm)
    assert adm.camera(cam).sombra_ativa is False                       # associar nao liga a sombra
    adm.post(f"/painel/cameras/{cam}/receita", {"receita_versao_id": ""})
    assert adm.camera(cam).receita_versao_id is None and "camera_receita_removida" in _acoes(adm)


def test_receita_invalida_pelo_especialista_preserva_a_associacao_anterior(adm) -> None:
    adm.criar_receita(nome="so-cor", vinc_usar=[COR], cond_esp=[COR, "", "", ""], cond_attr=["cor_uniforme", "", "", ""], cond_igual=["azul", "", "", ""])
    v_cor = adm.ultima_versao_id()
    v_bebe = adm.criar_receita(nome="so-bebe", vinc_usar=[BEBE], cond_esp=[BEBE, "", "", ""], cond_attr=["bebe_no_colo", "", "", ""], cond_igual=["sim", "", "", ""])
    cam = adm.criar_camera()
    adm.post(f"/painel/cameras/{cam}/receita", {"receita_versao_id": str(v_cor)})
    _ativar_bebe_v2(adm.inst, adm.tmp, lambda m: m["atributos"].pop(0))        # bebe_no_colo deixa de existir

    r = adm.post(f"/painel/cameras/{cam}/receita", {"receita_versao_id": str(v_bebe)})
    flash = _flash(adm)
    assert r.status_code == 303 and flash["tipo"] == "erro" and "inexistente" in flash["texto"]
    assert adm.camera(cam).receita_versao_id == v_cor                          # a anterior ficou
    assert "camera_receita_recusada" in _acoes(adm)


def test_mudanca_de_versao_do_especialista_invalida_a_associacao_sem_excecao(adm) -> None:
    vid, cam = adm.criar_receita(), adm.criar_camera()
    adm.associar_legado(cam, vid)
    _ativar_bebe_v2(adm.inst, adm.tmp, lambda m: m["atributos"].pop(0))
    assert adm.client.get("/painel/receitas").status_code == 200
    ctx = adm.contexto("receitas.html")
    [c] = ctx["cameras_receita"]
    assert c["valida"] is False and c["receita_versao_id"] == vid and any("bebe_no_colo" in m for m in c["motivos"])
    [rec] = ctx["receitas"]
    assert rec["valida"] is False and rec["motivos"] == c["motivos"]
    assert {e["id"]: e["versao_ativa"] for e in rec["especialistas"]}[BEBE] == "2.0.0"
    # fragmentos e monitoramento tambem seguem sem excecao
    assert adm.client.get("/painel/receitas-resumo").status_code == 200 and adm.client.get("/painel/monitoramento").status_code == 200
    adm.inst.reverter(BEBE)
    adm.client.get("/painel/receitas")
    assert adm.contexto("receitas.html")["cameras_receita"][0]["valida"] is True        # voltou a valer sem regravar nada


def test_receitas_resumo_segue_o_contrato(adm) -> None:
    adm.criar_receita()
    assert adm.client.get("/painel/receitas-resumo").status_code == 200
    [r] = adm.contexto("partials/receitas_resumo.html")["receitas"]
    assert set(r) >= {"id", "nome", "versao", "descricao", "especialistas", "cameras", "valida", "motivos"}
    assert r["descricao"] == "receita de teste" and {"id": BEBE, "versao_ativa": "1.0.0"} in r["especialistas"]


def test_associar_camera_ou_versao_inexistente_e_entrada_invalida(adm) -> None:
    vid, cam = adm.criar_receita(), adm.criar_camera()
    for dados, caminho in (({"receita_versao_id": "999"}, f"/painel/cameras/{cam}/receita"), ({"receita_versao_id": str(vid)}, "/painel/cameras/999/receita"),
                           ({"receita_versao_id": "abc"}, f"/painel/cameras/{cam}/receita")):
        assert adm.post(caminho, dados).status_code == 303 and _flash(adm)["tipo"] == "erro"
    assert adm.camera(cam).receita_versao_id is None


def test_remover_receita_da_camera_desliga_a_sombra(adm) -> None:
    vid, cam = adm.criar_receita(nome="so-bebe", vinc_usar=[BEBE], cond_esp=[BEBE, "", "", ""], cond_attr=["bebe_no_colo", "", "", ""], cond_igual=["sim", "", "", ""]), adm.criar_camera()
    adm.post(f"/painel/cameras/{cam}/receita", {"receita_versao_id": str(vid)})
    adm.post(f"/painel/cameras/{cam}/sombra", {"acao": "ligar"})
    assert adm.camera(cam).sombra_ativa is True
    adm.post(f"/painel/cameras/{cam}/receita", {"receita_versao_id": ""})
    c = adm.camera(cam)
    assert c.receita_versao_id is None and c.sombra_ativa is False


def test_htmx_recebe_hx_redirect_em_vez_de_303(adm) -> None:
    cam = adm.criar_camera()
    r = adm.post(f"/painel/cameras/{cam}/sombra", {"acao": "ligar"}, headers={"HX-Request": "true"})
    assert r.status_code == 200 and r.headers["HX-Redirect"] == "/painel/receitas" and adm.camera(cam).sombra_ativa is False
