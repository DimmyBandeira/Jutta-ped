"""Motor minimo em SOMBRA: observacao -> recorte -> especialista ativo -> atributos com validade -> receita -> evento.

Evidencia de CONTRATO: dois pacotes declarativos de conformidade (nenhum pediatrico) e backend FALSO deterministico
(o 'modelo' le os primeiros pixels do recorte). Nao prova qualidade de modelo real nem equivalencia da pediatria.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest
from test_especialistas_executor import (
    BackendFalso,
    ModeloFalso,
    empacotar,
    fabrica,
    instalar_ativar,
    manifesto_bebe,
    manifesto_cor,
)

from src.jutta_ped.especialistas.instalador import Instalador
from src.jutta_ped.motor.motor import MotorSombra, RastroJsonl
from src.jutta_ped.motor.observacao import Observacao, ajustar_bbox, extrair_recorte

BEBE, COR = "br.teste.bebe", "br.teste.uniforme"
ALTURA, LARGURA = 480, 640


class Relogio:
    def __init__(self) -> None:
        self.t = 100.0

    def __call__(self) -> float:
        return self.t


def receita(**extra) -> dict:
    r = {"id": "r.teste", "versao": "1",
         "vinculos": [{"especialista": BEBE, "orcamento_ms": 500}, {"especialista": COR, "orcamento_ms": 500}],
         "regra": {"combinacao": "todas", "condicoes": [{"especialista": BEBE, "atributo": "bebe_no_colo", "igual": "sim"},
                                                         {"especialista": COR, "atributo": "cor_uniforme", "igual": "azul"}]}}
    r.update(extra)
    return r


def quadro(*pintar: tuple[int, int, tuple[int, int, int]]) -> np.ndarray:
    """Quadro preto; `pintar` = (x, y, bgr) escreve o pixel que o modelo falso le (canto sup. esq. do recorte)."""
    q = np.zeros((ALTURA, LARGURA, 3), np.uint8)
    for x, y, bgr in pintar:
        q[y, x] = bgr
    return q


def obs(trilha: int, x: int, y: int, *, cam: str = "cam1", ger: int = 1, quadro_id: int = 10, t: float = 100.0, h: int = 200, w: int = 80, classe: str = "person") -> Observacao:
    return Observacao(cam, ger, quadro_id, trilha, classe, (x, y, x + w, y + h), t)


@pytest.fixture
def mundo(tmp_path: Path):
    inst = Instalador(tmp_path / "raiz")
    instalar_ativar(inst, tmp_path, manifesto_bebe(), b"pesos-bebe")
    instalar_ativar(inst, tmp_path, manifesto_cor(), b"pesos-cor", "modelos/cor.pt")
    backend = BackendFalso(tipo_por_arquivo={"modelos/cor.pt": "classificacao"})
    rel, eventos = Relogio(), []
    motor = MotorSombra(fabrica(inst, backend), habilitado=True, relogio=rel, destino=eventos.append)
    motor.configurar([receita()], {"cam1": {"receita": "r.teste", "versao": "1"}, "cam2": {"receita": "r.teste", "versao": "1"}})
    return motor, rel, eventos, backend, inst, tmp_path


SIM_AZUL = (230, 1, 0)        # bebe sim (0.90, 1 deteccao); uniforme azul (0.90)
NAO_AZUL = (10, 1, 0)         # bebe nao (0.04); uniforme: azul (0.04) vs verde (0.004) -> ainda "azul" (mais provavel)


def veredito(ev: list[dict], trilha: int) -> dict:
    return next(e for e in ev if e["trilha_id"] == trilha)


# --- trajeto completo ------------------------------------------------------------------------------------------

def test_trajeto_completo_ate_a_receita_com_identidades_e_sombra(mundo) -> None:
    motor, rel, eventos, *_ = mundo
    saida = motor.processar("cam1", 1, quadro((50, 60, SIM_AZUL), (300, 100, NAO_AZUL)), [obs(1, 50, 60), obs(2, 300, 100)])
    assert eventos == saida and len(saida) == 2
    e1, e2 = veredito(saida, 1), veredito(saida, 2)
    assert e1["veredito"] == "sim" and e2["veredito"] == "nao"
    assert e1["modo"] == "sombra" and e1["alerta_real"] is False
    assert (e1["camera_ref"], e1["geracao"], e1["quadro_id"]) == ("cam1", 1, 10)
    assert {x["id"] for x in e1["especialistas"]} == {BEBE, COR} and all(x["versao"] == "1.0.0" and x["identidade"] for x in e1["especialistas"])
    assert e1["receita"]["id"] == "r.teste" and e1["receita"]["config_hash"].startswith("sha256:")
    assert [c["resultado"] for c in e1["condicoes"]] == ["sim", "sim"] and [c["resultado"] for c in e2["condicoes"]] == ["nao", "sim"]
    json.dumps(e1)


def test_desabilitado_por_padrao_nao_recorta_nem_executa(mundo) -> None:
    motor, _, eventos, backend, *_ = mundo
    motor.habilitado = False
    cargas = list(backend.cargas)                                                # `configurar` ja resolveu os contratos
    assert motor.processar("cam1", 1, quadro((50, 60, SIM_AZUL)), [obs(1, 50, 60)]) == [] and eventos == []
    assert backend.cargas == cargas and backend.lotes == []
    assert MotorSombra(motor.executor).habilitado is False                      # padrao do construtor


def test_camera_sem_associacao_nao_executa_nada(mundo) -> None:
    motor, _, eventos, backend, *_ = mundo
    assert motor.processar("cam-x", 1, quadro(), [obs(1, 50, 60, cam="cam-x")]) == [] and backend.lotes == []


def test_alvo_so_recebe_a_classe_mestre_declarada(mundo) -> None:
    motor, *_ = mundo
    ev = motor.processar("cam1", 1, quadro((50, 60, SIM_AZUL)), [obs(1, 50, 60, classe="carro")])[0]
    assert ev["veredito"] == "indeterminado" and {c["motivo"] for c in ev["condicoes"]} == {"ausente"}


# --- recorte e coordenadas -------------------------------------------------------------------------------------

def test_recorte_preserva_a_transformacao_quadro_crop() -> None:
    q = np.arange(ALTURA * LARGURA * 3, dtype=np.uint32).astype(np.uint8).reshape(ALTURA, LARGURA, 3)
    r = extrair_recorte(q, (100, 50, 180, 250), margem=0.1)
    assert r.origem_xy == (92, 30) and r.bbox_quadro == (100, 50, 180, 250) and r.bbox_no_recorte == (8, 20, 88, 220)
    assert r.imagem.shape[:2] == (240, 96) and r.altura_alvo_px == 200
    x, y = r.para_quadro(8, 20)
    assert (x, y) == (100, 50) and r.para_recorte(100, 50) == (8, 20)
    assert np.array_equal(r.imagem[20, 8], q[50, 100])                         # o pixel do canto da bbox esta onde a transformacao diz
    antes = q[50:250, 100:180].copy()
    r.imagem[:] = 0
    assert np.array_equal(q[50:250, 100:180], antes)                              # copia: nao altera o quadro


def test_bbox_e_ajustada_ao_quadro_e_recorte_vazio_e_descartado() -> None:
    q = quadro()
    assert ajustar_bbox(q, (-20, -5, 50, 40)) == (0, 0, 50, 40)
    assert ajustar_bbox(q, (600, 400, 700, 600)) == (600, 400, 640, 480)
    assert extrair_recorte(q, (600, 400, 700, 600)).bbox_quadro == (600, 400, 640, 480)
    for ruim in ((700, 10, 800, 90), (10, 10, 10, 90), (50, 50, 20, 90), (float("nan"), 0, 5, 5), (0, 0, float("inf"), 5), None, "x", (1, 2, 3)):
        assert extrair_recorte(q, ruim) is None, ruim
    assert extrair_recorte(None, (0, 0, 5, 5)) is None and extrair_recorte(q, (0, 0, 9, 9), margem=2) is None


def test_cada_trilha_recebe_o_proprio_recorte(mundo) -> None:
    motor, *_ = mundo
    # pixel lido = canto do recorte; se os recortes trocassem de trilha os vereditos trocariam
    ev = motor.processar("cam1", 1, quadro((50, 60, SIM_AZUL), (300, 100, NAO_AZUL), (500, 20, SIM_AZUL)), [obs(3, 500, 20), obs(1, 50, 60), obs(2, 300, 100)])
    assert [veredito(ev, t)["veredito"] for t in (1, 2, 3)] == ["sim", "nao", "sim"]


def test_recorte_vazio_e_recorte_pequeno_viram_indeterminado(mundo) -> None:
    motor, _, _, backend, *_ = mundo
    ev = motor.processar("cam1", 1, quadro(), [obs(1, 700, 10), obs(2, 50, 60, h=100)])
    vazio, pequeno = veredito(ev, 1), veredito(ev, 2)
    assert vazio["veredito"] == "indeterminado" and vazio["descartes"]["recorte_vazio"] == 2 and {c["motivo"] for c in vazio["condicoes"]} == {"recorte_vazio"}
    assert pequeno["veredito"] == "indeterminado" and "recorte_pequeno" in {c["motivo"] for c in pequeno["condicoes"]}


# --- validade, atraso, erro -----------------------------------------------------------------------------------

def test_atributo_expira_conforme_a_validade_do_manifesto(mundo) -> None:
    motor, rel, *_ = mundo
    motor.processar("cam1", 1, quadro((50, 60, SIM_AZUL)), [obs(1, 50, 60)])
    # mesma trilha, sem recorte aproveitavel no quadro novo: so o cache pode sustentar o veredito
    rel.t = 101.0                                                                         # 1000 ms < 1500 ms (bebe) e 5000 ms (uniforme)
    ev = motor.processar("cam1", 1, quadro(), [obs(1, 700, 10, quadro_id=11, t=rel.t)])[0]
    assert ev["veredito"] == "sim" and ev["condicoes"][0]["idade_ms"] == pytest.approx(1000.0, abs=1)
    rel.t = 102.0                                                                         # 2000 ms: bebe expirou, uniforme (5000) ainda vale
    ev = motor.processar("cam1", 1, quadro(), [obs(1, 700, 10, quadro_id=12, t=rel.t)])[0]
    assert [c["resultado"] for c in ev["condicoes"]] == ["indeterminado", "sim"] and ev["condicoes"][0]["motivo"] == "expirado"
    assert ev["veredito"] == "indeterminado"


def test_validade_da_receita_pode_apertar_mas_nao_ampliar(mundo) -> None:
    motor, rel, _, _, inst, tmp = mundo
    r = receita()
    r["regra"]["condicoes"][0]["validade_ms"] = 300
    motor.configurar([r], {"cam1": {"receita": "r.teste", "versao": "1"}})
    motor.processar("cam1", 1, quadro((50, 60, SIM_AZUL)), [obs(1, 50, 60)])
    rel.t = 100.5                                                                         # 500 ms > 300 (receita) < 1500 (manifesto)
    ev = motor.processar("cam1", 1, quadro(), [obs(1, 700, 10, quadro_id=11, t=rel.t)])[0]
    assert ev["condicoes"][0]["resultado"] == "indeterminado" and ev["condicoes"][0]["motivo"] == "expirado"
    r["regra"]["condicoes"][0]["validade_ms"] = 99999
    with pytest.raises(ValueError, match="validade_ms"):
        motor.configurar([r], {"cam1": {"receita": "r.teste", "versao": "1"}})


def test_observacao_atrasada_nao_chega_ao_modelo(mundo) -> None:
    motor, rel, _, backend, *_ = mundo
    rel.t = 100.6                                                                         # capturada ha 600 ms; orcamento 500
    ev = motor.processar("cam1", 1, quadro((50, 60, SIM_AZUL)), [obs(1, 50, 60, t=100.0)])[0]
    assert backend.lotes == [] and ev["veredito"] == "indeterminado" and {c["motivo"] for c in ev["condicoes"]} == {"atrasado"}
    assert ev["descartes"]["orcamento_vencido_antes_de_enviar"] == 2


def test_resultado_que_chega_depois_do_prazo_e_descartado(mundo) -> None:
    motor, rel, _, backend, *_ = mundo
    inferir = ModeloFalso.inferir

    def lento(self, imagens):
        rel.t += 0.8                                                                      # a inferencia "demora" 800 ms de relogio
        return inferir(self, imagens)

    ModeloFalso.inferir = lento
    try:
        ev = motor.processar("cam1", 1, quadro((50, 60, SIM_AZUL)), [obs(1, 50, 60)])[0]
    finally:
        ModeloFalso.inferir = inferir
    assert ev["veredito"] == "indeterminado" and all(c["resultado"] == "indeterminado" for c in ev["condicoes"])
    assert not motor._cache["cam1"]                                                       # nada vencido entra no cache


def test_erro_do_executor_vira_indeterminado_e_nao_derruba_o_laco(mundo) -> None:
    motor, _, _, backend, *_ = mundo
    inferir = ModeloFalso.inferir
    ModeloFalso.inferir = lambda self, imgs: (_ for _ in ()).throw(RuntimeError("gpu caiu"))
    try:
        ev = motor.processar("cam1", 1, quadro((50, 60, SIM_AZUL)), [obs(1, 50, 60)])[0]
    finally:
        ModeloFalso.inferir = inferir
    assert ev["veredito"] == "indeterminado" and {c["motivo"] for c in ev["condicoes"]} == {"E_INTERNO"}
    assert veredito(motor.processar("cam1", 1, quadro((50, 60, SIM_AZUL)), [obs(1, 50, 60, quadro_id=11)]), 1)["veredito"] == "sim"   # recupera


def test_indeterminado_do_especialista_nao_vira_nao(mundo) -> None:
    motor, *_ = mundo
    ev = motor.processar("cam1", 1, quadro((50, 60, (77, 1, 0))), [obs(1, 50, 60)])[0]      # confianca 0.30: entre os limiares
    assert ev["condicoes"][0]["resultado"] == "indeterminado" and ev["condicoes"][0]["motivo"] == "baixa_confianca" and ev["veredito"] == "indeterminado"


# --- isolamento e ordem -----------------------------------------------------------------------------------------

def test_cameras_nao_compartilham_atributos_mesmo_com_mesma_trilha(mundo) -> None:
    motor, rel, *_ = mundo
    motor.processar("cam1", 1, quadro((50, 60, SIM_AZUL)), [obs(1, 50, 60)])
    ev2 = motor.processar("cam2", 1, quadro(), [obs(1, 700, 10, cam="cam2")])[0]        # trilha 1 tambem, mas sem recorte aproveitavel
    assert ev2["veredito"] == "indeterminado" and {c["motivo"] for c in ev2["condicoes"]} == {"recorte_vazio"}
    assert ev2["camera_ref"] == "cam2"


def test_nova_geracao_limpa_a_camera_e_a_antiga_e_descartada(mundo) -> None:
    motor, rel, eventos, *_ = mundo
    motor.processar("cam1", 1, quadro((50, 60, SIM_AZUL)), [obs(1, 50, 60)])
    ev = motor.processar("cam1", 2, quadro(), [obs(1, 700, 10, ger=2, quadro_id=1)])[0]      # mesma trilha id, sessao nova
    assert ev["veredito"] == "indeterminado" and ev["geracao"] == 2                         # nada herdado da sessao 1
    n = len(eventos)
    assert motor.processar("cam1", 1, quadro((50, 60, SIM_AZUL)), [obs(1, 50, 60, quadro_id=99)]) == [] and len(eventos) == n
    assert motor.contadores["descartado_sessao_anterior"] == 1


def test_observacao_de_outra_camera_geracao_ou_futuro_e_rejeitada(mundo) -> None:
    motor, *_ = mundo
    ev = motor.processar("cam1", 1, quadro((50, 60, SIM_AZUL)), [obs(1, 50, 60, cam="cam2"), obs(2, 50, 60, ger=0), obs(3, 50, 60, t=500.0)])
    assert ev == [] and motor.contadores["observacao_rejeitada"] == 3


def test_resultado_mais_antigo_nao_substitui_o_mais_novo(mundo) -> None:
    motor, rel, *_ = mundo
    rel.t = 100.2
    motor.processar("cam1", 1, quadro((50, 60, SIM_AZUL)), [obs(1, 50, 60, quadro_id=20, t=100.1)])
    motor.processar("cam1", 1, quadro((50, 60, NAO_AZUL)), [obs(1, 50, 60, quadro_id=19, t=100.0)])     # quadro mais antigo chegando depois
    e = motor._cache["cam1"][(1, BEBE, "bebe_no_colo")]
    assert e["quadro_id"] == 20 and e["atributo"]["valor"] == "sim" and motor.contadores["resultado_mais_antigo_rejeitado"] >= 1


# --- ativacao e reversao -----------------------------------------------------------------------------------------

def test_ativacao_e_reversao_nao_misturam_versoes(mundo) -> None:
    motor, rel, _, backend, inst, tmp = mundo
    motor.processar("cam1", 1, quadro((50, 60, SIM_AZUL)), [obs(1, 50, 60)])
    assert motor._cache["cam1"][(1, BEBE, "bebe_no_colo")]["versao"] == "1.0.0"
    r2 = inst.instalar(empacotar(tmp, "b2.zip", manifesto_bebe("1.1.0"), b"pesos-bebe-2"))
    inst.ativar(r2.id, r2.versao)
    # novo quadro com recorte vazio: nada de v1.0.0 pode sustentar o veredito
    ev = motor.processar("cam1", 1, quadro(), [obs(1, 700, 10, quadro_id=11)])[0]
    assert ev["condicoes"][0]["resultado"] == "indeterminado" and motor.contadores["cache_limpo_por_troca_de_versao"] == 1
    assert next(x for x in ev["especialistas"] if x["id"] == BEBE)["versao"] == "1.1.0"
    ev = motor.processar("cam1", 1, quadro((50, 60, SIM_AZUL)), [obs(1, 50, 60, quadro_id=12)])[0]
    assert ev["veredito"] == "sim" and motor._cache["cam1"][(1, BEBE, "bebe_no_colo")]["versao"] == "1.1.0"
    inst.reverter(BEBE)
    ev = motor.processar("cam1", 1, quadro(), [obs(1, 700, 10, quadro_id=13)])[0]
    assert ev["condicoes"][0]["resultado"] == "indeterminado" and next(x for x in ev["especialistas"] if x["id"] == BEBE)["versao"] == "1.0.0"
    assert motor.contadores["cache_limpo_por_troca_de_versao"] == 2


def test_versao_nao_aceita_pela_receita_vira_indeterminado(mundo) -> None:
    motor, _, _, _, inst, tmp = mundo
    r = receita()
    r["vinculos"][0]["versoes_aceitas"] = ["1.0.0"]
    motor.configurar([r], {"cam1": {"receita": "r.teste", "versao": "1"}})
    r2 = inst.instalar(empacotar(tmp, "b2.zip", manifesto_bebe("1.1.0"), b"pesos-bebe-2"))
    inst.ativar(r2.id, r2.versao)
    ev = motor.processar("cam1", 1, quadro((50, 60, SIM_AZUL)), [obs(1, 50, 60)])[0]
    assert ev["condicoes"][0]["resultado"] == "indeterminado" and ev["condicoes"][0]["motivo"] == "versao_nao_aceita"


# --- configuracao: validacao e atomicidade -------------------------------------------------------------------

@pytest.mark.parametrize("mexer, trecho", [
    (lambda r: r["regra"]["condicoes"][0].update(atributo="nao_existe"), "inexistente"),
    (lambda r: r["vinculos"].append({"especialista": "br.nao.instalado"}), "sem versao ativa"),
    (lambda r: r["vinculos"][0].update(parametros={"limiar_presente": 5}), "fora de"),
    (lambda r: r["vinculos"][0].update(parametros={"x": 1}), "desconhecido"),
    (lambda r: r["vinculos"][0].update(versoes_aceitas=["9.9.9"]), "versoes_aceitas"),
    (lambda r: r["vinculos"][0].update(margem_recorte=3), "margem_recorte"),
    (lambda r: r["vinculos"][0].update(orcamento_ms=0), "orcamento_ms"),
    (lambda r: r["regra"].update(combinacao="xor"), "combinacao"),
    (lambda r: r["regra"]["condicoes"].append({"especialista": "br.outro", "atributo": "a", "igual": "sim"}), "sem vinculo"),
])
def test_configuracao_invalida_e_recusada_sem_alterar_a_anterior(mundo, mexer, trecho) -> None:
    motor, *_ = mundo
    r = receita()
    mexer(r)
    with pytest.raises(ValueError, match=trecho):
        motor.configurar([r], {"cam1": {"receita": "r.teste", "versao": "1"}})
    assert set(motor._camera) == {"cam1", "cam2"}                                      # configuracao anterior intacta
    assert veredito(motor.processar("cam1", 1, quadro((50, 60, SIM_AZUL)), [obs(1, 50, 60)]), 1)["veredito"] == "sim"


def test_associacao_para_receita_inexistente_e_recusada(mundo) -> None:
    motor, *_ = mundo
    with pytest.raises(ValueError, match="inexistente"):
        motor.configurar([receita()], {"cam1": {"receita": "r.teste", "versao": "2"}})


def test_alvo_quadro_inteiro_ainda_nao_e_despachavel(mundo) -> None:
    motor, _, _, _, inst, tmp = mundo
    m = manifesto_bebe("2.0.0")
    m["pacote"]["id"] = "br.teste.cena"
    m["entrada"]["alvo"] = {"tipo": "quadro"}
    instalar_ativar(inst, tmp, m, b"pesos-cena")
    r = receita(vinculos=[{"especialista": "br.teste.cena"}], regra={"combinacao": "todas", "condicoes": [{"especialista": "br.teste.cena", "atributo": "bebe_no_colo", "igual": "sim"}]})
    with pytest.raises(ValueError, match="quadro"):
        motor.configurar([r], {"cam1": {"receita": "r.teste", "versao": "1"}})


# --- generalidade: pacote novo entra por manifesto/receita --------------------------------------------------------

def test_pacote_novo_entra_so_por_manifesto_e_receita(mundo) -> None:
    motor, rel, eventos, backend, inst, tmp = mundo
    m = manifesto_cor("3.0.0")                                                           # outro dominio: id, atributo e nomes proprios
    m["pacote"]["id"] = "br.exemplo.capacete"
    m["atributos"] = [{"nome": "capacete", "tipo": "tri", "validade_ms": 800,
                       "de": {"modelo": "cor", "tipo": "classificacao", "classe": "cracha", "agregacao": "probabilidade", "limiar_presente": 0.6, "limiar_ausente": 0.2}}]
    instalar_ativar(inst, tmp, m, b"pesos-capacete", "modelos/cor.pt")
    nova = {"id": "r.capacete", "versao": "1", "vinculos": [{"especialista": "br.exemplo.capacete", "margem_recorte": 0.0}],
            "regra": {"combinacao": "alguma", "condicoes": [{"especialista": "br.exemplo.capacete", "atributo": "capacete", "igual": "sim"}]}}
    motor.configurar([receita(), nova], {"cam1": {"receita": "r.teste", "versao": "1"}, "cam3": {"receita": "r.capacete", "versao": "1"}})
    q = quadro()
    q[60, 51, 0] = 250                                                                   # prob. de "cracha" = 250/255
    ev = motor.processar("cam3", 1, q, [obs(1, 50, 60, cam="cam3")])[0]
    assert ev["veredito"] == "sim" and ev["receita"]["id"] == "r.capacete" and [x["id"] for x in ev["especialistas"]] == ["br.exemplo.capacete"]
    ev = motor.processar("cam1", 1, quadro((50, 60, SIM_AZUL)), [obs(1, 50, 60)])[0]    # a receita anterior continua igual
    assert ev["veredito"] == "sim" and {x["id"] for x in ev["especialistas"]} == {BEBE, COR}


def test_o_motor_nao_conhece_nenhum_dominio() -> None:
    import re
    base = Path(__file__).resolve().parents[1] / "src" / "jutta_ped" / "motor"
    texto = "\n".join(p.read_text(encoding="utf-8").lower().replace("pediatria_legado", "") for p in base.glob("*.py"))
    assert not re.search(r"pediatr|crian|child|adult|bebe|infant", texto)


# --- rastro ---------------------------------------------------------------------------------------------------

def test_rastro_jsonl_grava_um_evento_por_linha(mundo, tmp_path: Path) -> None:
    motor, *_ = mundo
    caminho = tmp_path / "sombra" / "rastro.jsonl"
    motor._destino = RastroJsonl(caminho)
    motor.processar("cam1", 1, quadro((50, 60, SIM_AZUL), (300, 100, NAO_AZUL)), [obs(1, 50, 60), obs(2, 300, 100)])
    linhas = [json.loads(x) for x in caminho.read_text(encoding="utf-8").splitlines()]
    assert [x["trilha_id"] for x in linhas] == [1, 2] and all(x["alerta_real"] is False and x["modo"] == "sombra" for x in linhas)


# --- fumaca de integracao com modelo REAL compativel (pula se modelo/video locais nao existirem) ------------------

_RAIZ = Path(__file__).resolve().parents[1]
_MODELO, _VIDEO = _RAIZ / "src" / "models" / "Bebe-no-colo.pt", _RAIZ / "videos" / "Corredor pediatria.mp4"


@pytest.mark.skipif(not (_MODELO.exists() and _VIDEO.exists()), reason="modelo/video locais ausentes")
def test_fumaca_modelo_real_pelo_motor_ate_a_receita(tmp_path: Path) -> None:
    """Prova que o TRAJETO funciona com um .pt real (Ultralytics); NAO avalia qualidade nem equivalencia de nenhum caso de uso."""
    import cv2

    from src.jutta_ped.especialistas.executor import Executor

    m = manifesto_bebe()
    m["entrada"]["formato"].update(largura=960, altura=960)
    m["entrada"]["altura_minima_px"] = 0
    m["atributos"] = [{"nome": "bebe_no_colo", "tipo": "tri", "validade_ms": 1500,
                       "de": {"modelo": "principal", "tipo": "deteccao", "classes": ["bebe_colo"], "agregacao": "maior_confianca", "limiar_presente": 0.4, "limiar_ausente": 0.15}}]
    del m["parametros"]
    inst = Instalador(tmp_path / "raiz")
    instalar_ativar(inst, tmp_path, m, _MODELO.read_bytes())
    cap = cv2.VideoCapture(str(_VIDEO))
    cap.set(cv2.CAP_PROP_POS_FRAMES, max(0, int(cap.get(cv2.CAP_PROP_FRAME_COUNT)) // 2))
    ok, q = cap.read()
    cap.release()
    if not ok:
        pytest.skip("o video local nao pode ser lido neste ambiente")
    rel, eventos = Relogio(), []
    motor = MotorSombra(Executor(inst), habilitado=True, relogio=rel, destino=eventos.append)
    r = {"id": "r.real", "versao": "1", "vinculos": [{"especialista": BEBE, "orcamento_ms": 60000}],
         "regra": {"combinacao": "todas", "condicoes": [{"especialista": BEBE, "atributo": "bebe_no_colo", "igual": "sim"}]}}
    motor.configurar([r], {"cam1": {"receita": "r.real", "versao": "1"}})
    h, w = q.shape[:2]
    ev = motor.processar("cam1", 1, q, [Observacao("cam1", 1, 1, 1, "person", (0, 0, w, h), rel.t)])[0]
    assert ev["veredito"] in ("sim", "nao", "indeterminado") and not ev["erros"] and ev["alerta_real"] is False
    assert ev["condicoes"][0]["resultado"] in ("sim", "nao", "indeterminado") and ev["especialistas"][0]["identidade"]


# --- robustez (achados da revisao independente) -------------------------------------------------------------------

def _cfg(motor, r) -> None:
    motor.configurar([r], {"cam1": {"receita": "r.teste", "versao": "1"}})


@pytest.mark.parametrize("mexer, trecho", [
    (lambda r: r["regra"]["condicoes"][0].update(validade_ms=None), "validade_ms"),
    (lambda r: r["regra"]["condicoes"][0].update(igual="talvez"), "sim"),
    (lambda r: r["regra"]["condicoes"][1].update(igual="azull"), "categorias"),
    (lambda r: r["vinculos"][0].update(parametros=[1]), "objeto"),
    (lambda r: r["vinculos"][0].update(orcamento_ms=float("nan")), "orcamento_ms"),
    (lambda r: r["vinculos"][0].update(margem_recorte=float("nan")), "margem_recorte"),
    (lambda r: r["regra"]["condicoes"][0].update(especialista=["x"]), "sem vinculo|invalida"),
    (lambda r: r["vinculos"][0].update(especialista=["x"]), "invalido"),
])
def test_receita_malformada_vira_valueerror_e_nao_excecao_crua(mundo, mexer, trecho) -> None:
    motor, *_ = mundo
    r = receita()
    mexer(r)
    with pytest.raises(ValueError, match=trecho):
        _cfg(motor, r)


def test_associacao_malformada_e_receita_duplicada(mundo) -> None:
    motor, *_ = mundo
    with pytest.raises(ValueError):
        motor.configurar([receita()], {"cam1": {"receita": ["x"], "versao": "1"}})
    with pytest.raises(ValueError, match="duplicada"):
        motor.configurar([receita(), receita()], {"cam1": {"receita": "r.teste", "versao": "1"}})


def test_observacoes_malformadas_sao_rejeitadas_sem_derrubar_o_laco(mundo) -> None:
    motor, *_ = mundo
    ruins = [{"nao": "observacao"}, obs(1, 50, 60, t=float("nan")), Observacao("cam1", 1, 1, 2, "person", (50, 60, 130, 260), None),  # type: ignore[arg-type]
             obs(3, 50, 60, t=float("inf"))]
    ev = motor.processar("cam1", 1, quadro((50, 60, SIM_AZUL)), [*ruins, obs(4, 50, 60)])
    assert [e["trilha_id"] for e in ev] == [4] and motor.contadores["observacao_rejeitada"] == 4


def test_trilha_repetida_no_mesmo_quadro_e_rejeitada(mundo) -> None:
    motor, *_ = mundo
    ev = motor.processar("cam1", 1, quadro((50, 60, SIM_AZUL), (300, 100, NAO_AZUL)), [obs(1, 50, 60), obs(1, 300, 100)])
    assert len(ev) == 1 and ev[0]["veredito"] == "sim" and motor.contadores["observacao_rejeitada"] == 1


def test_trocar_a_receita_nao_herda_atributos_da_anterior(mundo) -> None:
    motor, *_ = mundo
    motor.processar("cam1", 1, quadro((50, 60, SIM_AZUL)), [obs(1, 50, 60)])
    r2 = receita(versao="2")
    r2["vinculos"][0]["margem_recorte"] = 0.5
    motor.configurar([receita(), r2], {"cam1": {"receita": "r.teste", "versao": "2"}})
    ev = motor.processar("cam1", 1, quadro(), [obs(1, 700, 10, quadro_id=11)])[0]
    assert ev["veredito"] == "indeterminado" and ev["receita"]["versao"] == "2"


def test_perda_do_ativo_nao_deixa_atributo_antigo_sustentar_o_veredito(mundo) -> None:
    motor, rel, _, backend, inst, tmp = mundo
    motor.processar("cam1", 1, quadro((50, 60, SIM_AZUL)), [obs(1, 50, 60)])
    original = motor.executor.contrato
    motor.executor.contrato = lambda pid: (_ for _ in ()).throw(OSError("manifest ilegivel")) if pid == BEBE else original(pid)
    try:
        ev = motor.processar("cam1", 1, quadro((50, 60, SIM_AZUL)), [obs(1, 50, 60, quadro_id=11)])[0]
    finally:
        motor.executor.contrato = original
    assert ev["condicoes"][0]["resultado"] == "indeterminado" and ev["condicoes"][0]["motivo"].startswith("E_INTERNO")


def test_versao_ativada_que_muda_o_alvo_nao_derruba_o_laco(mundo) -> None:
    motor, _, _, _, inst, tmp = mundo
    m = manifesto_bebe("2.0.0")
    m["entrada"]["alvo"] = {"tipo": "quadro"}
    r2 = inst.instalar(empacotar(tmp, "b-quadro.zip", m, b"pesos-q"))
    inst.ativar(r2.id, r2.versao)
    ev = motor.processar("cam1", 1, quadro((50, 60, SIM_AZUL)), [obs(1, 50, 60)])[0]
    assert ev["condicoes"][0]["motivo"] == "E_CONTRATO_INCOMPATIVEL" and ev["veredito"] == "indeterminado"


def test_orcamento_e_relido_entre_vinculos(mundo) -> None:
    motor, rel, _, backend, *_ = mundo
    inferir = ModeloFalso.inferir

    def gasta(self, imagens):
        rel.t += 0.45                                  # cada especialista "gasta" 450 ms de relogio
        return inferir(self, imagens)

    ModeloFalso.inferir = gasta
    try:
        ev = motor.processar("cam1", 1, quadro((50, 60, SIM_AZUL)), [obs(1, 50, 60)])[0]
    finally:
        ModeloFalso.inferir = inferir
    # o 1o gasta 450 ms de 500 e vale; o 2o recebe so ~50 ms restantes (nao 500) e seu resultado, que leva 450, e descartado
    assert ev["condicoes"][0]["resultado"] == "sim"
    assert ev["condicoes"][1]["resultado"] == "indeterminado" and ev["condicoes"][1]["motivo"] in ("atrasado", "E_PRAZO_ESGOTADO")


def test_falha_ao_gravar_evento_nao_derruba_o_laco(mundo) -> None:
    motor, *_ = mundo

    def quebra(_):
        raise OSError("disco cheio")

    motor._destino = quebra
    assert len(motor.processar("cam1", 1, quadro((50, 60, SIM_AZUL)), [obs(1, 50, 60), obs(2, 50, 60)])) == 2
    assert motor.contadores["falha_ao_gravar_evento"] == 2


def test_cache_nao_cresce_sem_limite(mundo) -> None:
    motor, rel, *_ = mundo
    for i in range(1, 30):
        rel.t = 100.0 + i * 100.0                       # cada quadro 100 s depois: o anterior ja venceu ha mais de 60 s
        motor.processar("cam1", 1, quadro((50, 60, SIM_AZUL)), [obs(i, 50, 60, quadro_id=i, t=rel.t)])
    assert len(motor._cache["cam1"]) <= 3 * 2 + 3       # so os atributos do ultimo quadro (+ folga)


def test_bbox_em_texto_e_recusada() -> None:
    assert extrair_recorte(quadro(), "1234") is None and ajustar_bbox(quadro(), "1234") is None


# --- ativacao posterior incompativel com a receita (revisao, achado 1) -------------------------------------------------

def _ativar_bebe_v2(inst, tmp, mexer) -> None:
    m = manifesto_bebe("2.0.0")
    mexer(m)
    r2 = inst.instalar(empacotar(tmp, "b-v2.zip", m, b"pesos-bebe-v2"))
    inst.ativar(r2.id, r2.versao)


def test_troca_de_tri_para_numero_com_o_mesmo_nome_vira_indeterminado_e_nao_nao(mundo) -> None:
    motor, _, _, _, inst, tmp = mundo
    assert veredito(motor.processar("cam1", 1, quadro((50, 60, SIM_AZUL)), [obs(1, 50, 60)]), 1)["veredito"] == "sim"

    def mexer(m):
        m["atributos"] = [{"nome": "bebe_no_colo", "tipo": "numero", "validade_ms": 1500, "faixa": [0, 3],
                           "de": {"modelo": "principal", "tipo": "deteccao", "classes": ["bebe_colo"], "agregacao": "contagem"}}]
        m["parametros"] = []
    _ativar_bebe_v2(inst, tmp, mexer)
    ev = motor.processar("cam1", 1, quadro((50, 60, SIM_AZUL)), [obs(1, 50, 60, quadro_id=11)])[0]
    c = ev["condicoes"][0]
    assert c["resultado"] == "indeterminado" and c["motivo"] == "E_CONTRATO_INCOMPATIVEL" and ev["veredito"] == "indeterminado"
    assert BEBE in ev["incompatibilidades"] and "numerico" in ev["incompatibilidades"][BEBE][0]
    assert (1, BEBE, "bebe_no_colo") not in motor._cache["cam1"]
    inst.reverter(BEBE)                                                                    # volta a ser compativel sem reconfigurar
    ev = motor.processar("cam1", 1, quadro((50, 60, SIM_AZUL)), [obs(1, 50, 60, quadro_id=12)])[0]
    assert ev["veredito"] == "sim" and not ev["incompatibilidades"]


@pytest.mark.parametrize("mexer, trecho", [
    (lambda m: m["parametros"][0].update(max=0.95), None),                                 # continua compativel
    (lambda m: m["atributos"].pop(0), "inexistente"),
    (lambda m: m["entrada"].update(alvo={"tipo": "quadro"}), "quadro"),
])
def test_demais_incompatibilidades_de_ativacao(mundo, mexer, trecho) -> None:
    motor, _, _, _, inst, tmp = mundo
    _ativar_bebe_v2(inst, tmp, mexer)
    ev = motor.processar("cam1", 1, quadro((50, 60, SIM_AZUL)), [obs(1, 50, 60)])[0]
    if trecho is None:
        assert not ev["incompatibilidades"]
    else:
        assert trecho in ev["incompatibilidades"][BEBE][0] and ev["condicoes"][0]["motivo"] == "E_CONTRATO_INCOMPATIVEL"


def test_remocao_de_categoria_usada_pela_receita(mundo) -> None:
    motor, _, _, _, inst, tmp = mundo
    m = manifesto_cor("2.0.0")
    m["atributos"][0]["categorias"] = ["verde", "outro"]                                  # "azul" some
    r2 = inst.instalar(empacotar(tmp, "c-v2.zip", m, b"pesos-cor-2", "modelos/cor.pt"))
    inst.ativar(r2.id, r2.versao)
    ev = motor.processar("cam1", 1, quadro((50, 60, SIM_AZUL)), [obs(1, 50, 60)])[0]
    assert ev["condicoes"][1]["motivo"] == "E_CONTRATO_INCOMPATIVEL" and "categorias" in ev["incompatibilidades"][COR][0]


def test_politica_de_cache_e_reutilizacao_aparecem_no_evento(mundo) -> None:
    motor, rel, *_ = mundo
    motor.processar("cam1", 1, quadro((50, 60, SIM_AZUL)), [obs(1, 50, 60)])
    rel.t = 100.5
    ev = motor.processar("cam1", 1, quadro(), [obs(1, 700, 10, quadro_id=11, t=rel.t)])[0]
    c = ev["condicoes"][0]
    assert ev["politica_cache"] == "manter_ate_expirar" and c["reutilizado"] is True and c["falha_recente"] == "recorte_vazio"
    assert c["idade_ms"] == pytest.approx(500, abs=1) and c["quadro_do_atributo"] == 10          # idade conta desde a captura ORIGINAL
