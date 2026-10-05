"""Configuracao de analise por camera (05f): modo simples no servidor, atomicidade, versionamento imutavel, legado, conversao de
percentuais e isolamento entre cameras. Tudo pelo painel real (rotas + bancos) com pacotes sinteticos."""
from __future__ import annotations

import json

import pytest
from painel_receitas_helpers import BEBE, COR, adm, amb, dados_receita  # noqa: F401
from sqlalchemy import select
from test_especialistas_executor import instalar_ativar, manifesto_bebe

from src.jutta_ped.motor.ajustes import ajustes_de_dict, ajustes_para_dict
from src.jutta_ped.painel.models import Auditoria, Camera, CameraConfigVersao, Receita, ReceitaVersao
from src.jutta_ped.painel.camera_config_service import CAMPOS_CFG

CHAVES_CONTEXTO = {"cam", "especialistas_compat", "receitas_compat", "receitas_fora_modo", "cfg", "efetivo", "perfis", "legado",
                   "diagnostico_ativo", "sombra_estado", "flash", "erro", "problemas"}


# ------------------------------------------------------------------------------------------------ helpers

def so_bebe(adm, nome: str = "so-bebe") -> int:
    return adm.criar_receita(nome=nome, vinc_usar=[BEBE], cond_esp=[BEBE, "", "", ""], cond_attr=["bebe_no_colo", "", "", ""], cond_igual=["sim", "", "", ""])


def so_cor(adm, nome: str = "so-cor") -> int:
    return adm.criar_receita(nome=nome, vinc_usar=[COR], cond_esp=[COR, "", "", ""], cond_attr=["cor_uniforme", "", "", ""], cond_igual=["azul", "", "", ""])


def salvar(adm, cam: int, **campos):
    return adm.post(f"/painel/cameras/{cam}/analise", {k: str(v) for k, v in campos.items()})


def versoes(adm, cam: int) -> list[CameraConfigVersao]:
    with adm.database.session() as db:
        return list(db.scalars(select(CameraConfigVersao).where(CameraConfigVersao.camera_id == cam).order_by(CameraConfigVersao.versao)).all())


def acoes(adm) -> list[str]:
    with adm.database.session() as db:
        return [a.acao for a in db.scalars(select(Auditoria).order_by(Auditoria.id)).all()]


def pagina(adm, cam: int) -> dict:
    adm.limpar_contextos()
    assert adm.client.get(f"/painel/cameras/{cam}/analise").status_code == 200
    return adm.contexto("cameras/analise.html")


def rejeitado(adm, cam: int, **campos) -> dict:
    r = salvar(adm, cam, **campos)
    assert r.status_code == 422, r.text[:300]
    ctx = adm.contexto("cameras/analise.html")
    assert ctx["erro"] and ctx["problemas"]
    return ctx


def instalar_termico(adm) -> None:
    m = manifesto_bebe()
    m["pacote"]["id"] = "br.teste.termico"
    m["entrada"]["fonte"] = "termica"
    instalar_ativar(adm.inst, adm.tmp, m, b"pesos-termico")


def instalar_outra_classe(adm) -> None:
    m = manifesto_bebe()
    m["pacote"]["id"] = "br.teste.carro"
    m["entrada"]["alvo"]["classe_mestre"] = "car"
    instalar_ativar(adm.inst, adm.tmp, m, b"pesos-carro")


# ------------------------------------------------------------------------------------------------ autorizacao e CSRF

ROTAS_GET = ["/analise", "/diagnostico", "/diagnostico/resumo", "/diagnostico/quadro.jpg", "/diagnostico/recorte/1.jpg"]


def test_usuario_comum_recebe_403_e_anonimo_e_redirecionado(adm) -> None:
    cam = adm.criar_camera()
    adm.client.cookies.clear()
    for sufixo in ROTAS_GET:
        r = adm.client.get(f"/painel/cameras/{cam}{sufixo}")
        assert r.status_code == 303 and r.headers["location"] == "/login", sufixo
    assert adm.client.post(f"/painel/cameras/{cam}/analise", data={}).status_code in (303, 403)
    adm.login_comum()
    for sufixo in ROTAS_GET:
        assert adm.client.get(f"/painel/cameras/{cam}{sufixo}").status_code == 403, sufixo
    for sufixo, dados in (("/analise", {}), ("/diagnostico", {"acao": "ligar"})):
        assert adm.post(f"/painel/cameras/{cam}{sufixo}", dados).status_code == 403
    assert adm.camera(cam).diagnostico_ativo is False and versoes(adm, cam) == []


def test_mutacoes_exigem_csrf(adm) -> None:
    cam = adm.criar_camera()
    assert adm.post(f"/painel/cameras/{cam}/analise", {"perfil": "generico"}, token=False).status_code == 403
    assert adm.post(f"/painel/cameras/{cam}/diagnostico", {"acao": "ligar"}, token=False).status_code == 403
    assert versoes(adm, cam) == [] and adm.camera(cam).diagnostico_ativo is False


def test_camera_inexistente_e_404_sem_excecao(adm) -> None:
    assert adm.client.get("/painel/cameras/999/analise").status_code == 404
    assert adm.client.get("/painel/cameras/999/diagnostico").status_code == 404
    assert adm.client.get("/painel/cameras/999/diagnostico/resumo").status_code == 404
    assert adm.client.get("/painel/cameras/999/diagnostico/quadro.jpg").status_code == 404
    assert salvar(adm, 999, perfil="generico").status_code == 404
    assert adm.post("/painel/cameras/999/diagnostico", {"acao": "ligar"}).status_code == 303


# ------------------------------------------------------------------------------------------------ contexto da pagina

def test_contexto_da_pagina_tem_exatamente_as_chaves_do_contrato(adm) -> None:
    cam = adm.criar_camera()
    so_bebe(adm)
    adm.criar_receita()                                                  # multi-especialista
    ctx = pagina(adm, cam)
    assert CHAVES_CONTEXTO <= set(ctx) and ctx["cam"] == {"id": cam, "nome": "Recepção"}
    assert {e["id"] for e in ctx["especialistas_compat"]} == {BEBE, COR}
    assert set(ctx["especialistas_compat"][0]) == {"id", "nome", "versao", "atributos"}
    assert [r["label"] for r in ctx["receitas_compat"]] == ["so-bebe v1"]
    assert set(ctx["receitas_compat"][0]) == {"versao_id", "label", "especialista", "valida", "motivos"}
    assert ctx["receitas_fora_modo"] == [{"label": "uniforme-bebe v1", "motivo": ctx["receitas_fora_modo"][0]["motivo"]}]
    assert "2 especialistas" in ctx["receitas_fora_modo"][0]["motivo"]
    assert ctx["efetivo"] is None and ctx["legado"] is None and ctx["diagnostico_ativo"] is False and ctx["sombra_estado"] == "desligada"
    assert set(ctx["cfg"]) == set(CAMPOS_CFG) and ctx["cfg"]["especialista"] == "" and ctx["cfg"]["roi_ativa"] == ""
    assert {p["nome"] for p in ctx["perfis"]} == {"generico", "pediatria_legado"}
    assert ctx["erro"] is None and ctx["problemas"] == []


def test_receitas_compat_filtra_pelo_especialista_selecionado(adm) -> None:
    cam = adm.criar_camera()
    so_bebe(adm)
    so_cor(adm)
    assert salvar(adm, cam, especialista=BEBE).status_code == 303
    ctx = pagina(adm, cam)
    assert [r["label"] for r in ctx["receitas_compat"]] == ["so-bebe v1"]


# ------------------------------------------------------------------------------------------------ modo simples

def test_nada_selecionado_e_valido_e_nao_cria_versao(adm) -> None:
    cam = adm.criar_camera()
    r = salvar(adm, cam)
    assert r.status_code == 303 and versoes(adm, cam) == [] and "camera_config_salva" in acoes(adm)
    assert "Nenhuma alteração" in pagina(adm, cam)["flash"]["texto"]


def test_especialista_sem_receita_e_valido(adm) -> None:
    cam = adm.criar_camera()
    assert salvar(adm, cam, especialista=BEBE).status_code == 303
    [v] = versoes(adm, cam)
    assert (v.versao, v.especialista_id, v.receita_versao_id, v.criado_por) == (1, BEBE, None, "admin") and adm.camera(cam).receita_versao_id is None
    ctx = pagina(adm, cam)
    assert ctx["cfg"]["especialista"] == BEBE and ctx["efetivo"]["versao"] == 1 and ctx["efetivo"]["receita_label"] is None


def test_especialista_e_receita_simples_associam_e_sincronizam(adm) -> None:
    cam, vid = adm.criar_camera(), so_bebe(adm)
    assert salvar(adm, cam, especialista=BEBE, receita_versao_id=vid).status_code == 303
    [v] = versoes(adm, cam)
    assert v.receita_versao_id == vid and adm.camera(cam).receita_versao_id == vid and v.config_hash.startswith("sha256:")
    ctx = pagina(adm, cam)
    assert ctx["efetivo"] == {"versao": 1, "config_hash": v.config_hash, "ajustes": ctx["efetivo"]["ajustes"], "especialista": BEBE, "receita_label": "so-bebe v1"}
    assert ctx["cfg"]["receita_versao_id"] == str(vid)


def test_receita_sem_especialista_selecionado_e_recusada(adm) -> None:
    cam, vid = adm.criar_camera(), so_bebe(adm)
    ctx = rejeitado(adm, cam, receita_versao_id=vid)
    assert any("Escolha o especialista" in p for p in ctx["problemas"]) and versoes(adm, cam) == [] and adm.camera(cam).receita_versao_id is None


def test_receita_multi_especialista_e_recusada_ao_salvar(adm) -> None:
    cam, vid = adm.criar_camera(), adm.criar_receita()
    ctx = rejeitado(adm, cam, especialista=BEBE, receita_versao_id=vid)
    assert any("fora do modo simples" in p for p in ctx["problemas"]) and versoes(adm, cam) == [] and adm.camera(cam).receita_versao_id is None
    assert ctx["cfg"]["especialista"] == BEBE                           # o digitado volta


def test_receita_de_outro_especialista_e_recusada(adm) -> None:
    cam, vid = adm.criar_camera(), so_cor(adm)
    ctx = rejeitado(adm, cam, especialista=BEBE, receita_versao_id=vid)
    assert any(COR in p and BEBE in p for p in ctx["problemas"]) and versoes(adm, cam) == []


@pytest.mark.parametrize("instalar, pid, trecho", [(instalar_termico, "br.teste.termico", "fonte"), (instalar_outra_classe, "br.teste.carro", "classe")])
def test_especialista_incompativel_e_recusado(adm, instalar, pid, trecho) -> None:
    instalar(adm)
    cam = adm.criar_camera()
    ctx = rejeitado(adm, cam, especialista=pid)
    assert any(trecho in p for p in ctx["problemas"]) and versoes(adm, cam) == []
    assert pid not in {e["id"] for e in pagina(adm, cam)["especialistas_compat"]}


def test_especialista_inexistente_e_receita_inexistente_sao_erros_legiveis(adm) -> None:
    cam = adm.criar_camera()
    assert any("não está ativo" in p for p in rejeitado(adm, cam, especialista="br.nao.existe")["problemas"])
    assert any("inexistente" in p for p in rejeitado(adm, cam, especialista=BEBE, receita_versao_id=9999)["problemas"])
    assert any("inválida" in p for p in rejeitado(adm, cam, especialista=BEBE, receita_versao_id="abc")["problemas"])
    assert versoes(adm, cam) == []


def test_receita_que_deixou_de_valer_nao_e_listada_nem_aceita(adm) -> None:
    from test_motor_sombra import _ativar_bebe_v2
    cam, vid = adm.criar_camera(), so_bebe(adm)
    _ativar_bebe_v2(adm.inst, adm.tmp, lambda m: m["atributos"].pop(0))
    assert pagina(adm, cam)["receitas_compat"] == []
    assert any("inexistente" in p for p in rejeitado(adm, cam, especialista=BEBE, receita_versao_id=vid)["problemas"])


# ------------------------------------------------------------------------------------------------ rota existente de associacao

def test_associar_multi_especialista_pela_rota_e_recusado_e_preserva_a_anterior(adm) -> None:
    cam, simples, multi = adm.criar_camera(), so_bebe(adm), adm.criar_receita()
    assert adm.post(f"/painel/cameras/{cam}/receita", {"receita_versao_id": str(simples)}).status_code == 303
    r = adm.post(f"/painel/cameras/{cam}/receita", {"receita_versao_id": str(multi)})
    assert r.status_code == 303
    adm.client.get("/painel/receitas")
    flash = adm.contexto("receitas.html")["flash"]
    assert flash["tipo"] == "erro" and "fora do modo simples" in flash["texto"]
    assert adm.camera(cam).receita_versao_id == simples and len(versoes(adm, cam)) == 1 and "camera_receita_recusada" in acoes(adm)


def test_associar_receita_simples_define_o_especialista_e_cria_versao(adm) -> None:
    cam, vid = adm.criar_camera(), so_cor(adm)
    assert adm.post(f"/painel/cameras/{cam}/receita", {"receita_versao_id": str(vid)}).status_code == 303
    [v] = versoes(adm, cam)
    assert (v.especialista_id, v.receita_versao_id) == (COR, vid) and adm.camera(cam).receita_versao_id == vid
    assert pagina(adm, cam)["cfg"]["especialista"] == COR
    assert adm.post(f"/painel/cameras/{cam}/receita", {"receita_versao_id": str(vid)}).status_code == 303     # igual: sem versao nova
    assert len(versoes(adm, cam)) == 1


def test_associar_preserva_os_ajustes_e_remover_desliga_a_sombra(adm) -> None:
    cam, vid = adm.criar_camera(), so_bebe(adm)
    assert salvar(adm, cam, especialista=BEBE, receita_versao_id=vid, crop_margem_h=15).status_code == 303
    adm.post(f"/painel/cameras/{cam}/sombra", {"acao": "ligar"})
    assert adm.camera(cam).sombra_ativa is True
    adm.post(f"/painel/cameras/{cam}/receita", {"receita_versao_id": ""})
    c = adm.camera(cam)
    assert c.receita_versao_id is None and c.sombra_ativa is False
    v1, v2 = versoes(adm, cam)
    assert v2.receita_versao_id is None and v2.especialista_id == BEBE and json.loads(v2.ajustes_json) == json.loads(v1.ajustes_json)


def test_importar_receita_multi_continua_permitido_mas_nao_associa(adm) -> None:
    cam = adm.criar_camera()
    definicao = {"vinculos": [{"especialista": BEBE}, {"especialista": COR}],
                 "regra": {"combinacao": "todas", "condicoes": [{"especialista": BEBE, "atributo": "bebe_no_colo", "igual": "sim"}]}}
    r = adm.post("/painel/receitas/importar", {"nome": "importada-multi", "descricao": "", "definicao_json": json.dumps(definicao)})
    assert r.status_code == 303 and adm.contar(Receita) == 1
    assert adm.camera(cam).receita_versao_id is None
    assert adm.post(f"/painel/cameras/{cam}/receita", {"receita_versao_id": str(adm.ultima_versao_id())}).status_code == 303
    assert adm.camera(cam).receita_versao_id is None and versoes(adm, cam) == []


# ------------------------------------------------------------------------------------------------ atomicidade e versionamento

def test_erro_preserva_a_versao_anterior_e_nao_grava_nada(adm) -> None:
    cam, vid = adm.criar_camera(), so_bebe(adm)
    assert salvar(adm, cam, especialista=BEBE, receita_versao_id=vid, crop_margem_h=12).status_code == 303
    antes = versoes(adm, cam)
    ctx = rejeitado(adm, cam, especialista=BEBE, receita_versao_id=vid, crop_margem_h=12, exec_orcamento_ms=-5)
    assert any("Orçamento" in p for p in ctx["problemas"])
    depois = versoes(adm, cam)
    assert [(v.id, v.config_hash, v.ajustes_json) for v in depois] == [(v.id, v.config_hash, v.ajustes_json) for v in antes]
    assert adm.camera(cam).receita_versao_id == vid and "camera_config_recusada" in acoes(adm)
    assert pagina(adm, cam)["efetivo"]["versao"] == 1


def test_salvar_igual_nao_cria_versao_e_mudar_cria_a_proxima(adm) -> None:
    cam, vid = adm.criar_camera(), so_bebe(adm)
    dados = dict(especialista=BEBE, receita_versao_id=vid, crop_margem=10, exec_orcamento_ms=400)
    assert salvar(adm, cam, **dados).status_code == 303
    [v1] = versoes(adm, cam)
    assert salvar(adm, cam, **dados).status_code == 303
    assert len(versoes(adm, cam)) == 1
    assert "Nenhuma alteração" in pagina(adm, cam)["flash"]["texto"]
    assert salvar(adm, cam, **{**dados, "crop_margem": 11}).status_code == 303
    v1b, v2 = versoes(adm, cam)
    assert (v1b.versao, v2.versao) == (1, 2) and v1b.config_hash == v1.config_hash and v2.config_hash != v1.config_hash
    assert v1b.ajustes_json == v1.ajustes_json                           # a v1 nunca mudou


def test_hash_e_estavel_entre_cameras_e_independe_do_formato_digitado(adm) -> None:
    cam1, cam2, vid = adm.criar_camera(), adm.criar_camera(nome="Corredor", ip="10.0.0.6"), so_bebe(adm)
    salvar(adm, cam1, especialista=BEBE, receita_versao_id=vid, crop_margem_h="12", crop_minimo_px="8")
    salvar(adm, cam2, especialista=BEBE, receita_versao_id=vid, crop_minimo_px="8", crop_margem_h="12,0")
    assert versoes(adm, cam1)[0].config_hash == versoes(adm, cam2)[0].config_hash


def test_versao_de_configuracao_e_imutavel_e_cai_com_a_camera(adm) -> None:
    cam = adm.criar_camera()
    salvar(adm, cam, especialista=BEBE)
    with pytest.raises(ValueError, match="imutavel"):
        with adm.database.session() as db:
            db.get(CameraConfigVersao, versoes(adm, cam)[0].id).ajustes_json = "{}"
    assert json.loads(versoes(adm, cam)[0].ajustes_json)["perfil"] is None
    from painel_helpers import _hx
    assert _hx(adm.client, "DELETE", f"/painel/cameras/{cam}").status_code == 200
    assert adm.contar(CameraConfigVersao) == 0


def test_receita_removida_desliga_a_sombra_com_texto_claro(adm) -> None:
    cam, vid = adm.criar_camera(), so_bebe(adm)
    salvar(adm, cam, especialista=BEBE, receita_versao_id=vid)
    adm.post(f"/painel/cameras/{cam}/sombra", {"acao": "ligar"})
    assert adm.camera(cam).sombra_ativa is True
    assert salvar(adm, cam, especialista=BEBE).status_code == 303
    assert adm.camera(cam).sombra_ativa is False and adm.camera(cam).receita_versao_id is None
    assert "DESLIGADA" in pagina(adm, cam)["flash"]["texto"]
    assert len(versoes(adm, cam)) == 2


# ------------------------------------------------------------------------------------------------ legado

def _legada(adm):
    cam, multi = adm.criar_camera(), adm.criar_receita()
    adm.associar_legado(cam, multi)
    return cam, multi


def test_receita_legada_e_identificada_e_continua_como_esta(adm) -> None:
    cam, multi = _legada(adm)
    ctx = pagina(adm, cam)
    assert ctx["legado"] and "uniforme-bebe v1" in ctx["legado"] and "fora do modo simples" in ctx["legado"]
    assert ctx["cfg"]["receita_versao_id"] == "" and ctx["efetivo"] is None
    assert adm.camera(cam).receita_versao_id == multi and versoes(adm, cam) == []     # so olhar nao muda nada
    adm.client.get("/painel/receitas")
    assert adm.contexto("receitas.html")["cameras_receita"][0]["receita_versao_id"] == multi


def test_salvar_ajustes_em_camera_legada_sem_transicao_explicita_e_recusado(adm) -> None:
    cam, multi = _legada(adm)
    adm.post(f"/painel/cameras/{cam}/sombra", {"acao": "ligar"})
    ctx = rejeitado(adm, cam, crop_margem=10)
    assert any("transição explícita" in p for p in ctx["problemas"])
    ctx = rejeitado(adm, cam, especialista=BEBE, crop_margem=10)                       # especialista sem receita tambem exige a transicao
    assert versoes(adm, cam) == [] and adm.camera(cam).receita_versao_id == multi and adm.camera(cam).sombra_ativa is True
    rejeitado(adm, cam, especialista=BEBE, receita_versao_id=multi)                    # manter a multi tambem e recusado
    assert versoes(adm, cam) == [] and adm.camera(cam).receita_versao_id == multi


def test_transicao_explicita_remover_a_receita_legada(adm) -> None:
    cam, _ = _legada(adm)
    adm.post(f"/painel/cameras/{cam}/sombra", {"acao": "ligar"})
    assert salvar(adm, cam, crop_margem=10, remover_receita=1).status_code == 303
    c = adm.camera(cam)
    assert c.receita_versao_id is None and c.sombra_ativa is False and len(versoes(adm, cam)) == 1
    assert pagina(adm, cam)["legado"] is None


def test_transicao_explicita_para_receita_simples(adm) -> None:
    cam, _ = _legada(adm)
    simples = so_bebe(adm)
    assert salvar(adm, cam, especialista=BEBE, receita_versao_id=simples).status_code == 303
    assert adm.camera(cam).receita_versao_id == simples and versoes(adm, cam)[0].receita_versao_id == simples
    assert pagina(adm, cam)["legado"] is None


def test_associar_simples_pela_rota_tira_a_camera_do_legado(adm) -> None:
    cam, _ = _legada(adm)
    simples = so_cor(adm)
    assert adm.post(f"/painel/cameras/{cam}/receita", {"receita_versao_id": str(simples)}).status_code == 303
    assert adm.camera(cam).receita_versao_id == simples and versoes(adm, cam)[0].especialista_id == COR


# ------------------------------------------------------------------------------------------------ conversao e validacao

def test_percentuais_viram_fracoes_e_voltam_a_percentuais(adm) -> None:
    cam = adm.criar_camera()
    r = salvar(adm, cam, perfil="pediatria_legado", roi_ativa="on", roi_x1=10, roi_y1="20,5", roi_x2=90, roi_y2=100, roi_criterio="intersecao",
               roi_intersecao_min=60, crop_margem_h=12, crop_margem_v=8, crop_minimo_px=8, exec_orcamento_ms=400, exec_validade_max_ms=1000, exec_intervalo_min_s="0,5")
    assert r.status_code == 303, adm.contexto("cameras/analise.html")["problemas"]
    [v] = versoes(adm, cam)
    gravado = json.loads(v.ajustes_json)
    assert gravado["roi"] == {"x1": 0.1, "y1": 0.205, "x2": 0.9, "y2": 1.0, "criterio": "intersecao", "intersecao_min": 0.6}
    assert gravado["crop"] == {"margem": None, "margem_h": 0.12, "margem_v": 0.08, "minimo_px": 8}
    assert gravado["execucao"] == {"orcamento_ms": 400.0, "validade_max_ms": 1000.0, "intervalo_min_s": 0.5} and gravado["perfil"] == "pediatria_legado"
    assert ajustes_para_dict(ajustes_de_dict(gravado)) == gravado
    cfg = pagina(adm, cam)["cfg"]
    assert (cfg["roi_ativa"], cfg["roi_x1"], cfg["roi_y1"], cfg["roi_x2"], cfg["roi_y2"], cfg["roi_intersecao_min"]) == ("1", "10", "20.5", "90", "100", "60")
    assert (cfg["crop_margem_h"], cfg["crop_margem_v"], cfg["crop_minimo_px"], cfg["exec_orcamento_ms"], cfg["exec_intervalo_min_s"]) == ("12", "8", "8", "400", "0.5")
    ef = pagina(adm, cam)["efetivo"]["ajustes"]
    assert ef["crop"]["margem_h"] == 0.12 and ef["roi"]["x1"] == 0.1 and ef["execucao"]["orcamento_ms"] == 400.0


def test_efetivo_aplica_o_perfil_e_a_margem_da_receita(adm) -> None:
    cam = adm.criar_camera()
    salvar(adm, cam, perfil="generico")
    assert pagina(adm, cam)["efetivo"]["ajustes"]["crop"]["margem_h"] == 0.1
    salvar(adm, cam, perfil="generico", crop_margem_v=30)
    crop = pagina(adm, cam)["efetivo"]["ajustes"]["crop"]
    assert (crop["margem_h"], crop["margem_v"]) == (0.1, 0.3)


@pytest.mark.parametrize("campos, trecho", [
    ({"roi_ativa": "1", "roi_x1": "abc", "roi_y1": 0, "roi_x2": 50, "roi_y2": 50}, "não é um número"),
    ({"roi_ativa": "1", "roi_x1": 0, "roi_y1": 0, "roi_x2": 150, "roi_y2": 50}, "entre 0 e 100"),
    ({"roi_ativa": "1", "roi_x1": 60, "roi_y1": 0, "roi_x2": 50, "roi_y2": 50}, "x2 deve ser maior"),
    ({"roi_ativa": "1", "roi_x1": 0, "roi_y1": 0, "roi_x2": 50}, "ROI y2"),
    ({"roi_ativa": "1", "roi_x1": 0, "roi_y1": 0, "roi_x2": 50, "roi_y2": 50, "roi_criterio": "magico"}, "criterio"),
    ({"roi_ativa": "1", "roi_x1": 0, "roi_y1": 0, "roi_x2": 50, "roi_y2": 50, "roi_intersecao_min": 0}, "interseção"),
    ({"roi_ativa": "1", "roi_x1": "nan", "roi_y1": 0, "roi_x2": 50, "roi_y2": 50}, "número"),
    ({"crop_margem": -1}, "Margem do recorte"),
    ({"crop_margem_h": 101}, "Margem horizontal"),
    ({"crop_minimo_px": "3.5"}, "inteiro"),
    ({"crop_minimo_px": 500}, "Folga mínima"),
    ({"exec_orcamento_ms": 0}, "Orçamento"),
    ({"exec_orcamento_ms": 70000}, "Orçamento"),
    ({"exec_validade_max_ms": "x"}, "Validade"),
    ({"exec_intervalo_min_s": 99}, "Intervalo"),
    ({"perfil": "inexistente"}, "perfil"),
])
def test_ajuste_invalido_vira_erro_legivel_e_nada_e_gravado(adm, campos, trecho) -> None:
    cam = adm.criar_camera()
    ctx = rejeitado(adm, cam, **campos)
    assert any(trecho in p for p in ctx["problemas"]), ctx["problemas"]
    assert versoes(adm, cam) == [] and "camera_config_recusada" in acoes(adm)
    for k, v in campos.items():
        if k in ctx["cfg"] and k != "roi_ativa":
            assert ctx["cfg"][k] == str(v).strip()                         # o digitado volta
    assert adm.contexto("cameras/analise.html")["diagnostico_ativo"] is False


def test_roi_desativada_ignora_coordenadas_digitadas(adm) -> None:
    cam = adm.criar_camera()
    assert salvar(adm, cam, roi_x1="lixo", crop_margem=5).status_code == 303
    assert json.loads(versoes(adm, cam)[0].ajustes_json)["roi"] is None


# ------------------------------------------------------------------------------------------------ visibilidade

def test_monitoramento_e_lista_mostram_versao_e_hash_curto_da_configuracao(adm) -> None:
    cam, vid = adm.criar_camera(), so_bebe(adm)
    adm.client.get("/painel/monitoramento")
    assert adm.contexto("monitoramento.html")["cameras"][0].config_versao is None
    salvar(adm, cam, especialista=BEBE, receita_versao_id=vid)
    adm.client.get("/painel/monitoramento")
    c = adm.contexto("monitoramento.html")["cameras"][0]
    assert c.config_versao == 1 and c.config_hash and len(c.config_hash) == 10 and versoes(adm, cam)[0].config_hash.startswith("sha256:" + c.config_hash)
    assert c.diagnostico_ativo is False
    adm.client.get("/painel/receitas")
    linha = adm.contexto("receitas.html")["cameras_receita"][0]
    assert (linha["config_versao"], linha["config_hash"]) == (1, c.config_hash) and linha["receita_versao_id"] == vid
    adm.client.get(f"/painel/cameras/{cam}/ia-estado")
