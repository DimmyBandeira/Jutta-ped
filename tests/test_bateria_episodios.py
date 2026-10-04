"""Avaliador de episodios v2: associacao exclusiva, validacao/proveniencia da anotacao, semantica de trilhas, cobertura/timeout,
ambiguidade, leitura provisoria, propostas de IA preservadas e achados da revisao independente. Dados sinteticos; as propostas reais
sao lidas de docs/ (testes pulam se ausentes)."""
from __future__ import annotations

import copy
import json
import random
from pathlib import Path

import pytest

from tools.bateria.episodios import (
    VERSAO_AVALIADOR,
    aguardar_e_encerrar,
    associar,
    avaliar,
    calcular_cobertura,
    carregar_anotacoes,
    classificar_anotacao,
    cobertura_consistente,
    confirmar_anotacao,
    leitura_provisoria,
    validar_anotacao,
)

RAIZ = Path(__file__).resolve().parents[1]
SHA = "a" * 64


def cob_ok(total: int = 300) -> dict:
    return calcular_cobertura({"timeout": False, "status": "finished", "stop_reason": "eof", "last_error": None, "quadros_analisados": total}, total)


def analise(alertas, *, cobertura="ok", fps=10.0, quadros=300) -> dict:
    a = {"video": "v.dav", "sha256": SHA, "fps": fps, "quadros_do_arquivo": quadros,
         "alertas": [{"t_s": t, "estado": e, "trilha": tr, "quadro": 0} for t, e, tr in alertas], "suprimidos": [],
         "trilhas_distintas_nos_alertas": sorted({tr for _, _, tr in alertas if tr is not None})}
    cob = cob_ok(quadros) if cobertura == "ok" else cobertura
    if cob is not None:
        a["cobertura"] = cob
    return a


def ep(id_, ini, fim, estado="qualquer", **kw) -> dict:
    return {"id": id_, "inicio_s": ini, "fim_s": fim, "estado_esperado": estado, "criterio_inicio": "crianca sem adulto proximo", **kw}


PROC = {"confirmada": True, "fonte": "DVR do hospital", "tipo": "real", "confirmado_por": "Maria Souza"}


def anot(eps, **kw) -> dict:
    base = {"esquema": 2, "video": "v.dav", "sha256": SHA, "origem_revisao": "humana", "status": "confirmada", "revisado": True,
            "revisor": "Maria Souza", "data": "2026-10-04", "revisao_completa_do_video": True, "revisao_cega": True,
            "procedencia": copy.deepcopy(PROC), "episodios": eps}
    if not eps:
        base["negativo_confirmado"] = True
    base.update(kw)
    return base


def proposta_ia(eps, **kw) -> dict:
    prop = anot(eps, origem_revisao="ia", status="proposta_nao_confirmada", revisado=False, revisao_completa_do_video=False,
                revisor="Codex (IA)", revisao_cega=False, **kw)
    prop.pop("procedencia", None)                      # a proposta nao traz procedencia (pendencia visivel)
    if not eps:
        prop.pop("negativo_confirmado", None)          # um negativo proposto por IA nao e uma declaracao deliberada
    return prop


# --- achado 1: associacao exclusiva e deterministica -----------------------------------------------------------------

def test_um_alerta_nao_detecta_dois_episodios() -> None:
    r = avaliar(analise([(11.0, "CHILD_ALONE", 1)]), anot([ep("a", 0, 10), ep("b", 12, 20)]), tolerancia_s=2.0)
    m = r["metricas"]
    assert m["detectados"] == 1 and len(m["perdidos"]) == 1 and m["n_alertas_falsos"] == 0
    assert sum(e["alertas"] for e in m["por_episodio"]) == 1                # o alerta foi creditado a UM episodio
    assert m["alertas_ambiguos"] and set(m["alertas_ambiguos"][0]["candidatos"]) == {"a", "b"} and m["detectados_sem_ambiguidade"] == 0


def test_desempate_documentado_distancia_depois_inicio_depois_id() -> None:
    r = avaliar(analise([(11.0, "CHILD_ALONE", 1)]), anot([ep("b", 12, 20), ep("a", 0, 10)]))        # distancias iguais -> menor inicio_s
    assert r["metricas"]["perdidos"] == ["b"]
    r = avaliar(analise([(11.0, "CHILD_ALONE", 1)]), anot([ep("x", 12, 20), ep("y", 12, 20)]))       # inicio igual -> menor id
    assert r["metricas"]["perdidos"] == ["y"]


def test_dentro_do_intervalo_tem_prioridade_absoluta_sobre_a_tolerancia() -> None:
    """Revisao independente (12): com [0,10] e [10.4,20], alertas em 9.0 e 9.5 estao DENTRO de 'a'; a tolerancia nao pode empurrar um deles para 'b'."""
    r = avaliar(analise([(9.0, "CHILD_ALONE", 1), (9.5, "CHILD_ALONE", 2)]), anot([ep("a", 0, 10), ep("b", 10.4, 20)]), tolerancia_s=2.0)
    m = r["metricas"]
    assert m["perdidos"] == ["b"] and m["alertas_repetidos"] == 1 and m["alertas_ambiguos"] == []
    # tolerancia grande tambem nao muda isso
    assert avaliar(analise([(9.0, "CHILD_ALONE", 1), (9.5, "CHILD_ALONE", 2)]), anot([ep("a", 0, 10), ep("b", 10.4, 20)]), tolerancia_s=4.0)["metricas"]["perdidos"] == ["b"]


def test_casamento_maximo_detecta_os_dois_quando_ha_alertas_suficientes() -> None:
    # a1 (11.0) so alcanca por tolerancia e serve a "a" e "b"; a2 (9.0) esta DENTRO de "a": a1->b, a2->a
    r = avaliar(analise([(11.0, "CHILD_ALONE", 1), (9.0, "CHILD_ALONE", 2)]), anot([ep("a", 0, 10), ep("b", 12, 20)]))
    m = r["metricas"]
    assert m["detectados"] == 2 and m["perdidos"] == [] and m["alertas_repetidos"] == 0


def test_resultado_independe_da_ordem_dos_alertas_inclusive_trilhas_com_mais_de_um_digito() -> None:
    alertas = [(t, "CHILD_ALONE", tr) for t, tr in [(3.0, 10), (3.0, 2), (6.0, 1), (9.5, 11), (11.0, 3), (12.5, 2), (15.0, 100), (19.0, 4), (25.0, 5)]]
    eps = [ep("a", 2, 10), ep("b", 11, 16), ep("c", 18, 20)]
    ref = avaliar(analise(alertas), anot(eps))["metricas"]
    for semente in range(8):
        emb = alertas[:]
        random.Random(semente).shuffle(emb)
        assert avaliar(analise(emb), anot(eps))["metricas"] == ref


def test_estado_esperado_diferente_nao_casa() -> None:
    r = avaliar(analise([(5.0, "CHILD_ALONE", 1)]), anot([ep("a", 0, 10, "CHILD_SEPARATED")]))
    assert r["metricas"]["perdidos"] == ["a"] and r["metricas"]["n_alertas_falsos"] == 1


def test_detectado_perdido_falso_e_repetido_basicos() -> None:
    eps = [ep("e1", 10, 20, "CHILD_ALONE"), ep("e2", 40, 50, "CHILD_SEPARATED"), ep("e3", 70, 75)]
    alertas = [(12.0, "CHILD_ALONE", 5), (15.0, "CHILD_ALONE", 5), (18.0, "CHILD_ALONE", 9), (30.0, "CHILD_ALONE", 7), (45.0, "CHILD_ALONE", 8)]
    m = avaliar(analise(alertas, quadros=1000), anot(eps))["metricas"]          # 100 s de video: os episodios cabem
    assert m["detectados"] == 1 and m["perdidos"] == ["e2", "e3"] and m["n_alertas_falsos"] == 2 and m["alertas_repetidos"] == 2
    e1 = m["por_episodio"][0]
    assert e1["atraso_s"] == 2.0 and e1["alertas"] == 3 and e1["trilhas"] == [5, 9] and e1["criterio_inicio"]


# --- sobreposicao de sujeitos: ambiguidade explicita e associacao manual --------------------------------------------

def test_sobreposicao_marca_ambiguidade_e_nao_inventa_pessoa() -> None:
    eps = [ep("e1", 2.4, 10.8, "CHILD_ALONE", sujeito_local="primeira"), ep("e2", 10.4, 14.0, "CHILD_ALONE", sujeito_local="segunda")]
    r = avaliar(analise([(10.6, "CHILD_ALONE", 3)]), anot(eps))
    m = r["metricas"]
    assert m["detectados"] == 1 and m["detectados_sem_ambiguidade"] == 0          # o unico alerta cabe nos dois sujeitos
    assert len(m["alertas_ambiguos"]) == 1 and m["episodios_sobrepostos"] == [["e1", "e2"]] or m["episodios_sobrepostos"] == [("e1", "e2")]


def test_sobreposicao_sem_sujeitos_distintos_gera_aviso_nao_bloqueio() -> None:
    c = classificar_anotacao(anot([ep("e1", 0, 10), ep("e2", 8, 15)]))
    assert c["oficial"] and any("sobrepõem" in a for a in c["avisos"])
    c2 = classificar_anotacao(anot([ep("e1", 0, 10, sujeito_local="a"), ep("e2", 8, 15, sujeito_local="b")]))
    assert not any("sobrepõem" in a for a in c2["avisos"])


def test_associacao_manual_resolve_e_tambem_pode_declarar_alerta_que_nao_pertence() -> None:
    eps = [ep("e1", 2.4, 10.8), ep("e2", 10.4, 14.0)]
    al = [(10.6, "CHILD_ALONE", 3), (11.0, "CHILD_ALONE", 4)]
    com = avaliar(analise(al), anot(eps, associacoes=[{"t_s": 10.6, "trilha": 3, "episodio": "e1"}, {"t_s": 11.0, "trilha": 4, "episodio": "e2"}]))["metricas"]
    assert com["detectados"] == 2 and com["detectados_sem_ambiguidade"] == 2 and com["alertas_ambiguos"] == []
    falso = avaliar(analise(al[:1]), anot(eps, associacoes=[{"t_s": 10.6, "trilha": 3, "episodio": None}]))["metricas"]
    assert falso["n_alertas_falsos"] == 1 and falso["detectados"] == 0


def test_associacao_manual_fora_da_janela_e_sinalizada() -> None:
    """Revisao independente (13): uma decisao humana e aceita, mas nao some em silencio quando contradiz a janela/estado."""
    eps = [ep("e1", 0, 10), ep("e2", 40, 50, "CHILD_SEPARATED")]
    m = avaliar(analise([(6.0, "CHILD_ALONE", 1)], quadros=1000), anot(eps, associacoes=[{"t_s": 6.0, "trilha": 1, "episodio": "e2"}]))["metricas"]
    assert m["por_episodio"][1]["detectado"] and m["associacoes_manuais_fora_da_janela"] == [{"t_s": 6.0, "trilha": 1, "episodio": "e2"}]


@pytest.mark.parametrize("assoc,trecho", [
    ({"t_s": 5.0, "trilha": 1, "episodio": "inexistente"}, "associacoes"),
    ({"t_s": 5.0, "trilha": 1}, "associacoes"),                       # sem a chave 'episodio' (achado 14): era KeyError
    ({"t_s": 5.0, "trilha": "1", "episodio": "e1"}, "associacoes"),    # trilha com tipo errado
    ({"episodio": "e1"}, "associacoes"),
])
def test_associacao_malformada_e_recusada_sem_derrubar_o_lote(assoc, trecho) -> None:
    r = avaliar(analise([(5.0, "CHILD_ALONE", 1)]), anot([ep("e1", 0, 10)], associacoes=[assoc]))
    assert r["metricas"] is None and trecho in r["motivo"]


def test_associacao_manual_sem_alerta_correspondente_e_recusada() -> None:
    r = avaliar(analise([(5.0, "CHILD_ALONE", 1)]), anot([ep("e1", 0, 10)], associacoes=[{"t_s": 99.0, "trilha": 1, "episodio": "e1"}]))
    assert r["metricas"] is None and "sem alerta correspondente" in r["motivo"]


# --- achado 2: validacao da anotacao e proveniencia ---------------------------------------------------------------------

def test_revisao_sem_lista_de_episodios_nao_vira_negativo() -> None:
    sem_lista = {k: v for k, v in anot([ep("a", 0, 5)]).items() if k != "episodios"}
    r = avaliar(analise([(1.0, "CHILD_ALONE", 1)]), sem_lista)
    assert r["metricas"] is None and "episodios" in r["motivo"].lower() and r["anotacao"]["estado"] == "invalida"


def test_lista_vazia_exige_negativo_deliberado() -> None:
    assert any("negativo_confirmado" in e for e in validar_anotacao(anot([], negativo_confirmado=False)))
    r = avaliar(analise([(1.0, "CHILD_ALONE", 1), (2.0, "CHILD_ALONE", 2)]), anot([]))
    assert r["metricas"]["n_alertas_falsos"] == 2 and r["metricas"]["episodios_positivos"] == 0
    assert "negativo de 30 s" in r["metricas"]["aviso_negativo_curto"] and "NAO estima falsos alertas por hora" in r["metricas"]["aviso_negativo_curto"]
    assert any("incompativel" in e for e in validar_anotacao(anot([ep("a", 0, 5)], negativo_confirmado=True)))


@pytest.mark.parametrize("mut,trecho", [
    (lambda a: a.pop("revisor"), "revisor"),
    (lambda a: a.update(revisor="  "), "revisor"),
    (lambda a: a.pop("data"), "data"),
    (lambda a: a.update(data="04/10/2026"), "data"),
    (lambda a: a.update(sha256="xyz"), "sha256"),
    (lambda a: a.update(revisado="sim"), "revisado"),
    (lambda a: a.update(revisao_cega="nao"), "revisao_cega"),
    (lambda a: a.pop("origem_revisao"), "origem_revisao"),
    (lambda a: a.update(status="aprovada"), "status"),
    (lambda a: a.update(esquema=1), "esquema"),
    (lambda a: a.update(revisado=False), "incoerentes"),
    (lambda a: a.update(origem_revisao="ia"), "ia"),
    (lambda a: a["procedencia"].update(fonte=""), "fonte"),                       # achado 1: confirmada com fonte vazia
    (lambda a: a["procedencia"].update(fonte="   "), "fonte"),
    (lambda a: a["procedencia"].update(tipo="talvez"), "tipo"),
    (lambda a: a["procedencia"].pop("tipo"), "tipo"),
    (lambda a: a["procedencia"].pop("confirmado_por"), "confirmado_por"),
    (lambda a: a.update(procedencia={"confirmada": "sim"}), "procedencia"),
])
def test_campos_obrigatorios_e_coerencia(mut, trecho) -> None:
    a = anot([ep("a", 0, 5)])
    mut(a)
    erros = validar_anotacao(a)
    assert erros and any(trecho in e for e in erros), erros


@pytest.mark.parametrize("episodio,trecho", [
    (ep("a", 5, 5), "inicio_s < fim_s"),
    (ep("a", 6, 5), "inicio_s < fim_s"),
    (ep("a", -1, 5), "negativo"),
    (ep("a", float("nan"), 5), "finitos"),
    (ep("a", 0, float("inf")), "finitos"),
    (ep("a", True, 5), "finitos"),
    (ep("a", "0", 5), "finitos"),
    (ep("a", 0, 5, "TALVEZ"), "estado_esperado"),
    ({"inicio_s": 0, "fim_s": 5, "estado_esperado": "qualquer"}, "'id'"),
    (ep("a", 0, 500), "alem da duracao"),
    (ep("a", 0, 5, criterio_inicio=7), "criterio_inicio"),
])
def test_episodios_invalidos(episodio, trecho) -> None:
    erros = validar_anotacao(anot([episodio]), duracao_s=30.0)
    assert any(trecho in e for e in erros), erros


def test_id_duplicado() -> None:
    assert any("duplicado" in e for e in validar_anotacao(anot([ep("a", 0, 5), ep("a", 6, 9)])))


@pytest.mark.parametrize("lixo", [[1], "texto", 5, ["x"], {"episodios": 3}])
def test_anotacao_nao_dict_nao_derruba_o_lote(lixo) -> None:
    """Revisao independente (7): `rev.get` antes da validacao quebrava o comando `episodios` inteiro."""
    r = avaliar(analise([(1.0, "CHILD_ALONE", 1)]), lixo)
    assert r["metricas"] is None and r["anotacao"]["estado"] == "invalida"
    lp = leitura_provisoria(analise([(1.0, "CHILD_ALONE", 1)]), lixo)
    assert lp["leitura"] is None and lp["oficial"] is False


def test_ia_nunca_gera_metricas_oficiais_mesmo_com_booleanos_verdadeiros() -> None:
    a = proposta_ia([ep("a", 0, 10)])
    r = avaliar(analise([(5.0, "CHILD_ALONE", 1)]), a)
    assert r["metricas"] is None and r["anotacao"]["estado"] == "proposta_ia" and not r["anotacao"]["oficial"]
    forjada = {**a, "status": "confirmada", "revisado": True}                      # "confirmar" uma IA editando booleanos
    r = avaliar(analise([(5.0, "CHILD_ALONE", 1)]), forjada)
    assert r["metricas"] is None and r["anotacao"]["estado"] == "invalida"


@pytest.mark.parametrize("nome", ["Claude", "Codex (IA)", "GPT", "bot", "Sonnet 5.5", "Anthropic", "Claude Opus", "assistente virtual", "ChatGPT"])
def test_revisor_com_cara_de_ia_em_anotacao_humana_e_recusado(nome) -> None:
    c = classificar_anotacao(anot([ep("a", 0, 5)], revisor=nome))
    assert not c["oficial"] and any("IA" in m for m in c["motivos"]), nome


@pytest.mark.parametrize("nome", ["Maria Souza", "Joao Lima", "Fabiana Ribeiro", "Aiala Santos", "Dr. Paulo Opuso"])
def test_nomes_comuns_nao_disparam_o_filtro_de_ia(nome) -> None:
    assert classificar_anotacao(anot([ep("a", 0, 5)], revisor=nome))["oficial"]


def test_criterio_de_inicio_e_obrigatorio_para_metricas_oficiais() -> None:
    sem = ep("dvr1-e1", 9.2, 31.9)
    sem.pop("criterio_inicio")
    c = classificar_anotacao(anot([sem]))
    assert not c["oficial"] and any("dvr1-e1" in m and "criterio_inicio" in m for m in c["motivos"])
    r = avaliar(analise([(15.0, "CHILD_SEPARATED", 4)], quadros=400), anot([sem]))          # video de 40 s
    assert r["metricas"] is None and "criterio_inicio" in r["motivo"]


def test_pendencias_ficam_visiveis_e_revisao_nao_cega_gera_aviso() -> None:
    c = classificar_anotacao(anot([ep("a", 0, 5)], revisao_completa_do_video=False, procedencia={"confirmada": False}, revisao_cega=False))
    assert not c["oficial"] and len(c["motivos"]) == 2 and any("nao cega" in a for a in c["avisos"])
    r = avaliar(analise([(1.0, "CHILD_ALONE", 1)]), anot([ep("a", 0, 5)], revisao_completa_do_video=False))
    assert r["metricas"] is None and "revisao_completa" in r["motivo"]
    ok = avaliar(analise([(1.0, "CHILD_ALONE", 1)]), anot([ep("a", 0, 5)]))
    assert ok["metricas"] is not None and ok["anotacao"]["procedencia"]["tipo"] == "real" and ok["anotacao"]["revisao_cega"] is True


def test_sha_diferente_e_sem_anotacao() -> None:
    assert avaliar(analise([]), anot([ep("a", 0, 5)], sha256="b" * 64))["metricas"] is None
    r = avaliar(analise([(1.0, "CHILD_ALONE", 1)]), None)
    assert r["metricas"] is None and r["anotacao"]["estado"] == "sem_anotacao" and r["alertas_emitidos"] == 1


# --- achado 3: semantica de trilhas ----------------------------------------------------------------------------------

def test_trilhas_adicionais_e_transicoes_sao_coisas_diferentes() -> None:
    m = avaliar(analise([(1.0, "CHILD_ALONE", 1), (2.0, "CHILD_ALONE", 2), (3.0, "CHILD_ALONE", 1)]), anot([ep("a", 0, 10)]))["metricas"]
    e = m["por_episodio"][0]
    assert e["trilhas"] == [1, 2] and e["trilhas_adicionais"] == 1 and e["transicoes_de_trilha"] == 2          # 1->2->1 = 2 transicoes
    assert "trocas_de_trilha" not in m and m["trilhas_adicionais"] == 1 and m["transicoes_de_trilha"] == 2


def test_duas_criancas_aparecem_como_trilhas_adicionais_sem_chamar_de_fragmentacao() -> None:
    m = avaliar(analise([(1.0, "CHILD_ALONE", 7), (1.2, "CHILD_ALONE", 8)]), anot([ep("a", 0, 10)]))["metricas"]
    assert m["trilhas_adicionais"] == 1 and m["alertas_repetidos"] == 1           # indicio, nao prova de troca de trilha da mesma crianca


def test_trilha_ausente_nao_conta() -> None:
    m = avaliar(analise([(1.0, "CHILD_ALONE", None), (2.0, "CHILD_ALONE", 4)]), anot([ep("a", 0, 10)]))["metricas"]
    assert m["por_episodio"][0]["trilhas"] == [4] and m["transicoes_de_trilha"] == 0


# --- achado 4: cobertura integral e timeout ----------------------------------------------------------------------------

def _cob(**kw) -> dict:
    base = cob_ok()
    base.update(kw)
    return base


@pytest.mark.parametrize("cobertura,trecho", [
    (calcular_cobertura({"timeout": True, "status": "running", "stop_reason": None, "last_error": None, "quadros_analisados": 3}, 300), "timeout"),
    (calcular_cobertura({"timeout": False, "status": "finished", "stop_reason": "eof", "last_error": None, "quadros_analisados": 3}, 300), "diferem"),
    (None, "anterior a versao 2"),
    ({"integral": True, "motivos_nao_integral": []}, "nao confiavel"),                               # achado 8: flag sem campos-base
    (_cob(quadros_analisados=10), "inconsistente"),                                                      # integral=true mas 10 != 300
    (_cob(timeout=True), "inconsistente"),
    (_cob(terminou_por_eof=False), "inconsistente"),
    (_cob(erro="boom"), "inconsistente"),
])
def test_sem_cobertura_integral_comprovada_nao_ha_metricas(cobertura, trecho) -> None:
    r = avaliar(analise([(1.0, "CHILD_ALONE", 1)], cobertura=cobertura), anot([ep("a", 0, 5)]))
    assert r["metricas"] is None and trecho in r["motivo"]


def test_cobertura_consistente_aceita_a_integral_real() -> None:
    assert cobertura_consistente(cob_ok()) == (True, "")


class _Estado:
    def __init__(self, **kw) -> None:
        self.status, self.stop_reason, self.last_error, self.frames_processed = kw.get("status", "running"), kw.get("stop_reason"), kw.get("last_error"), kw.get("frames", 0)


class _Thread:
    def __init__(self, vivo: bool) -> None:
        self.vivo = vivo

    def is_alive(self) -> bool:
        return self.vivo


class _Sessao:
    def __init__(self, thread_vivo: bool, estado: _Estado) -> None:
        self.thread, self.state, self.parou = _Thread(thread_vivo), estado, False

    def stop(self) -> None:
        self.parou = True
        self.state.status, self.state.stop_reason = "stopped", "stop_requested"        # o stop() SOBRESCREVE o motivo real
        self.thread.vivo = False


def _relogio():
    t = [0.0]
    return (lambda: t[0]), (lambda s: t.__setitem__(0, t[0] + s))


def test_timeout_e_registrado_antes_do_stop_e_nao_parece_termino_normal() -> None:
    sessao = _Sessao(True, _Estado(status="running", frames=40))
    rel, dormir = _relogio()
    inst = aguardar_e_encerrar(sessao, 5.0, relogio=rel, dormir=dormir)
    assert sessao.parou and inst["timeout"] is True and inst["status"] == "running" and inst["stop_reason"] is None     # motivo ANTES do stop
    cob = calcular_cobertura(inst, 480)
    assert cob["integral"] is False and cob["timeout"] is True and "timeout" in cob["motivos_nao_integral"][0]


def test_termino_por_eof_com_todos_os_quadros_e_integral() -> None:
    sessao = _Sessao(False, _Estado(status="finished", stop_reason="eof", frames=480))
    rel, dormir = _relogio()
    inst = aguardar_e_encerrar(sessao, 5.0, relogio=rel, dormir=dormir)
    cob = calcular_cobertura(inst, 480)
    assert cob["integral"] and cob["terminou_por_eof"] and cob["razao"] == 1.0 and cob["motivos_nao_integral"] == []


@pytest.mark.parametrize("estado,quadros,frases", [
    (dict(status="finished", stop_reason="eof", frames=470), 480, "diferem"),                 # menos quadros que o arquivo
    (dict(status="finished", stop_reason="read_failed", frames=480), 480, "nao terminou por fim de arquivo"),
    (dict(status="finished", stop_reason="eof", frames=480, last_error="boom"), 480, "boom"),
    (dict(status="finished", stop_reason="eof", frames=480), 0, "nao declara numero de quadros"),
])
def test_coberturas_nao_integrais(estado, quadros, frases) -> None:
    sessao = _Sessao(False, _Estado(**estado))
    rel, dormir = _relogio()
    inst = aguardar_e_encerrar(sessao, 5.0, relogio=rel, dormir=dormir)
    cob = calcular_cobertura(inst, quadros)
    assert not cob["integral"] and any(frases in m for m in cob["motivos_nao_integral"])


# --- leitura provisoria e as propostas de IA preservadas ----------------------------------------------------------------

def test_leitura_provisoria_e_rotulada_nao_oficial_dentro_do_proprio_corte_e_usa_outros_nomes() -> None:
    lp = leitura_provisoria(analise([(5.0, "CHILD_ALONE", 1), (6.0, "CHILD_ALONE", 2), (50.0, "CHILD_ALONE", 3)], quadros=1000), proposta_ia([ep("a", 0, 10)]))
    assert lp["oficial"] is False and "nao sao metricas" in lp["aviso"].lower()
    leitura = lp["leitura"]
    assert leitura["_oficial"] is False and "NAO sao metricas" in leitura["_aviso"] or "nao sao metricas" in leitura["_aviso"].lower()   # sobrevive ao recorte de `.leitura`
    assert leitura["detectados"] == 1 and leitura["alertas_repetidos"] == 1 and leitura["n_alertas_fora_dos_episodios_propostos"] == 1
    assert "alertas_falsos" not in leitura and "n_alertas_falsos" not in leitura                 # "falso" exige revisao completa
    assert lp["anotacao"]["pendencias"]
    assert avaliar(analise([(5.0, "CHILD_ALONE", 1)]), proposta_ia([ep("a", 0, 10)]))["metricas"] is None


def test_proposta_de_negativo_sem_declaracao_aparece_so_na_leitura_provisoria() -> None:
    prop = proposta_ia([])
    assert avaliar(analise([(1.0, "CHILD_ALONE", 1)]), prop)["metricas"] is None
    lp = leitura_provisoria(analise([(1.0, "CHILD_ALONE", 1)]), prop)
    assert lp["leitura"]["n_alertas_fora_dos_episodios_propostos"] == 1 and lp["leitura"]["_negativo_declarado"] is False and lp["oficial"] is False


def test_leitura_provisoria_avisa_cobertura_nao_comprovada() -> None:
    lp = leitura_provisoria(analise([(1.0, "CHILD_ALONE", 1)], cobertura=None), proposta_ia([ep("a", 0, 5)]))
    assert lp["ressalva"] == "cobertura integral da analise nao comprovada"


PROPOSTAS = RAIZ / "docs" / "referencia" / "2026-10-04" / "propostas_ia"


@pytest.mark.skipif(not PROPOSTAS.exists(), reason="propostas de IA ausentes")
def test_propostas_de_ia_reais_permanecem_propostas_e_nao_geram_metricas() -> None:
    arquivos = sorted(PROPOSTAS.glob("*.json"))
    assert len(arquivos) == 3
    for arq in arquivos:
        p = json.loads(arq.read_text(encoding="utf-8"))
        assert p["revisado"] is False and p["revisao_completa_do_video"] is False and p["status"] == "proposta_nao_confirmada" and p["origem_revisao"] == "ia"
        c = classificar_anotacao(p, exigir_negativo_deliberado=False)
        assert c["valida"] and not c["oficial"] and c["estado"] == "proposta_ia"
        # pendencias visiveis: procedencia, revisao completa e (se houver episodios) criterio de inicio
        assert any("procedencia" in m for m in c["motivos"]) and any("revisao_completa" in m for m in c["motivos"])
        if p["episodios"]:
            assert any("criterio_inicio" in m for m in c["motivos"])
        assert avaliar(analise([], fps=30.0, quadros=300) | {"sha256": p["sha256"]}, p)["metricas"] is None


@pytest.mark.skipif(not PROPOSTAS.exists(), reason="propostas de IA ausentes")
def test_leitura_provisoria_das_propostas_reproduz_a_leitura_visual_registrada() -> None:
    dvr3 = json.loads((PROPOSTAS / "dvr3.json").read_text(encoding="utf-8"))
    al = [(6.333, "CHILD_ALONE", 1), (6.567, "CHILD_ALONE", 18)]
    a = analise(al, fps=30.0, quadros=441) | {"sha256": dvr3["sha256"]}
    lp = leitura_provisoria(a, dvr3)["leitura"]
    assert lp["detectados"] == 1 and lp["perdidos"] == ["dvr3-e2"] and lp["alertas_repetidos"] == 1 and lp["n_alertas_fora_dos_episodios_propostos"] == 0
    assert leitura_provisoria(a, dvr3, tolerancia_s=3.9)["leitura"]["perdidos"] == ["dvr3-e2"]       # achado 12: tolerancia nao credita o e2
    dvr1 = json.loads((PROPOSTAS / "dvr1.json").read_text(encoding="utf-8"))
    a1 = analise([(15.667, "CHILD_SEPARATED", 4), (21.267, "CHILD_SEPARATED", 20), (31.133, "CHILD_SEPARATED", 2)], fps=15.0, quadros=480) | {"sha256": dvr1["sha256"]}
    l1 = leitura_provisoria(a1, dvr1)["leitura"]
    assert l1["detectados"] == 1 and l1["alertas_repetidos"] == 2 and l1["trilhas_adicionais"] == 2 and l1["transicoes_de_trilha"] == 2


# --- confirmacao humana (caminho), sem confirmar nada sozinho -------------------------------------------------------------

DECL = dict(revisor="Maria Souza", data="2026-10-05", revisao_completa=True, episodios_conferidos=True, procedencia_confirmada=True,
            procedencia_fonte="DVR do 5o andar", procedencia_tipo="real", criterios_inicio={"a": "adulto sai do campo"})


def test_confirmar_exige_cada_declaracao_explicita_e_preserva_a_proposta() -> None:
    prop = proposta_ia([{k: v for k, v in ep("a", 0, 10).items() if k != "criterio_inicio"}])
    prop["metodo"] = {"tipo": "folhas_de_contato", "limite": "nao equivale a revisao humana integral"}
    original = copy.deepcopy(prop)
    novo = confirmar_anotacao(prop, **DECL)
    assert prop == original                                                   # a proposta nao e alterada
    assert classificar_anotacao(novo)["oficial"] and "metodo" not in novo      # achado 4: o metodo contraditorio nao fica na confirmada
    assert novo["derivada_de_proposta"]["revisor"] == "Codex (IA)" and novo["derivada_de_proposta"]["metodo_original"]["tipo"] == "folhas_de_contato"
    assert novo["procedencia"]["confirmado_por"] == "Maria Souza" and novo["revisao_cega"] is False
    for kw, trecho in (({"revisor": "Claude"}, "pessoa"), ({"revisao_completa": False}, "por inteiro"), ({"episodios_conferidos": False}, "conferido"),
                       ({"procedencia_confirmada": False}, "procedencia"), ({"procedencia_fonte": " "}, "fonte"), ({"procedencia_tipo": "talvez"}, "tipo"),
                       ({"criterios_inicio": {}}, "criterio_inicio")):
        with pytest.raises(ValueError, match=trecho):
            confirmar_anotacao(prop, **(DECL | kw))


def test_confirmar_nao_assume_procedencia_confirmada_por_padrao() -> None:
    """Achado 5: nenhuma declaracao tem valor padrao que confirme por voce."""
    import inspect

    sig = inspect.signature(confirmar_anotacao)
    for nome in ("revisao_completa", "episodios_conferidos", "procedencia_confirmada", "procedencia_fonte", "procedencia_tipo"):
        assert sig.parameters[nome].default is inspect.Parameter.empty, nome
    assert sig.parameters["revisao_cega"].default is False


def test_confirmar_proposta_de_negativo_declara_o_negativo() -> None:
    novo = confirmar_anotacao(proposta_ia([]), **(DECL | {"revisor": "Joao Lima", "criterios_inicio": {}}))
    assert novo["negativo_confirmado"] is True and classificar_anotacao(novo)["oficial"]


def test_carregar_anotacoes_recusa_duas_para_o_mesmo_video(tmp_path: Path) -> None:
    (tmp_path / "a.json").write_text(json.dumps(anot([ep("a", 0, 5)])), encoding="utf-8")
    assert SHA in carregar_anotacoes(tmp_path)
    (tmp_path / "b.json").write_text(json.dumps(anot([ep("b", 0, 5)])), encoding="utf-8")
    with pytest.raises(ValueError, match="mais de uma anotacao"):
        carregar_anotacoes(tmp_path)


def test_versao_do_avaliador_vai_nas_saidas() -> None:
    assert VERSAO_AVALIADOR == 2 and avaliar(analise([]), None)["versao_avaliador"] == 2


def test_associar_e_deterministico_e_exclusivo() -> None:
    eps = [ep("a", 0, 10), ep("b", 9, 20)]
    alertas = [{"t_s": t, "estado": "CHILD_ALONE", "trilha": 1} for t in (9.5, 9.6, 9.7)]
    r1 = associar(alertas, eps, 2.0, [])
    r2 = associar(list(reversed(alertas)), eps, 2.0, [])
    assert sorted(map(str, r1["atribuicao"].values())) == sorted(map(str, r2["atribuicao"].values())) and len(r1["atribuicao"]) == 3
