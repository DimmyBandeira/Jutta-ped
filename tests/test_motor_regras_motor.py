"""Integracao MotorSombra + especialistas reais de conformidade (backend FALSO) + regras do contrato 2.

Relogio controlado (nada de sleeps): `rel.t` e o `recebido_em` de cada cena. Pacotes de teste, nenhum de dominio real:
BEBE (classe mestre 'person'), VEIC (classe mestre 'veiculo'), CMPA/CMPB (numericos, para `maior_que`)."""
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
from test_motor_sombra import ALTURA, LARGURA, NAO_AZUL, SIM_AZUL, Relogio, quadro
from test_motor_sombra import receita as receita_v1

from src.jutta_ped.especialistas.instalador import Instalador
from src.jutta_ped.motor.cena import Deteccao, criar_cena, observacoes_de_cena
from src.jutta_ped.motor.motor import MotorSombra

BEBE, VEIC, CMPA, CMPB, COR = "br.teste.bebe", "br.teste.veiculo", "br.teste.cmpa", "br.teste.cmpb", "br.teste.uniforme"


def manifesto_veiculo(versao: str = "1.0.0") -> dict:
    m = manifesto_cor(versao)
    m["pacote"]["id"] = VEIC
    m["entrada"]["alvo"]["classe_mestre"] = "veiculo"
    return m


def manifesto_numerico(pid: str, nome: str, classe_prob: str, versao: str = "1.0.0", tipo: str = "numero") -> dict:
    m = manifesto_cor(versao)
    m["pacote"]["id"] = pid
    de = {"modelo": "cor", "tipo": "classificacao", "classe": classe_prob, "agregacao": "probabilidade"}
    if tipo == "numero":
        m["atributos"] = [{"nome": nome, "tipo": "numero", "validade_ms": 3000, "faixa": [0, 1], "de": de}]
    else:
        m["atributos"] = [{"nome": nome, "tipo": "tri", "validade_ms": 3000, "de": {**de, "limiar_presente": 0.6, "limiar_ausente": 0.2}}]
    return m


def receita_cena(versao: str = "1", duracao: float = 2.0, **evento) -> dict:
    ev = {"id": "junto", "sujeito": "alvo", "quando_todas": ["perto_carro"], "duracao_s": duracao, "lacuna_max_s": 1.5, "perda_max_s": 5, "encerrar_negativo_s": 2}
    ev.update(evento)
    return {"id": "r.cena", "versao": versao, "contrato": 2,
            "vinculos": [{"especialista": BEBE, "orcamento_ms": 500}, {"especialista": VEIC, "orcamento_ms": 500}],
            "papeis": {"alvo": {"classe": "person", "combinacao": "todas", "condicoes": [{"especialista": BEBE, "atributo": "bebe_no_colo", "igual": "sim", "validade_ms": 1500}]},
                       "carro": {"classe": "veiculo", "combinacao": "todas", "condicoes": [{"especialista": VEIC, "atributo": "usa_cracha", "igual": "sim"}]}},
            "relacoes": {"perto_carro": {"tipo": "existe_proximo", "sujeito": "alvo", "outro": "carro", "distancia": {"unidade": "pixels", "maximo": 100}}},
            "eventos": [ev]}


def receita_cmp(op: dict, versao: str = "1") -> dict:
    return {"id": "r.cmp", "versao": versao, "contrato": 2,
            "vinculos": [{"especialista": CMPA}, {"especialista": CMPB}],
            "papeis": {"alvo": {"classe": "person", "combinacao": "todas", "condicoes": [{"especialista": CMPA, "atributo": "prob_azul", **op}]}},
            "eventos": [{"id": "e", "sujeito": "alvo", "quando_todas": ["alvo"], "duracao_s": 1}]}


class Mundo:
    def __init__(self, tmp: Path, nome: str = "raiz") -> None:
        self.tmp = tmp / nome
        self.tmp.mkdir()
        self.inst = Instalador(self.tmp / "inst")
        instalar_ativar(self.inst, self.tmp, manifesto_bebe(), b"pesos-bebe")
        instalar_ativar(self.inst, self.tmp, manifesto_veiculo(), b"pesos-veic", "modelos/cor.pt")
        instalar_ativar(self.inst, self.tmp, manifesto_numerico(CMPA, "prob_azul", "azul"), b"pesos-cmpa", "modelos/cor.pt")
        instalar_ativar(self.inst, self.tmp, manifesto_numerico(CMPB, "prob_verde", "verde"), b"pesos-cmpb", "modelos/cor.pt")
        instalar_ativar(self.inst, self.tmp, manifesto_cor(), b"pesos-cor", "modelos/cor.pt")
        self.backend = BackendFalso(tipo_por_arquivo={"modelos/cor.pt": "classificacao"})
        self.rel, self.eventos = Relogio(), []
        self.motor = MotorSombra(fabrica(self.inst, self.backend), habilitado=True, relogio=self.rel, destino=self.eventos.append)

    def configurar(self, receitas: list[dict], cams: dict[str, tuple[str, str]]) -> None:
        self.motor.configurar(receitas, {c: {"receita": r, "versao": v} for c, (r, v) in cams.items()})


@pytest.fixture
def mundo(tmp_path: Path) -> Mundo:
    m = Mundo(tmp_path)
    m.configurar([receita_cena(), receita_cena("2", duracao=3.0), receita_cmp({"minimo": 0.5}, "min"), receita_cmp({"maior_que": {"especialista": CMPB, "atributo": "prob_verde"}}, "cmp"),
                  receita_v1()],
                 {"cam1": ("r.cena", "1"), "cam2": ("r.cena", "1")})
    return m


def desenhar(q: np.ndarray, x: int, y: int, bgr: tuple[int, int, int], b2: int | None = None) -> None:
    q[y, x] = bgr
    if b2 is not None:
        q[y, x + 1, 0] = b2


def mk(t: float, pessoas=(), veiculos=(), *, cam: str = "cam1", ger: int = 1, sem_quadro: bool = False):
    """pessoas = (trilha, x, y, bgr); veiculos = (trilha, x, y, b2) (b2 >= 153 -> usa_cracha 'sim'). Caixas 80x200."""
    q = quadro()
    dets = []
    for tr, x, y, bgr in pessoas:
        desenhar(q, x, y, bgr)
        dets.append(Deteccao(tr, "person", (x, y, x + 80, y + 200)))
    for tr, x, y, b2 in veiculos:
        desenhar(q, x, y, (0, 0, 0), b2)
        dets.append(Deteccao(tr, "veiculo", (x, y, x + 80, y + 200)))
    return criar_cena(cam, ger, int(t * 1000), t, LARGURA, ALTURA, dets, None if sem_quadro else q)


def rodar(w: Mundo, t: float, pessoas=(), veiculos=(), **kw) -> dict:
    w.rel.t = t
    saida = w.motor.processar_cena(mk(t, pessoas, veiculos, **kw))
    assert len(saida) == 1
    return saida[0]


PAR = dict(pessoas=[(1, 50, 60, SIM_AZUL)], veiculos=[(7, 150, 60, 250)])        # pessoa 1 a 20 px do veiculo 7 (ambos 'sim')


def linha(ev: dict, trilha: int = 1, eid: str = "junto") -> dict:
    return next(x for x in ev["eventos"] if x["id"] == eid and x["trilha_id"] == trilha)


def sujeito(ev: dict, trilha: int, classe: str = "person") -> dict:
    return next(s for s in ev["sujeitos"] if s["trilha_id"] == trilha and s["classe"] == classe)


def ativar(w: Mundo, cam: str = "cam1", inicio: float = 100.0) -> dict:
    ev = {}
    for t in (inicio, inicio + 0.5, inicio + 1.0, inicio + 1.5, inicio + 2.0):
        ev = rodar(w, t, cam=cam, **PAR)
    assert linha(ev)["estado"] == "ativo"
    return ev


# --- contrato 1: identico ao v1 -------------------------------------------------------------------------------------------------------

def test_contrato1_processar_cena_produz_os_mesmos_eventos_de_processar(tmp_path: Path) -> None:
    a, b = Mundo(tmp_path, "a"), Mundo(tmp_path, "b")
    for w in (a, b):
        w.configurar([receita_v1()], {"cam1": ("r.teste", "1")})
    seq = [(100.0, [(1, 50, 60, SIM_AZUL), (2, 300, 100, NAO_AZUL)]), (100.5, [(1, 50, 60, SIM_AZUL)]), (101.0, []), (102.0, [(3, 500, 20, SIM_AZUL)])]
    for t, pessoas in seq:
        a.rel.t = b.rel.t = t
        cena = mk(t, pessoas)
        direto = a.motor.processar("cam1", 1, cena.quadro, observacoes_de_cena(cena))
        via_cena = b.motor.processar_cena(cena)
        assert via_cena == direto
        assert [e["trilha_id"] for e in via_cena] == [p[0] for p in pessoas]          # cena vazia: nenhum evento (como no v1)
    assert b.eventos == a.eventos and all("tipo" not in e for e in b.eventos)


def test_contrato1_e_contrato2_coexistem_no_mesmo_motor(mundo: Mundo) -> None:
    mundo.configurar([receita_cena(), receita_v1()], {"cam1": ("r.cena", "1"), "cam3": ("r.teste", "1")})
    ev1 = rodar(mundo, 100.0, **PAR)
    assert ev1["tipo"] == "cena"
    mundo.rel.t = 100.0
    v1 = mundo.motor.processar_cena(mk(100.0, [(1, 50, 60, SIM_AZUL)], cam="cam3"))
    assert len(v1) == 1 and v1[0]["veredito"] == "sim" and "tipo" not in v1[0]


def test_processar_v1_numa_camera_de_contrato2_nao_faz_nada(mundo: Mundo) -> None:
    c = mk(100.0, **PAR)
    mundo.rel.t = 100.0
    assert mundo.motor.processar("cam1", 1, c.quadro, observacoes_de_cena(c)) == []
    assert mundo.motor.contadores["receita_contrato2_exige_cena"] == 1


def test_desabilitado_e_camera_sem_receita_e_entrada_invalida_nao_derrubam(mundo: Mundo) -> None:
    mundo.rel.t = 100.0
    assert mundo.motor.processar_cena(mk(100.0, cam="sem")) == []
    assert mundo.motor.processar_cena("lixo") == [] and mundo.motor.contadores["cena_rejeitada"] == 1      # type: ignore[arg-type]
    mundo.rel.t = 99.0
    assert mundo.motor.processar_cena(mk(100.0)) == []                      # do futuro (alem do relogio local)
    mundo.motor.habilitado = False
    cargas = list(mundo.backend.cargas)
    assert mundo.motor.processar_cena(mk(100.0, **PAR)) == [] and mundo.backend.lotes == [] and mundo.backend.cargas == cargas


# --- contrato 2 ponta a ponta --------------------------------------------------------------------------------------------------------

def test_evento_de_cena_formato_papeis_relacoes_e_especialistas(mundo: Mundo) -> None:
    ev = rodar(mundo, 100.0, **PAR)
    assert mundo.eventos == [ev]
    assert ev["tipo"] == "cena" and ev["modo"] == "sombra" and ev["alerta_real"] is False and ev["versao_evento"] == 1
    assert (ev["camera_ref"], ev["geracao"], ev["quadro_id"], ev["capturado_em"], ev["avaliado_em"]) == ("cam1", 1, 100000, 100.0, 100.0)
    assert ev["receita"]["id"] == "r.cena" and ev["receita"]["versao"] == "1" and ev["receita"]["config_hash"].startswith("sha256:")
    assert {x["id"] for x in ev["especialistas"]} == {BEBE, VEIC} and ev["politica_cache"] == "manter_ate_expirar"
    assert ev["incompatibilidades"] == {} and ev["erros"] == {} and ev["descartes"] == {} and ev["encerramentos_por_descarte"] == []
    p, v = sujeito(ev, 1), sujeito(ev, 7, "veiculo")
    assert p["papeis"] == {"alvo": "sim"} and v["papeis"] == {"carro": "sim"}
    assert p["relacoes"] == {"perto_carro": {"estado": "sim", "proximos": [7], "proximos_indeterminados": []}} and v["relacoes"] == {}
    assert p["detalhes"]["alvo"][0]["resultado"] == "sim" and p["bbox_xyxy"] == [50, 60, 130, 260]
    assert linha(ev) == {"id": "junto", "trilha_id": 1, "estado": "candidato", "positivo_s": 0.0, "idade_ultima_evidencia_s": 0.0,
                         "sustentacao_perdida": False, "transicao": None, "motivo": None}
    json.dumps(ev)


def test_persistencia_por_tempo_de_relogio_inicia_so_apos_a_duracao(mundo: Mundo) -> None:
    estados = [(linha(rodar(mundo, t, **PAR))["estado"]) for t in (100.0, 100.5, 101.0, 101.5)]
    assert estados == ["candidato"] * 4
    ev = rodar(mundo, 102.0, **PAR)
    assert linha(ev)["transicao"] == "iniciado" and linha(ev)["estado"] == "ativo" and linha(ev)["positivo_s"] == 2.0
    assert mundo.motor.contadores["evento_iniciado"] == 1


def test_dois_pacotes_com_classes_mestre_diferentes_so_veem_a_propria_classe(mundo: Mundo) -> None:
    rodar(mundo, 100.0, **PAR)
    chaves = {k[:2] for k in mundo.motor._cache["cam1"]}
    assert chaves == {(1, BEBE), (7, VEIC)}      # o veiculo nao foi para o pacote da pessoa e vice-versa
    ev = rodar(mundo, 100.5, pessoas=[(1, 50, 60, SIM_AZUL)], veiculos=[(7, 150, 60, 10)])
    assert sujeito(ev, 7, "veiculo")["papeis"] == {"carro": "nao"} and sujeito(ev, 1)["relacoes"]["perto_carro"]["estado"] == "nao"


def test_veiculo_longe_ou_nao_satisfeito_e_evidencia_negativa_e_resolve(mundo: Mundo) -> None:
    ativar(mundo)
    ev = {}
    for t in (102.5, 103.0, 103.5, 104.0, 104.5):
        ev = rodar(mundo, t, pessoas=[(1, 50, 60, SIM_AZUL)], veiculos=[(7, 400, 60, 250)])      # 'carro' sim, mas longe
        assert sujeito(ev, 1)["relacoes"]["perto_carro"]["estado"] == "nao"
    assert linha(ev)["transicao"] == "encerrado" and linha(ev)["motivo"] == "resolvido_evidencia_negativa"


def test_cameras_tem_estado_isolado_mesmo_com_as_mesmas_trilhas(mundo: Mundo) -> None:
    for t in (100.0, 100.5, 101.0, 101.5, 102.0):
        ev1 = rodar(mundo, t, **PAR)
    ev2 = rodar(mundo, 102.0, cam="cam2", **PAR)
    assert linha(ev1)["estado"] == "ativo" and linha(ev2)["estado"] == "candidato" and linha(ev2)["positivo_s"] == 0.0 and ev2["camera_ref"] == "cam2"
    ev1 = rodar(mundo, 102.5, **PAR)
    ev2 = rodar(mundo, 102.5, cam="cam2", **PAR)
    assert linha(ev1)["positivo_s"] == 2.5 and linha(ev2)["positivo_s"] == 0.5


def test_varias_trilhas_independentes_na_mesma_cena(mundo: Mundo) -> None:
    pessoas = [(1, 50, 60, SIM_AZUL), (2, 450, 300, SIM_AZUL), (3, 500, 20, NAO_AZUL)]
    ev = rodar(mundo, 100.0, pessoas=pessoas, veiculos=[(7, 150, 60, 250)])
    assert [sujeito(ev, k)["papeis"]["alvo"] for k in (1, 2, 3)] == ["sim", "sim", "nao"]
    assert [sujeito(ev, k)["relacoes"]["perto_carro"]["estado"] for k in (1, 2, 3)] == ["sim", "nao", "nao_se_aplica"]
    for t in (100.5, 101.0, 101.5, 102.0):
        ev = rodar(mundo, t, pessoas=pessoas, veiculos=[(7, 150, 60, 250)])
    assert [linha(ev, k)["estado"] for k in (1, 2, 3)] == ["ativo", "inativo", "inativo"]


def test_cena_vazia_avanca_o_estado_e_emite_o_evento(mundo: Mundo) -> None:
    ativar(mundo)
    ev = rodar(mundo, 103.0)                                                 # sem deteccoes
    assert ev["tipo"] == "cena" and ev["sujeitos"] == [] and linha(ev)["estado"] == "ativo" and linha(ev)["sustentacao_perdida"] is False
    assert mundo.eventos[-1] is ev
    ev = rodar(mundo, 105.0)                                                 # ultimo sim ha 3 s > lacuna 1.5
    assert linha(ev)["estado"] == "ativo" and linha(ev)["sustentacao_perdida"] is True
    ev = rodar(mundo, 107.5)                                                 # ausente por 5.5 s > perda_max_s (5)
    assert linha(ev)["transicao"] == "encerrado" and linha(ev)["motivo"] == "sujeito_ausente_prolongado"
    assert rodar(mundo, 108.0)["eventos"] == []


def test_expiracao_do_atributo_vira_indeterminado_e_depois_perda_de_observacao(mundo: Mundo) -> None:
    ativar(mundo)                                                            # ultimo sim em 102.0; bebe_no_colo vale 1500 ms
    ev = rodar(mundo, 103.0, sem_quadro=True, pessoas=[(1, 50, 60, SIM_AZUL)], veiculos=[(7, 150, 60, 250)])
    assert sujeito(ev, 1)["papeis"]["alvo"] == "sim" and sujeito(ev, 1)["detalhes"]["alvo"][0]["reutilizado"] is True       # cache ainda vale
    ev = rodar(mundo, 104.0, sem_quadro=True, pessoas=[(1, 50, 60, SIM_AZUL)], veiculos=[(7, 150, 60, 250)])               # 2000 ms: expirou
    d = sujeito(ev, 1)["detalhes"]["alvo"][0]
    assert sujeito(ev, 1)["papeis"]["alvo"] == "indeterminado" and d["motivo"] == "expirado"
    assert linha(ev)["estado"] == "ativo" and linha(ev)["sustentacao_perdida"] is False and linha(ev)["transicao"] is None       # ultimo sim: 103.0
    ev = rodar(mundo, 105.0, sem_quadro=True, pessoas=[(1, 50, 60, SIM_AZUL)], veiculos=[(7, 150, 60, 250)])
    assert linha(ev)["estado"] == "ativo" and linha(ev)["sustentacao_perdida"] is True
    ev = rodar(mundo, 108.5, sem_quadro=True, pessoas=[(1, 50, 60, SIM_AZUL)], veiculos=[(7, 150, 60, 250)])              # 5.5 s sem evidencia
    assert linha(ev)["transicao"] == "encerrado" and linha(ev)["motivo"] == "perda_de_observacao"


def test_indeterminado_do_especialista_nunca_satisfaz_a_persistencia(mundo: Mundo) -> None:
    indef = (77, 1, 0)                                                       # conf 0.30: entre os limiares
    for i in range(12):
        ev = rodar(mundo, 100.0 + i * 0.5, pessoas=[(1, 50, 60, indef)], veiculos=[(7, 150, 60, 250)])
        assert sujeito(ev, 1)["papeis"]["alvo"] == "indeterminado" and sujeito(ev, 1)["detalhes"]["alvo"][0]["motivo"] == "baixa_confianca"
        assert linha(ev)["estado"] == "inativo" and linha(ev)["transicao"] is None
    ativar(mundo, inicio=110.0)
    for i in range(1, 12):
        ev = rodar(mundo, 112.0 + i * 0.5, pessoas=[(1, 50, 60, indef)], veiculos=[(7, 150, 60, 250)])
        assert linha(ev)["transicao"] in (None, "encerrado")
    assert mundo.motor.contadores["evento_encerrado_perda_de_observacao"] == 1   # nunca 'resolvido': indeterminado nao e negativo


def test_resultado_que_volta_depois_do_prazo_continua_descartado(mundo: Mundo) -> None:
    inferir = ModeloFalso.inferir

    def lento(self, imagens):
        mundo.rel.t += 0.8
        return inferir(self, imagens)

    ModeloFalso.inferir = lento
    try:
        ev = rodar(mundo, 100.0, **PAR)
    finally:
        ModeloFalso.inferir = inferir
    assert sujeito(ev, 1)["papeis"]["alvo"] == "indeterminado" and sujeito(ev, 7, "veiculo")["papeis"]["carro"] == "indeterminado"
    assert not mundo.motor._cache["cam1"] and linha(ev)["estado"] == "inativo"
    assert ev["avaliado_em"] > ev["capturado_em"] + 0.5


def test_perda_do_especialista_ativo_vira_indeterminado(mundo: Mundo) -> None:
    ativar(mundo)
    original = mundo.motor.executor.contrato
    mundo.motor.executor.contrato = lambda pid: (_ for _ in ()).throw(OSError("manifest ilegivel")) if pid == BEBE else original(pid)
    try:
        ev = rodar(mundo, 102.5, **PAR)
    finally:
        mundo.motor.executor.contrato = original
    d = sujeito(ev, 1)["detalhes"]["alvo"][0]
    assert d["resultado"] == "indeterminado" and d["motivo"].startswith("E_INTERNO") and linha(ev)["estado"] == "ativo"
    assert ev["erros"][BEBE].startswith("E_INTERNO")


def test_troca_de_versao_do_pacote_incompativel_e_indeterminado_nunca_nao(mundo: Mundo) -> None:
    ativar(mundo)
    m = manifesto_bebe("2.0.0")
    m["atributos"] = [{"nome": "bebe_no_colo", "tipo": "numero", "validade_ms": 1500, "faixa": [0, 3],
                       "de": {"modelo": "principal", "tipo": "deteccao", "classes": ["bebe_colo"], "agregacao": "contagem"}}]
    m["parametros"] = []
    r2 = mundo.inst.instalar(empacotar(mundo.tmp, "bebe-v2.zip", m, b"pesos-bebe-v2"))
    mundo.inst.ativar(r2.id, r2.versao)
    for t in (102.5, 103.0, 103.5, 104.0, 104.5):
        ev = rodar(mundo, t, **PAR)
        assert sujeito(ev, 1)["papeis"]["alvo"] == "indeterminado" and sujeito(ev, 1)["detalhes"]["alvo"][0]["motivo"] == "E_CONTRATO_INCOMPATIVEL"
        assert linha(ev)["estado"] == "ativo" and linha(ev)["transicao"] is None        # nao virou evidencia negativa
    assert ev["erros"][BEBE] == "E_CONTRATO_INCOMPATIVEL" and BEBE in ev["incompatibilidades"]
    mundo.inst.reverter(BEBE)
    ev = rodar(mundo, 105.0, **PAR)
    assert sujeito(ev, 1)["papeis"]["alvo"] == "sim" and not ev["incompatibilidades"]


def test_troca_de_receita_descarta_o_estado_reconfigurado(mundo: Mundo) -> None:
    ativar(mundo)
    mundo.configurar([receita_cena(), receita_cena("2", duracao=3.0)], {"cam1": ("r.cena", "2"), "cam2": ("r.cena", "1")})
    ev = rodar(mundo, 102.5, **PAR)
    assert [(x["id"], x["trilha_id"], x["transicao"], x["motivo"]) for x in ev["encerramentos_por_descarte"]] == [("junto", 1, "encerrado", "reconfigurado")]
    assert linha(ev)["estado"] == "candidato" and linha(ev)["positivo_s"] == 0.0 and ev["receita"]["versao"] == "2"
    assert rodar(mundo, 103.0, **PAR)["encerramentos_por_descarte"] == []          # so e relatado uma vez
    assert mundo.motor.contadores["evento_encerrado_reconfigurado"] == 1


def test_reconfigurar_com_a_mesma_receita_preserva_o_estado_e_a_camera_removida_perde_o_estado(mundo: Mundo) -> None:
    ativar(mundo)
    mundo.configurar([receita_cena()], {"cam1": ("r.cena", "1"), "cam2": ("r.cena", "1")})
    assert linha(rodar(mundo, 102.5, **PAR))["estado"] == "ativo"
    mundo.configurar([receita_cena()], {"cam2": ("r.cena", "1")})
    assert "cam1" not in mundo.motor._regras


def test_receita_contrato2_invalida_e_recusada_na_configuracao(mundo: Mundo) -> None:
    r = receita_cena()
    r["papeis"]["carro"]["classe"] = "person"                                # VEIC avalia 'veiculo'
    with pytest.raises(ValueError, match="classe mestre"):
        mundo.configurar([r], {"cam1": ("r.cena", "1")})
    r = receita_cena()
    r["eventos"][0]["duracao"] = 4
    with pytest.raises(ValueError, match="desconhecidas"):
        mundo.configurar([r], {"cam1": ("r.cena", "1")})
    assert set(mundo.motor._camera) == {"cam1", "cam2"}                     # configuracao anterior intacta


def test_reinicio_de_sessao_geracao_maior_descarta_e_menor_e_recusada(mundo: Mundo) -> None:
    ativar(mundo)
    ev = rodar(mundo, 103.0, ger=2, **PAR)
    assert ev["geracao"] == 2 and [(x["motivo"], x["trilha_id"]) for x in ev["encerramentos_por_descarte"]] == [("geracao_encerrada", 1)]
    assert linha(ev)["estado"] == "candidato" and linha(ev)["positivo_s"] == 0.0
    mundo.rel.t = 104.0
    n = len(mundo.eventos)
    assert mundo.motor.processar_cena(mk(104.0, ger=1, **PAR)) == [] and len(mundo.eventos) == n
    assert mundo.motor.contadores["descartado_sessao_anterior"] == 1


def test_cena_fora_de_ordem_e_rejeitada_e_contada(mundo: Mundo) -> None:
    rodar(mundo, 100.0, **PAR)
    rodar(mundo, 101.0, **PAR)
    mundo.rel.t = 101.0
    assert mundo.motor.processar_cena(mk(101.0, **PAR)) == [] and mundo.motor.processar_cena(mk(100.5, **PAR)) == []
    assert mundo.motor.contadores["cena_fora_de_ordem"] == 2
    assert linha(rodar(mundo, 101.5, **PAR))["positivo_s"] == 1.5           # estado intacto


def test_encerrar_camera_descarta_com_motivo_de_sessao(mundo: Mundo) -> None:
    ativar(mundo)
    linhas = mundo.motor.encerrar_camera("cam1")
    assert [(x["transicao"], x["motivo"]) for x in linhas] == [("encerrado", "sessao_encerrada")]
    ev = rodar(mundo, 103.0, **PAR)
    assert [x["motivo"] for x in ev["encerramentos_por_descarte"]] == ["sessao_encerrada"] and linha(ev)["estado"] == "candidato"


def test_falha_do_destino_nao_derruba_o_laco(mundo: Mundo) -> None:
    def quebra(_):
        raise OSError("disco cheio")

    mundo.motor._destino = quebra
    mundo.rel.t = 100.0
    assert len(mundo.motor.processar_cena(mk(100.0, **PAR))) == 1 and mundo.motor.contadores["falha_ao_gravar_evento"] == 1


def test_o_motor_de_regras_nao_conhece_nenhum_dominio() -> None:
    import re
    base = Path(__file__).resolve().parents[1] / "src" / "jutta_ped" / "motor"
    texto = "\n".join(p.read_text(encoding="utf-8").lower().replace("pediatria_legado", "") for p in base.glob("*.py"))
    assert not re.search(r"pediatr|crian|child|adult|bebe|infant", texto)


# --- operadores numericos nas condicoes de papel ------------------------------------------------------------------------------------

def num(w: Mundo, versao: str, t: float, bgr, **kw) -> dict:
    w.configurar([receita_cmp({"minimo": 0.5}, "min"), receita_cmp({"maior_que": {"especialista": CMPB, "atributo": "prob_verde"}}, "cmp")], {"cam1": ("r.cmp", versao)})
    return rodar(w, t, pessoas=[(1, 50, 60, bgr)], **kw)


def papel_alvo(ev: dict) -> tuple[str, dict]:
    s = sujeito(ev, 1)
    return s["papeis"]["alvo"], s["detalhes"]["alvo"][0]


def test_minimo_em_atributo_numero(mundo: Mundo) -> None:
    assert papel_alvo(num(mundo, "min", 100.0, (200, 0, 0)))[0] == "sim"        # prob_azul 0.78 >= 0.5
    assert papel_alvo(num(mundo, "min", 101.0, (100, 0, 0)))[0] == "nao"        # 0.39 < 0.5


def test_maior_que_sim_nao_e_empate_e_nao(mundo: Mundo) -> None:
    estado, det = papel_alvo(num(mundo, "cmp", 100.0, (200, 100, 0)))
    assert estado == "sim" and det["maior_que"] == {"especialista": CMPB, "atributo": "prob_verde"} and det["motivo"] is None
    assert papel_alvo(num(mundo, "cmp", 101.0, (100, 100, 0)))[0] == "nao"      # empate = nao
    assert papel_alvo(num(mundo, "cmp", 102.0, (50, 100, 0)))[0] == "nao"
    assert papel_alvo(num(mundo, "cmp", 103.0, (0, 0, 0)))[0] == "nao"          # 0 > 0 e falso


def test_maior_que_indeterminado_se_qualquer_lado_ausente_ou_expirado(mundo: Mundo) -> None:
    estado, det = papel_alvo(num(mundo, "cmp", 100.0, (200, 100, 0), sem_quadro=True))
    assert estado == "indeterminado" and det["motivo"] == "recorte_vazio"
    assert papel_alvo(rodar(mundo, 101.0, pessoas=[(1, 50, 60, (200, 100, 0))]))[0] == "sim"
    estado, det = papel_alvo(rodar(mundo, 104.5, sem_quadro=True, pessoas=[(1, 50, 60, (200, 100, 0))]))          # 3.5 s > 3000 ms (os dois)
    assert estado == "indeterminado" and det["motivo"] == "expirado"
    # so o OUTRO lado indisponivel (atributo comparado de outro especialista)
    original = mundo.motor.executor.contrato
    mundo.motor.executor.contrato = lambda pid: (_ for _ in ()).throw(OSError("x")) if pid == CMPB else original(pid)
    try:
        estado, det = papel_alvo(rodar(mundo, 110.0, pessoas=[(1, 50, 60, (200, 100, 0))]))
    finally:
        mundo.motor.executor.contrato = original
    assert estado == "indeterminado" and det["motivo"].startswith("comparado:E_INTERNO")
    assert papel_alvo(rodar(mundo, 111.0, pessoas=[(1, 50, 60, (200, 100, 0))]))[0] == "sim"          # recuperou


def test_maior_que_incompativel_apos_troca_de_versao_do_atributo_comparado(mundo: Mundo) -> None:
    assert papel_alvo(num(mundo, "cmp", 100.0, (200, 100, 0)))[0] == "sim"
    m = manifesto_numerico(CMPB, "prob_verde", "verde", "2.0.0", tipo="tri")          # o atributo comparado deixa de ser numero
    r2 = mundo.inst.instalar(empacotar(mundo.tmp, "cmpb-v2.zip", m, b"pesos-cmpb-2", "modelos/cor.pt"))
    mundo.inst.ativar(r2.id, r2.versao)
    ev = rodar(mundo, 101.0, pessoas=[(1, 50, 60, (200, 100, 0))])
    estado, det = papel_alvo(ev)
    assert estado == "indeterminado" and ev["erros"][CMPB] == "E_CONTRATO_INCOMPATIVEL" and CMPB in ev["incompatibilidades"]
    assert "exige atributo numero" in ev["incompatibilidades"][CMPB][0]
    assert det["motivo"] == "comparado:E_CONTRATO_INCOMPATIVEL"
    mundo.inst.reverter(CMPB)
    assert papel_alvo(rodar(mundo, 102.0, pessoas=[(1, 50, 60, (200, 100, 0))]))[0] == "sim"
