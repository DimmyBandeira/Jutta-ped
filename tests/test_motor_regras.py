"""Regras espaciais/temporais PURAS (contrato 2): geometria, relacoes 3-valoradas, persistencia com relogio controlado
(= `recebido_em` das cenas; sem sleeps) e validacao da receita. Nada de dominio: classes/papeis sao rotulos arbitrarios."""
from __future__ import annotations

import copy
import math

import pytest

from src.jutta_ped.motor.cena import criar_cena, Deteccao
from src.jutta_ped.motor.receita import validar_receita, validar_vinculo
from src.jutta_ped.motor.regras import (
    AvaliadorRegras,
    CaixaInvalida,
    distancia_em,
    intervalo_pixels,
    relacao_3v,
    validar_contrato2,
)

LARG, ALT = 640, 480


# --- geometria -------------------------------------------------------------------------------------------------------

def test_intervalo_entre_caixas_sobrepostas_adjacentes_e_longe() -> None:
    a = (0, 0, 100, 100)
    assert intervalo_pixels(a, (50, 50, 150, 150)) == 0          # sobrepostas
    assert intervalo_pixels(a, (100, 0, 200, 100)) == 0          # encostadas
    assert intervalo_pixels(a, (50, 20, 60, 30)) == 0            # contida
    assert intervalo_pixels(a, (130, 140, 200, 200)) == pytest.approx(50.0)     # dx=30, dy=40
    assert intervalo_pixels(a, (200, 10, 300, 20)) == pytest.approx(100.0)      # so no eixo x
    assert intervalo_pixels((130, 140, 200, 200), a) == pytest.approx(50.0)     # simetrica


def test_unidades_pixels_altura_do_sujeito_e_diagonal() -> None:
    suj, outro = (0, 0, 100, 200), (160, 0, 260, 200)           # intervalo 60 px
    assert distancia_em(suj, outro, "pixels", LARG, ALT) == pytest.approx(60)
    assert distancia_em(suj, outro, "altura_sujeito", LARG, ALT) == pytest.approx(0.3)       # / 200 (altura do SUJEITO)
    assert distancia_em(outro, suj, "altura_sujeito", LARG, ALT) == pytest.approx(0.3)       # mesma altura aqui
    pequeno = (160, 0, 260, 50)
    assert distancia_em(pequeno, suj, "altura_sujeito", LARG, ALT) == pytest.approx(1.2)     # sujeito baixo: 60 / 50
    assert distancia_em(suj, outro, "diagonal_quadro", 300, 400) == pytest.approx(60 / 500)
    with pytest.raises(ValueError):
        distancia_em(suj, outro, "metros", LARG, ALT)
    with pytest.raises(CaixaInvalida):
        distancia_em(suj, outro, "diagonal_quadro", 0, ALT)


def test_metricas_intervalo_centro_e_ponto_de_apoio() -> None:
    a, b = (0, 0, 100, 200), (300, 100, 400, 400)
    assert distancia_em(a, b, "pixels", LARG, ALT) == pytest.approx(200.0)                  # intervalo: dx=200, dy=0
    assert distancia_em(a, b, "pixels", LARG, ALT, "intervalo_bbox") == pytest.approx(200.0)
    assert distancia_em(a, b, "pixels", LARG, ALT, "centro") == pytest.approx(math.hypot(300, 150))     # (50,100) vs (350,250)
    assert distancia_em(a, b, "pixels", LARG, ALT, "ponto_de_apoio") == pytest.approx(math.hypot(300, 200))   # (50,200) vs (350,400)
    # caixas sobrepostas: intervalo 0, mas centro/apoio nao
    c, d = (0, 0, 100, 100), (50, 50, 150, 150)
    assert distancia_em(c, d, "pixels", LARG, ALT) == 0
    assert distancia_em(c, d, "pixels", LARG, ALT, "centro") == pytest.approx(math.hypot(50, 50))
    assert distancia_em(c, d, "pixels", LARG, ALT, "ponto_de_apoio") == pytest.approx(math.hypot(50, 50))
    # as unidades valem para todas as metricas (altura do sujeito = 200)
    assert distancia_em(a, b, "altura_sujeito", LARG, ALT, "centro") == pytest.approx(math.hypot(300, 150) / 200)
    assert distancia_em(a, b, "diagonal_quadro", 300, 400, "ponto_de_apoio") == pytest.approx(math.hypot(300, 200) / 500)
    with pytest.raises(ValueError):
        distancia_em(a, b, "pixels", LARG, ALT, "fisica")


@pytest.mark.parametrize("ruim", [(10, 10, 10, 50), (50, 10, 20, 50), (0, 0, float("nan"), 5), (0, 0, float("inf"), 5), (1, 2, 3), "abcd", None, (0, 0, True, 5)])
def test_caixas_degeneradas_sao_rejeitadas(ruim) -> None:
    with pytest.raises(CaixaInvalida):
        intervalo_pixels(ruim, (0, 0, 10, 10))
    with pytest.raises(CaixaInvalida):
        intervalo_pixels((0, 0, 10, 10), ruim)


# --- relacoes 3-valoradas (funcao pura) ---------------------------------------------------------------------------------

@pytest.mark.parametrize("tipo, sim, ind, esperado", [
    ("existe_proximo", True, False, "sim"), ("existe_proximo", True, True, "sim"), ("existe_proximo", False, True, "indeterminado"),
    ("existe_proximo", False, False, "nao"),
    ("nenhum_proximo", True, False, "nao"), ("nenhum_proximo", True, True, "nao"), ("nenhum_proximo", False, True, "indeterminado"),
    ("nenhum_proximo", False, False, "sim"),
])
def test_relacao_3_valorada(tipo, sim, ind, esperado) -> None:
    assert relacao_3v(tipo, sim, ind) == esperado


# --- construcao do cenario ----------------------------------------------------------------------------------------------

def definicao(**evento) -> dict:
    ev = {"id": "isolado", "sujeito": "alvo", "quando_todas": ["sem_ref"], "duracao_s": 10, "lacuna_max_s": 2, "perda_max_s": 8, "encerrar_negativo_s": 4}
    ev.update(evento)
    return {"id": "r", "versao": "1", "contrato": 2, "vinculos": [{"especialista": "esp"}],
            "papeis": {"alvo": {"classe": "person", "combinacao": "todas", "condicoes": []},
                       "ref": {"classe": "person", "combinacao": "todas", "condicoes": []}},
            "relacoes": {"sem_ref": {"tipo": "nenhum_proximo", "sujeito": "alvo", "outro": "ref", "distancia": {"unidade": "pixels", "maximo": 100}},
                         "com_ref": {"tipo": "existe_proximo", "sujeito": "alvo", "outro": "ref", "distancia": {"unidade": "pixels", "maximo": 100}}},
            "eventos": [ev]}


def caixa(x: float, y: float = 0, w: float = 50, h: float = 100) -> tuple[float, float, float, float]:
    return (x, y, x + w, y + h)


def cena(t: float, dets: dict | None = None, *, ger: int = 1, cam: str = "c", quadro_id: int | None = None, classe: str = "person"):
    dets = dets or {}
    return criar_cena(cam, ger, int(t * 1000) if quadro_id is None else quadro_id, t, LARG, ALT, [Deteccao(k, classe, v) for k, v in dets.items()])


def passo(av: AvaliadorRegras, t: float, alvo: str | None = "sim", *, trilha: int = 1, ref: dict[int, str] | None = None, dets: dict | None = None):
    """Cena em `t` com o sujeito `trilha` (se `alvo` nao for None) isolado em x=0 e as referencias de `ref` ({trilha: estado}) longe, em x=500+."""
    d = {} if dets is None else dict(dets)
    estados = {"alvo": {}, "ref": {}}
    if alvo is not None:
        d[trilha] = caixa(0)
        estados["alvo"][trilha] = alvo
    for k, est in (ref or {}).items():
        d.setdefault(k, caixa(500 + 10 * k))
        estados["ref"][k] = est
    return av.avancar(cena(t, d), estados)


def linha(res, trilha: int = 1, evento: str = "isolado") -> dict:
    return next(x for x in res.eventos if x["id"] == evento and x["trilha_id"] == trilha)


def percorrer(av, tempos, alvo="sim", **kw):
    return [passo(av, t, alvo, **kw) for t in tempos]


# --- relacoes no avaliador -------------------------------------------------------------------------------------------------

def rels_de(estado_alvo, vizinhos: dict[int, str], onde: float = 60) -> dict:
    """Sujeito 1 em x=0 (largura 50); vizinhos (trilha: estado de ref) em x = 50 + onde (intervalo = onde)."""
    av = AvaliadorRegras(definicao())
    dets = {1: caixa(0), **{k: caixa(50 + onde, 0) for k in vizinhos}}
    estados = {"alvo": {1: estado_alvo}, "ref": dict(vizinhos)}
    res = av.avancar(cena(1.0, dets), estados)
    return next(s for s in res.sujeitos if s["trilha_id"] == 1)["relacoes"]


def test_relacoes_com_candidato_sim_nao_e_indeterminado_perto_e_longe() -> None:
    r = rels_de("sim", {2: "sim"})                                  # perto (60 <= 100) e sim
    assert r["sem_ref"]["estado"] == "nao" and r["com_ref"]["estado"] == "sim" and r["com_ref"]["proximos"] == [2]
    r = rels_de("sim", {2: "nao"})                                  # perto, mas o papel e nao: ignorado
    assert r["sem_ref"]["estado"] == "sim" and r["com_ref"]["estado"] == "nao" and r["com_ref"]["proximos"] == []
    r = rels_de("sim", {2: "indeterminado"})                        # perto e indeterminado: nao da para afirmar
    assert r["sem_ref"]["estado"] == "indeterminado" and r["com_ref"]["estado"] == "indeterminado" and r["com_ref"]["proximos_indeterminados"] == [2]
    r = rels_de("sim", {2: "indeterminado"}, onde=300)              # indeterminado, mas LONGE: nao importa
    assert r["sem_ref"]["estado"] == "sim" and r["com_ref"]["estado"] == "nao"
    r = rels_de("sim", {2: "sim", 3: "indeterminado"})              # sim vence indeterminado em existe_proximo
    assert r["com_ref"]["estado"] == "sim" and r["sem_ref"]["estado"] == "nao"
    r = rels_de("sim", {})                                          # sem candidato
    assert r["sem_ref"]["estado"] == "sim" and r["com_ref"]["estado"] == "nao"


def test_relacao_no_limite_da_distancia_vale_e_alem_nao() -> None:
    assert rels_de("sim", {2: "sim"}, onde=100)["com_ref"]["estado"] == "sim"
    assert rels_de("sim", {2: "sim"}, onde=100.5)["com_ref"]["estado"] == "nao"


def test_sujeito_indeterminado_ou_nao_nao_gera_relacao_afirmada() -> None:
    r = rels_de("indeterminado", {2: "sim"})
    assert r["sem_ref"]["estado"] == "indeterminado" and r["com_ref"]["estado"] == "indeterminado"
    r = rels_de("nao", {2: "sim"})
    assert r["sem_ref"]["estado"] == "nao_se_aplica" and r["com_ref"]["estado"] == "nao_se_aplica"


def test_o_proprio_sujeito_nao_e_candidato_de_si_mesmo() -> None:
    av = AvaliadorRegras(definicao())
    res = av.avancar(cena(1.0, {1: caixa(0)}), {"alvo": {1: "sim"}, "ref": {1: "sim"}})        # a mesma trilha e alvo E ref
    assert res.sujeitos[0]["relacoes"]["sem_ref"]["estado"] == "sim"


def test_classes_diferentes_com_o_mesmo_trilha_id_sao_sujeitos_diferentes() -> None:
    d = definicao()
    d["papeis"]["ref"]["classe"] = "veiculo"
    av = AvaliadorRegras(d)
    c = criar_cena("c", 1, 1, 1.0, LARG, ALT, [Deteccao(1, "person", caixa(0)), Deteccao(1, "veiculo", caixa(60))])
    res = av.avancar(c, {"alvo": {1: "sim"}, "ref": {1: "sim"}})
    pessoa = next(s for s in res.sujeitos if s["classe"] == "person")
    assert pessoa["relacoes"]["com_ref"]["estado"] == "sim" and pessoa["relacoes"]["com_ref"]["proximos"] == [1]      # o veiculo 1 nao e a pessoa 1


# --- persistencia ------------------------------------------------------------------------------------------------------------

def test_inicio_so_com_evidencia_sim_acumulada() -> None:
    av = AvaliadorRegras(definicao())
    res = percorrer(av, [0, 2, 4, 6, 8])                                   # 8 s de sim: ainda nao
    assert [linha(r)["estado"] for r in res] == ["candidato"] * 5 and not any(r.transicoes for r in res)
    assert linha(res[-1])["positivo_s"] == 8
    r = passo(av, 9.9)
    assert linha(r)["estado"] == "candidato" and linha(r)["transicao"] is None
    r = passo(av, 10)
    assert linha(r)["transicao"] == "iniciado" and linha(r)["estado"] == "ativo" and linha(r)["positivo_s"] == 10
    assert linha(passo(av, 11))["transicao"] is None                         # so inicia uma vez


def test_intervalos_irregulares_somam_o_tempo_real() -> None:
    av = AvaliadorRegras(definicao())
    for t in (0.0, 0.3, 1.9, 2.0, 3.5, 5.0, 6.9, 7.0, 8.9, 9.0):
        passo(av, t)
    r = passo(av, 10.4)
    assert linha(r)["transicao"] == "iniciado" and linha(r)["positivo_s"] == pytest.approx(10.4)


def test_indeterminado_nao_completa_o_tempo_nem_soma() -> None:
    av = AvaliadorRegras(definicao())
    for t in (0, 1, 2):
        passo(av, t)
    for t in (3, 4, 5, 6, 7, 8):
        r = passo(av, t, "indeterminado")
        assert linha(r)["transicao"] is None and linha(r)["positivo_s"] == 2
    r = passo(av, 11)                                                        # voltou o sim: intervalo 9 s > lacuna 2 -> recomeca
    assert linha(r)["estado"] == "candidato" and linha(r)["positivo_s"] == 0 and linha(r)["transicao"] is None


def test_indeterminado_curto_entre_sims_preserva_o_confirmado_mas_nao_credita_a_ponte() -> None:
    """Pausa curta: o tempo ja confirmado fica; o intervalo ATRAVES da amostra indeterminada nao e creditado ao retomar."""
    av = AvaliadorRegras(definicao())
    passo(av, 0)
    assert linha(passo(av, 1))["positivo_s"] == 1
    passo(av, 1.5, "indeterminado")
    r = passo(av, 2)                                                         # retomada: so fixa a nova referencia (antes creditava +1 s)
    assert linha(r)["positivo_s"] == 1 and linha(r)["estado"] == "candidato"
    assert linha(passo(av, 3))["positivo_s"] == 2                           # a partir daqui volta a acumular


# --- achado da revisao 03b: tempo indeterminado nao pode ser creditado retrospectivamente ------------------------------------

def test_reproducao_da_revisao_positivo_nao_inicia_antes_da_hora() -> None:
    """duracao 2 s: 0 sim, 1 sim, 1,5 indeterminado, 2 sim. Antes: positivo_s=2 e 'iniciado' em t=2 (com 1,5 s de evidencia real)."""
    av = AvaliadorRegras(definicao(duracao_s=2, lacuna_max_s=2, encerrar_negativo_s=2))
    passo(av, 0)
    passo(av, 1)
    passo(av, 1.5, "indeterminado")
    r = passo(av, 2)
    assert linha(r)["positivo_s"] == 1 and linha(r)["transicao"] is None and linha(r)["estado"] == "candidato"
    r = passo(av, 3)
    assert linha(r)["positivo_s"] == 2 and linha(r)["transicao"] == "iniciado"             # so com 2 s de evidencia VALIDA acumulada


def test_reproducao_da_revisao_negativo_nao_encerra_como_resolvido_antes_da_hora() -> None:
    """Ativo; 3 nao, 4 nao, 4,5 indeterminado, 5 nao (encerrar_negativo 2 s). Antes: 'encerrado/resolvido' em t=5."""
    av = AvaliadorRegras(definicao(duracao_s=2, lacuna_max_s=2, encerrar_negativo_s=2))
    for t in (0, 1, 2):
        r = passo(av, t)
    assert linha(r)["estado"] == "ativo"
    passo(av, 3, "nao")
    r = passo(av, 4, "nao")
    assert linha(r)["transicao"] is None
    passo(av, 4.5, "indeterminado")
    r = passo(av, 5, "nao")
    assert linha(r)["transicao"] is None and linha(r)["estado"] == "ativo"                  # a ponte 4 -> 5 nao conta como negativa
    r = passo(av, 6, "nao")
    assert linha(r)["transicao"] == "encerrado" and linha(r)["motivo"] == "resolvido_evidencia_negativa"       # 2 s de negativo REAL (4->... 5->6 + 3->4)


def test_sujeito_ausente_tambem_pausa_e_nao_credita_a_ponte() -> None:
    av = AvaliadorRegras(definicao(duracao_s=2))
    passo(av, 0)
    passo(av, 1)
    passo(av, 1.5, None)                                                     # sujeito ausente da cena
    assert linha(passo(av, 2))["positivo_s"] == 1
    assert linha(passo(av, 3))["positivo_s"] == 2


def test_quadros_nao_amostrados_continuam_creditados_dentro_da_lacuna() -> None:
    """Diferente de indeterminado: sem amostra no meio (fila descartou), o intervalo segue sujeito so a lacuna_max_s."""
    av = AvaliadorRegras(definicao(duracao_s=2, lacuna_max_s=2))
    passo(av, 0)
    passo(av, 1)
    assert linha(passo(av, 2))["positivo_s"] == 2                           # nenhuma amostra em t=1,5: ponte creditada


def test_varios_indeterminados_seguidos_so_fixam_referencia_na_retomada() -> None:
    av = AvaliadorRegras(definicao(duracao_s=10, lacuna_max_s=2))
    passo(av, 0)
    passo(av, 1)
    for t in (1.2, 1.4, 1.6):
        passo(av, t, "indeterminado")
    assert linha(passo(av, 1.8))["positivo_s"] == 1
    assert linha(passo(av, 2.8))["positivo_s"] == pytest.approx(2.0)


def test_pausa_mais_longa_que_a_lacuna_continua_reiniciando() -> None:
    av = AvaliadorRegras(definicao(duracao_s=10, lacuna_max_s=2))
    passo(av, 0)
    passo(av, 1)
    passo(av, 1.5, "indeterminado")
    passo(av, 2.5, "indeterminado")
    assert linha(passo(av, 3.5))["positivo_s"] == 0                         # 2,5 s desde o ultimo sim > lacuna: recomeca


def test_ausencia_no_negativo_nao_conta_como_evidencia_negativa() -> None:
    av = AvaliadorRegras(definicao(duracao_s=2, encerrar_negativo_s=2))
    for t in (0, 1, 2):
        passo(av, t)
    passo(av, 3, "nao")
    passo(av, 4, "nao")
    passo(av, 4.5, None)                                                     # ausente (nao e negativo)
    r = passo(av, 5, "nao")
    assert linha(r)["estado"] == "ativo" and linha(r)["transicao"] is None


def test_retomada_nao_deixa_a_pausa_vazar_para_as_proximas_amostras() -> None:
    av = AvaliadorRegras(definicao(duracao_s=10, lacuna_max_s=2))
    passo(av, 0)
    passo(av, 1, "indeterminado")
    passo(av, 1.5)                                                           # retomada (referencia)
    assert linha(passo(av, 2.0))["positivo_s"] == pytest.approx(0.5)        # ja acumula normalmente
    assert linha(passo(av, 3.0))["positivo_s"] == pytest.approx(1.5)


def test_lacuna_maior_que_o_limite_reinicia_e_o_tempo_da_lacuna_nao_conta() -> None:
    av = AvaliadorRegras(definicao())
    for t in range(0, 6):
        passo(av, t)                                                         # positivo 5
    passo(av, 5.5, None)                                                     # sujeito ausente
    r = passo(av, 8.5)                                                       # 3.5 s depois do ultimo sim > 2
    assert linha(r)["positivo_s"] == 0 and linha(r)["estado"] == "candidato"
    r = passo(av, 10.0)
    assert linha(r)["positivo_s"] == pytest.approx(1.5)
    assert linha(passo(av, 11.0))["positivo_s"] == pytest.approx(2.5)


def test_lacuna_exatamente_no_limite_ainda_sustenta() -> None:
    av = AvaliadorRegras(definicao())
    passo(av, 0)
    assert linha(passo(av, 2.0))["positivo_s"] == 2.0


def test_nao_zera_a_corrida_antes_de_ativar() -> None:
    av = AvaliadorRegras(definicao())
    for t in range(0, 6):
        passo(av, t)
    r = passo(av, 6, "nao")
    assert linha(r)["estado"] == "inativo" and linha(r)["positivo_s"] == 0
    for t in range(7, 17):                                                   # recomeca do zero: 10 s ate ativar (7..17)
        passo(av, t)
    assert linha(passo(av, 17))["transicao"] == "iniciado"


def ativar(av: AvaliadorRegras, ate: float = 10.0, **kw) -> None:
    for i in range(int(ate) + 1):
        res = passo(av, float(i), **kw)
    assert linha(res)["estado"] == "ativo"


def test_encerra_por_negativo_sustentado_com_motivo_resolvido() -> None:
    av = AvaliadorRegras(definicao())
    ativar(av)
    ultimo = None
    for t in (11, 12, 13):                                                   # nao 0, 1, 2 s sustentados: ainda ativo
        ultimo = passo(av, t, "nao")
        assert linha(ultimo)["estado"] == "ativo" and linha(ultimo)["transicao"] is None
    r = passo(av, 15, "nao")                                                 # 13 -> 15 (2 s <= lacuna): negativo 4 s
    assert linha(r)["transicao"] == "encerrado" and linha(r)["motivo"] == "resolvido_evidencia_negativa" and linha(r)["estado"] == "inativo"


def test_negativo_interrompido_por_sim_recomeca() -> None:
    av = AvaliadorRegras(definicao())
    ativar(av)
    passo(av, 11, "nao")
    passo(av, 12, "nao")
    passo(av, 13, "sim")                                                     # sim zera o negativo
    for t in (14, 15, 16, 17):
        r = passo(av, t, "nao")
        assert linha(r)["estado"] == "ativo"
    assert linha(passo(av, 18, "nao"))["motivo"] == "resolvido_evidencia_negativa"


def test_negativo_com_lacuna_maior_que_o_limite_nao_acumula() -> None:
    av = AvaliadorRegras(definicao())
    ativar(av)
    passo(av, 11, "nao")
    r = passo(av, 16, "nao")                                                 # 5 s depois: nao sustentado
    assert linha(r)["estado"] == "ativo"


def test_indeterminado_nunca_encerra_como_resolvido_e_perda_tem_motivo_proprio() -> None:
    av = AvaliadorRegras(definicao())
    ativar(av)
    for t in (12, 14, 16, 18):                                               # 8 s sem evidencia: nao passa de perda_max (8)
        assert linha(passo(av, t, "indeterminado"))["estado"] == "ativo"
    r = passo(av, 18.5, "indeterminado")                                     # 8.5 s > 8
    assert linha(r)["transicao"] == "encerrado" and linha(r)["motivo"] == "perda_de_observacao" and linha(r)["estado"] == "inativo"


def test_sustentacao_perdida_e_idade_da_ultima_evidencia() -> None:
    av = AvaliadorRegras(definicao())
    ativar(av)
    r = passo(av, 11.5, "indeterminado")
    assert linha(r)["sustentacao_perdida"] is False and linha(r)["idade_ultima_evidencia_s"] == 1.5
    r = passo(av, 13.0, "indeterminado")
    assert linha(r)["sustentacao_perdida"] is True and linha(r)["idade_ultima_evidencia_s"] == 3.0 and linha(r)["estado"] == "ativo"
    assert linha(passo(av, 13.5))["sustentacao_perdida"] is False


def test_sujeito_ausente_prolongado_tem_motivo_proprio_e_cena_vazia_avanca_o_estado() -> None:
    av = AvaliadorRegras(definicao())
    ativar(av)
    r = av.avancar(cena(11.0), {})                                           # cena vazia: o evento continua, sem sustentacao
    assert linha(r)["estado"] == "ativo" and r.sujeitos == []
    r = av.avancar(cena(18.0), {})                                           # 8 s ausente: nao passa
    assert linha(r)["estado"] == "ativo"
    r = av.avancar(cena(19.5), {})                                           # 9.5 s ausente > 8
    assert linha(r)["transicao"] == "encerrado" and linha(r)["motivo"] == "sujeito_ausente_prolongado"
    assert av.avancar(cena(20.0), {}).eventos == []                          # nada mais a acompanhar


def test_cena_vazia_nao_conta_como_negativa_nem_inicia() -> None:
    av = AvaliadorRegras(definicao())
    for t in (0, 1, 2, 3, 4):
        passo(av, t)
    r = av.avancar(cena(5.0), {})
    assert linha(r)["estado"] == "candidato" and linha(r)["positivo_s"] == 4 and r.transicoes == []
    ativar(av2 := AvaliadorRegras(definicao()))
    for t in (11, 12, 13, 14, 15):                                           # ausencia nunca soma tempo negativo
        r = av2.avancar(cena(float(t)), {})
        assert linha(r)["estado"] == "ativo"


def test_trilha_some_e_reaparece_com_o_mesmo_id_so_pelas_regras_de_lacuna() -> None:
    av = AvaliadorRegras(definicao())
    for t in (0, 1, 2, 3):
        passo(av, t)
    av.avancar(cena(3.5), {})
    r = passo(av, 4.0)                                                       # sumiu 0.5 s (cena SEM ele): corrida preservada, ponte nao creditada
    assert linha(r)["positivo_s"] == 3
    assert linha(passo(av, 5.0))["positivo_s"] == 4                         # e volta a acumular
    av.avancar(cena(5.5), {})
    av.avancar(cena(6.5), {})
    r = passo(av, 8.0)                                                       # 3 s depois do ultimo sim (5.0) > lacuna 2: recomeca
    assert linha(r)["positivo_s"] == 0


def test_ativo_que_reaparece_apos_lacuna_continua_ativo() -> None:
    av = AvaliadorRegras(definicao())
    ativar(av)
    av.avancar(cena(11.0), {})
    r = passo(av, 16.0)                                                      # voltou antes de perda_max (8): segue ativo, corrida recomecada
    assert linha(r)["estado"] == "ativo" and linha(r)["positivo_s"] == 0


def test_ids_diferentes_sao_sujeitos_independentes() -> None:
    av = AvaliadorRegras(definicao())
    for t in range(0, 6):
        passo(av, t)                                                         # trilha 1: 5 s
    r = av.avancar(cena(6.0, {1: caixa(0), 2: caixa(300)}), {"alvo": {1: "sim", 2: "sim"}, "ref": {}})
    assert linha(r, 1)["positivo_s"] == 6 and linha(r, 2)["positivo_s"] == 0 and linha(r, 2)["estado"] == "candidato"
    for t in range(7, 17):
        r = av.avancar(cena(float(t), {1: caixa(0)}), {"alvo": {1: "sim"}, "ref": {}})
    assert linha(r, 1)["estado"] == "ativo"
    r = av.avancar(cena(17.0, {3: caixa(0)}), {"alvo": {3: "sim"}, "ref": {}})        # outro ID: nada herdado do 1
    assert linha(r, 3)["positivo_s"] == 0 and linha(r, 3)["estado"] == "candidato" and linha(r, 1)["estado"] == "ativo"


def test_evidencia_negativa_de_um_sujeito_nao_afeta_o_outro() -> None:
    av = AvaliadorRegras(definicao())
    for t in range(0, 11):
        av.avancar(cena(float(t), {1: caixa(0), 2: caixa(300)}), {"alvo": {1: "sim", 2: "sim"}, "ref": {}})
    for t in (11, 12, 13, 14, 15):
        r = av.avancar(cena(float(t), {1: caixa(0), 2: caixa(300)}), {"alvo": {1: "nao", 2: "sim"}, "ref": {}})
    assert linha(r, 1)["estado"] == "inativo" and linha(r, 2)["estado"] == "ativo"


def test_relacao_nao_satisfeita_e_evidencia_negativa_e_indeterminada_nao() -> None:
    av = AvaliadorRegras(definicao())
    ativar(av)
    for t in (11, 12, 13):                                                   # vizinha 'sim' ao lado: nenhum_proximo = nao
        r = passo(av, float(t), ref={2: "sim"}, dets={2: caixa(60)})
    assert linha(r)["estado"] == "ativo"
    r = passo(av, 15.0, ref={2: "sim"}, dets={2: caixa(60)})
    assert linha(r)["motivo"] == "resolvido_evidencia_negativa"
    av = AvaliadorRegras(definicao())
    ativar(av)
    for t in (11, 12, 13, 14, 15, 16):                                       # vizinha indeterminada: nunca resolve
        r = passo(av, float(t), ref={2: "indeterminado"}, dets={2: caixa(60)})
        assert linha(r)["estado"] == "ativo" and linha(r)["motivo"] is None


def test_papel_nao_e_evidencia_negativa_do_evento_e_papel_indeterminado_nao() -> None:
    av = AvaliadorRegras(definicao())
    ativar(av)
    for t in (11, 12, 13, 14, 15):
        r = passo(av, float(t), "nao")
    assert linha(r)["motivo"] == "resolvido_evidencia_negativa"
    av = AvaliadorRegras(definicao())
    ativar(av)
    for t in (11, 12, 13, 14, 15):
        r = passo(av, float(t), "indeterminado")
    assert linha(r)["estado"] == "ativo"


def test_quando_todas_com_o_proprio_papel_e_varias_relacoes() -> None:
    av = AvaliadorRegras(definicao(quando_todas=["alvo", "sem_ref"], duracao_s=2))
    for t in (0, 1, 2):
        r = passo(av, float(t))
    assert linha(r)["transicao"] == "iniciado"


def test_cena_fora_de_ordem_ou_repetida_e_rejeitada_sem_alterar_o_estado() -> None:
    av = AvaliadorRegras(definicao())
    passo(av, 5)
    passo(av, 6)
    for t in (6.0, 5.5, 1.0):
        r = passo(av, t)
        assert r.rejeitada == "cena_fora_de_ordem" and r.eventos == [] and r.sujeitos == []
    assert linha(passo(av, 7))["positivo_s"] == 2                            # estado intacto


def test_cena_de_outra_camera_ou_geracao_e_rejeitada() -> None:
    av = AvaliadorRegras(definicao())
    passo(av, 1)
    assert av.avancar(cena(2.0, cam="outra"), {}).rejeitada == "camera_ou_geracao_diferente"
    assert av.avancar(cena(2.0, ger=2), {}).rejeitada == "camera_ou_geracao_diferente"
    assert av.avancar("nao e cena", {}).rejeitada == "cena_invalida"            # type: ignore[arg-type]


def test_cena_com_caixa_degenerada_e_rejeitada_inteira() -> None:
    from src.jutta_ped.motor.cena import Cena

    av = AvaliadorRegras(definicao())
    ruim = Cena("c", 1, 1, 1.0, LARG, ALT, (Deteccao(1, "person", (5, 5, 5, 9)),))
    assert av.avancar(ruim, {"alvo": {1: "sim"}}).rejeitada == "caixa_invalida"
    assert av.ultimo_t is None


def test_descartar_encerra_ativos_com_motivo_de_descarte_e_zera_tudo() -> None:
    av = AvaliadorRegras(definicao())
    ativar(av)
    passo(av, 11, trilha=2)                                                  # trilha 2 candidata (nao ativa)
    linhas = av.descartar("reconfigurado")
    assert [(x["id"], x["trilha_id"], x["transicao"], x["motivo"]) for x in linhas] == [("isolado", 1, "encerrado", "reconfigurado")]
    r = passo(av, 0.5)                                                       # relogio pode recomecar: estado novo
    assert linha(r)["estado"] == "candidato" and linha(r)["positivo_s"] == 0
    assert av.descartar("geracao_encerrada") == []                           # nada ativo
    assert av.descartar("sessao_encerrada") == []


def test_parametros_padrao_do_contrato() -> None:
    d = definicao()
    del d["eventos"][0]["lacuna_max_s"], d["eventos"][0]["perda_max_s"], d["eventos"][0]["encerrar_negativo_s"]
    av = AvaliadorRegras(d)
    ev = av.eventos[0]
    assert (ev.lacuna_s, ev.perda_s, ev.negativo_s) == (2.0, 10.0, 5.0)


def test_resultado_e_deterministico() -> None:
    seq = [(0, "sim"), (1, "sim"), (2, "indeterminado"), (3, "sim"), (4, "nao"), (9, "sim")]
    a, b = AvaliadorRegras(definicao()), AvaliadorRegras(definicao())
    assert [passo(a, t, e).eventos for t, e in seq] == [passo(b, t, e).eventos for t, e in seq]


# --- validacao do contrato 2 ---------------------------------------------------------------------------------------------------

def contrato_esp(classe: str = "person") -> dict:
    return {"versao": "1.0.0", "identidade": "i", "entrada": {"fonte": "visivel", "alvo": {"tipo": "recorte", "classe_mestre": classe}},
            "parametros": {}, "atributos": {"flag": {"tipo": "tri", "validade_ms": 1000, "categorias": []},
                                            "cor": {"tipo": "categoria", "validade_ms": 1000, "categorias": ["azul", "verde"]},
                                            "n": {"tipo": "numero", "validade_ms": 1000, "categorias": []},
                                            "m": {"tipo": "numero", "validade_ms": 1000, "categorias": []}}}


CONTRATOS = {"esp": contrato_esp(), "esp2": contrato_esp()}


def receita_ok() -> dict:
    return {"id": "r", "versao": "1", "contrato": 2, "vinculos": [{"especialista": "esp", "margem_recorte": 0.1, "orcamento_ms": 500}],
            "papeis": {"alvo": {"classe": "person", "combinacao": "todas", "condicoes": [{"especialista": "esp", "atributo": "flag", "igual": "sim", "validade_ms": 500}]},
                       "ref": {"classe": "person", "combinacao": "alguma", "condicoes": [{"especialista": "esp", "atributo": "n", "minimo": 0.5}]}},
            "relacoes": {"sem_ref": {"tipo": "nenhum_proximo", "sujeito": "alvo", "outro": "ref", "distancia": {"unidade": "altura_sujeito", "maximo": 1.5}}},
            "eventos": [{"id": "isolado", "sujeito": "alvo", "quando_todas": ["sem_ref"], "duracao_s": 10, "lacuna_max_s": 2, "perda_max_s": 8, "encerrar_negativo_s": 4}]}


def test_receita_contrato2_valida_passa_por_validar_receita_e_por_validar_contrato2() -> None:
    assert validar_receita(receita_ok(), CONTRATOS) == [] and validar_contrato2(receita_ok(), CONTRATOS, ("visivel",)) == []


def test_contrato1_continua_exatamente_como_antes_e_contrato_desconhecido_e_erro() -> None:
    v1 = {"id": "r", "versao": "1", "vinculos": [{"especialista": "esp"}], "regra": {"combinacao": "todas", "condicoes": [{"especialista": "esp", "atributo": "flag", "igual": "sim"}]}}
    assert validar_receita(v1, CONTRATOS) == [] and validar_receita({**v1, "contrato": 1}, CONTRATOS) == []
    assert any("contrato desconhecido" in x for x in validar_receita({**v1, "contrato": 3}, CONTRATOS))
    assert any("contrato desconhecido" in x for x in validar_receita({**v1, "contrato": True}, CONTRATOS))
    assert any("contrato desconhecido" in x for x in validar_receita({**v1, "contrato": "2"}, CONTRATOS))
    assert any("contrato desconhecido" in x for x in validar_receita({**v1, "contrato": None}, CONTRATOS))
    # v1 ignora chaves extras como sempre; contrato 2 NAO
    assert validar_receita({**v1, "extra": 1}, CONTRATOS) == []


def mexer(f):
    r = receita_ok()
    f(r)
    return r


CASOS = [
    ("chave desconhecida na receita", lambda r: r.update(regra={}), "desconhecidas"),
    ("typo no vinculo", lambda r: r["vinculos"][0].update(orcamento=5), "desconhecidas"),
    ("typo no papel", lambda r: r["papeis"]["alvo"].update(clase="x"), "desconhecidas"),
    ("typo na condicao", lambda r: r["papeis"]["alvo"]["condicoes"][0].update(igal="sim"), "desconhecidas"),
    ("typo na relacao", lambda r: r["relacoes"]["sem_ref"].update(outro_=1), "desconhecidas"),
    ("typo na distancia", lambda r: r["relacoes"]["sem_ref"]["distancia"].update(maxim=1), "desconhecidas"),
    ("typo no evento", lambda r: r["eventos"][0].update(duracao=3), "desconhecidas"),
    ("vinculo sem ativo", lambda r: r["vinculos"].append({"especialista": "ausente"}), "sem versao ativa"),
    ("vinculo repetido", lambda r: r["vinculos"].append({"especialista": "esp"}), "repetido"),
    ("margem invalida", lambda r: r["vinculos"][0].update(margem_recorte=3), "margem_recorte"),
    ("sem papeis", lambda r: r.update(papeis={}), "papeis"),
    ("papel nao objeto", lambda r: r["papeis"].update(x=3), "deve ser um objeto"),
    ("classe vazia", lambda r: r["papeis"]["alvo"].update(classe=""), "classe"),
    ("classe nao texto", lambda r: r["papeis"]["alvo"].update(classe=3), "classe"),
    ("combinacao invalida", lambda r: r["papeis"]["alvo"].update(combinacao="xor"), "combinacao"),
    ("alguma sem condicoes", lambda r: r["papeis"]["ref"].update(condicoes=[]), "vazias"),
    ("condicoes nao lista", lambda r: r["papeis"]["ref"].update(condicoes={}), "lista"),
    ("classe diferente da do especialista", lambda r: r["papeis"]["alvo"].update(classe="veiculo"), "classe mestre"),
    ("condicao sem operador", lambda r: r["papeis"]["alvo"]["condicoes"][0].pop("igual"), "exatamente um operador"),
    ("igual e minimo juntos", lambda r: r["papeis"]["ref"]["condicoes"][0].update(igual=1), "exatamente um operador"),
    ("minimo e maximo juntos", lambda r: r["papeis"]["ref"]["condicoes"][0].update(maximo=3), "exatamente um operador"),
    ("especialista sem vinculo", lambda r: r["papeis"]["alvo"]["condicoes"][0].update(especialista="esp2"), "sem vinculo"),
    ("atributo inexistente", lambda r: r["papeis"]["alvo"]["condicoes"][0].update(atributo="nada"), "inexistente"),
    ("igual tri invalido", lambda r: r["papeis"]["alvo"]["condicoes"][0].update(igual="talvez"), "tri"),
    ("igual categoria invalida", lambda r: r["papeis"]["alvo"]["condicoes"][0].update(atributo="cor", igual="roxo"), "categorias"),
    ("igual numero nao numerico", lambda r: r["papeis"]["alvo"]["condicoes"][0].update(atributo="n", igual="x"), "numerico"),
    ("minimo em atributo tri", lambda r: r["papeis"]["ref"]["condicoes"][0].update(atributo="flag"), "so vale em atributo numero"),
    ("minimo nao finito", lambda r: r["papeis"]["ref"]["condicoes"][0].update(minimo=float("nan")), "numero finito"),
    ("validade acima da do atributo", lambda r: r["papeis"]["alvo"]["condicoes"][0].update(validade_ms=5000), "validade_ms"),
    ("validade zero", lambda r: r["papeis"]["alvo"]["condicoes"][0].update(validade_ms=0), "validade_ms"),
    ("relacao tipo", lambda r: r["relacoes"]["sem_ref"].update(tipo="perto"), "tipo"),
    ("relacao sujeito inexistente", lambda r: r["relacoes"]["sem_ref"].update(sujeito="fantasma"), "papel existente"),
    ("relacao outro inexistente", lambda r: r["relacoes"]["sem_ref"].update(outro="fantasma"), "papel existente"),
    ("distancia ausente", lambda r: r["relacoes"]["sem_ref"].pop("distancia"), "distancia"),
    ("unidade invalida", lambda r: r["relacoes"]["sem_ref"]["distancia"].update(unidade="metros"), "unidade"),
    ("maximo zero", lambda r: r["relacoes"]["sem_ref"]["distancia"].update(maximo=0), "maximo"),
    ("maximo infinito", lambda r: r["relacoes"]["sem_ref"]["distancia"].update(maximo=float("inf")), "maximo"),
    ("maximo negativo", lambda r: r["relacoes"]["sem_ref"]["distancia"].update(maximo=-1), "maximo"),
    ("metrica invalida", lambda r: r["relacoes"]["sem_ref"]["distancia"].update(metrica="fisica"), "metrica"),
    ("id de relacao igual a papel", lambda r: r["relacoes"].update(alvo=r["relacoes"]["sem_ref"]), "distintos"),
    ("sem eventos", lambda r: r.update(eventos=[]), "eventos"),
    ("ids de evento repetidos", lambda r: r["eventos"].append(dict(r["eventos"][0])), "repetido"),
    ("evento sem id", lambda r: r["eventos"][0].pop("id"), "id"),
    ("evento sujeito inexistente", lambda r: r["eventos"][0].update(sujeito="fantasma"), "sujeito"),
    ("quando_todas vazio", lambda r: r["eventos"][0].update(quando_todas=[]), "quando_todas"),
    ("quando_todas desconhecido", lambda r: r["eventos"][0].update(quando_todas=["nada"]), "nem papel"),
    ("relacao de outro sujeito", lambda r: (r["relacoes"].update(inversa={"tipo": "existe_proximo", "sujeito": "ref", "outro": "alvo", "distancia": {"unidade": "pixels", "maximo": 5}}),
                                           r["eventos"][0].update(quando_todas=["inversa"])), "outro sujeito"),
    ("papel de outro sujeito em quando_todas", lambda r: r["eventos"][0].update(quando_todas=["ref"]), "proprio sujeito"),
    ("duracao ausente", lambda r: r["eventos"][0].pop("duracao_s"), "duracao_s"),
    ("duracao zero", lambda r: r["eventos"][0].update(duracao_s=0), "duracao_s"),
    ("duracao nan", lambda r: r["eventos"][0].update(duracao_s=float("nan")), "duracao_s"),
    ("lacuna negativa", lambda r: r["eventos"][0].update(lacuna_max_s=-1), "lacuna_max_s"),
    ("perda infinita", lambda r: r["eventos"][0].update(perda_max_s=float("inf")), "perda_max_s"),
    ("encerrar_negativo zero", lambda r: r["eventos"][0].update(encerrar_negativo_s=0), "encerrar_negativo_s"),
    ("sem id da receita", lambda r: r.pop("id"), "'id' e 'versao'"),
    ("receita nao objeto", None, "objeto"),
]


@pytest.mark.parametrize("nome, f, trecho", CASOS, ids=[c[0] for c in CASOS])
def test_validacao_contrato2_aponta_o_erro(nome, f, trecho) -> None:
    r = "texto" if f is None else mexer(f)
    problemas = validar_receita(r, CONTRATOS)
    assert problemas and any(trecho in x for x in problemas), problemas


def test_padroes_e_opcionais_do_contrato2_validos() -> None:
    r = receita_ok()
    r["relacoes"] = {}
    r["eventos"][0].update(quando_todas=["alvo"])
    for k in ("lacuna_max_s", "perda_max_s", "encerrar_negativo_s"):
        del r["eventos"][0][k]
    assert validar_receita(r, CONTRATOS) == []
    r["relacoes"] = {"sem_ref": receita_ok()["relacoes"]["sem_ref"]}
    r["relacoes"]["sem_ref"]["distancia"]["metrica"] = "ponto_de_apoio"
    assert validar_receita(r, CONTRATOS) == []
    r["papeis"]["livre"] = {"classe": "veiculo", "combinacao": "todas", "condicoes": []}      # papel so de classe
    assert validar_receita(r, CONTRATOS) == []


def test_estrutura_malformada_vira_problema_e_nunca_excecao() -> None:
    for lixo in (None, 3, [], {"contrato": 2}, {"contrato": 2, "id": "a", "versao": "1", "vinculos": [{"especialista": "esp"}], "papeis": {"a": {"classe": "p", "combinacao": "todas", "condicoes": [3]}},
                                                "eventos": [5]}):
        assert isinstance(validar_receita(lixo, CONTRATOS), list)
    assert validar_contrato2({"contrato": 2, "vinculos": [{"especialista": "esp"}], "papeis": {"a": []}, "eventos": [{"id": 1}], "relacoes": []}, CONTRATOS)


# --- maior_que (compara dois atributos numericos) -----------------------------------------------------------------------------

def com_maior_que(alvo: dict, atributo: str = "n") -> dict:
    r = receita_ok()
    r["vinculos"].append({"especialista": "esp2"})
    r["papeis"]["ref"]["condicoes"] = [{"especialista": "esp", "atributo": atributo, "maior_que": alvo}]
    return r


def test_maior_que_valido_no_mesmo_ou_em_outro_especialista() -> None:
    assert validar_receita(com_maior_que({"especialista": "esp", "atributo": "m"}), CONTRATOS) == []
    assert validar_receita(com_maior_que({"especialista": "esp2", "atributo": "m"}), CONTRATOS) == []


@pytest.mark.parametrize("alvo, atributo, trecho", [
    ({"especialista": "esp", "atributo": "nada"}, "n", "inexistente"),
    ({"especialista": "esp", "atributo": "flag"}, "n", "exige atributo numero"),
    ({"especialista": "esp", "atributo": "m"}, "flag", "exige atributo numero"),
    ({"especialista": "esp3", "atributo": "m"}, "n", "sem vinculo"),
    ({"especialista": "esp"}, "n", "sem vinculo"),
    ({"especialista": "esp", "atributo": "m", "extra": 1}, "n", "desconhecidas"),
    ("m", "n", "especialista, atributo"),
])
def test_maior_que_invalido(alvo, atributo, trecho) -> None:
    problemas = validar_receita(com_maior_que(alvo, atributo), CONTRATOS)
    assert any(trecho in x for x in problemas), problemas


def test_maior_que_exige_classe_mestre_do_papel_tambem_no_outro_especialista() -> None:
    contratos = {"esp": contrato_esp(), "esp2": contrato_esp("veiculo")}
    problemas = validar_receita(com_maior_que({"especialista": "esp2", "atributo": "m"}), contratos)
    assert any("classe mestre" in x for x in problemas)


def test_maior_que_com_outro_operador_na_mesma_condicao_e_erro() -> None:
    r = com_maior_que({"especialista": "esp", "atributo": "m"})
    r["papeis"]["ref"]["condicoes"][0]["minimo"] = 0.5
    assert any("exatamente um operador" in x for x in validar_receita(r, CONTRATOS))


def test_validar_vinculo_considera_condicoes_do_contrato2_e_o_atributo_comparado() -> None:
    r = com_maior_que({"especialista": "esp2", "atributo": "m"})
    assert validar_vinculo(r, "esp", CONTRATOS["esp"]) == [] and validar_vinculo(r, "esp2", CONTRATOS["esp2"]) == []
    quebrado = copy.deepcopy(CONTRATOS["esp"])
    quebrado["atributos"]["flag"]["tipo"] = "numero"                       # a condicao 'igual: sim' do alvo deixa de valer
    assert any("numerico" in x for x in validar_vinculo(r, "esp", quebrado))
    sem_m = copy.deepcopy(CONTRATOS["esp2"])
    del sem_m["atributos"]["m"]                                            # esp2 so aparece como atributo COMPARADO
    assert any("inexistente" in x for x in validar_vinculo(r, "esp2", sem_m))
    vira_tri = copy.deepcopy(CONTRATOS["esp2"])
    vira_tri["atributos"]["m"]["tipo"] = "tri"
    assert any("exige atributo numero" in x for x in validar_vinculo(r, "esp2", vira_tri))
    outra_classe = contrato_esp("veiculo")
    assert any("classe mestre" in x for x in validar_vinculo(r, "esp", outra_classe))
    assert validar_vinculo(r, "nao_vinculado", CONTRATOS["esp"]) == []
