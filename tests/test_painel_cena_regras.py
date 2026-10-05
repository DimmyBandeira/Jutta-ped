"""Receitas contrato 2 (relacoes entre trilhas + persistencia) no PAINEL: importacao, recusa de edicao nao preservavel,
execucao em sombra pela Cena (porta -> despacho -> motor -> feed) com relogio controlado. Dados sinteticos: provam o contrato e a
generalidade, nao a qualidade de nenhum modelo."""
from __future__ import annotations

import json

import numpy as np
from painel_receitas_helpers import BEBE, COR, adm, amb, dados_receita  # noqa: F401
from test_painel_sombra import _flash, _preparar  # noqa: F401
from test_painel_sombra_corridas import PRAZO, _desligar, _feed, _ligar  # noqa: F401

from src.jutta_ped.motor.cena import Deteccao, criar_cena
from src.jutta_ped.painel.models import Receita, ReceitaVersao

NOME = "isolado-v2"


def definicao_v2(**extra) -> dict:
    d = {"contrato": 2,
         "vinculos": [{"especialista": BEBE, "orcamento_ms": 500}, {"especialista": COR, "orcamento_ms": 500}],
         "papeis": {"alvo": {"classe": "person", "combinacao": "todas", "condicoes": [{"especialista": BEBE, "atributo": "bebe_no_colo", "igual": "sim"}]},
                    "ref": {"classe": "person", "combinacao": "todas", "condicoes": [{"especialista": COR, "atributo": "cor_uniforme", "igual": "azul"}]}},
         "relacoes": {"sem_ref": {"tipo": "nenhum_proximo", "sujeito": "alvo", "outro": "ref", "distancia": {"unidade": "altura_sujeito", "maximo": 1.0}}},
         "eventos": [{"id": "isolado", "sujeito": "alvo", "quando_todas": ["sem_ref"], "duracao_s": 3, "lacuna_max_s": 2, "perda_max_s": 5, "encerrar_negativo_s": 2}]}
    d.update(extra)
    return d


def importar(adm, definicao, nome=NOME, descricao="v2 de teste", bruto=None):
    return adm.post("/painel/receitas/importar", {"nome": nome, "descricao": descricao, "definicao_json": bruto if bruto is not None else json.dumps(definicao)})


def contar_versoes(adm) -> tuple[int, int]:
    return adm.contar(Receita), adm.contar(ReceitaVersao)


# ------------------------------------------------------------------------------------------------ importacao

def test_importa_receita_contrato2_como_versao_imutavel_e_valida_contra_especialistas(adm) -> None:
    r = importar(adm, definicao_v2())
    assert r.status_code == 303 and _flash(adm)["tipo"] == "ok"
    with adm.database.session() as db:
        v = db.query(ReceitaVersao).one()
        d = json.loads(v.definicao_json)
        assert d["id"] == NOME and d["versao"] == "1" and d["contrato"] == 2 and v.config_hash.startswith("sha256:")
    adm.client.get("/painel/receitas")
    visao = adm.contexto("receitas.html")["receitas"][0]
    assert visao["contrato"] == 2 and visao["editavel"] is False and "contrato" in visao["versoes"][0]["resumo"]
    r2 = importar(adm, definicao_v2(), descricao="")                              # mesmo nome: nova versao, a anterior fica intacta
    assert r2.status_code == 303 and contar_versoes(adm) == (1, 2)


def test_importacao_invalida_nao_grava_nada_e_diz_o_porque(adm) -> None:
    antes = contar_versoes(adm)
    ruins = {
        "chave desconhecida": (definicao_v2(typo=1), "typo"),
        "categoria inexistente": ({**definicao_v2(), "papeis": {**definicao_v2()["papeis"], "ref": {"classe": "person", "combinacao": "todas", "condicoes": [{"especialista": COR, "atributo": "cor_uniforme", "igual": "roxo"}]}}}, "roxo"),
        "especialista sem ativo": ({**definicao_v2(), "vinculos": [{"especialista": "br.nao.existe"}]}, "br.nao.existe"),
        "id diferente do nome": (definicao_v2(id="outro-nome"), "difere do nome"),
        "versao inconsistente": (definicao_v2(versao="9"), "difere"),
        "contrato desconhecido": (definicao_v2(contrato=3), "contrato"),
    }
    for rotulo, (definicao, trecho) in ruins.items():
        r = importar(adm, definicao)
        assert r.status_code == 422, rotulo
        assert trecho in r.text, (rotulo, r.text[:300])
        assert contar_versoes(adm) == antes, rotulo
    assert importar(adm, None, bruto="{nao e json").status_code == 422
    assert importar(adm, None, bruto="[1, 2]").status_code == 422
    assert importar(adm, None, bruto=" " * (70 * 1024) + "{}").status_code == 422
    assert importar(adm, definicao_v2(), nome="Nome Invalido").status_code == 422
    assert contar_versoes(adm) == antes


def test_importar_exige_admin_e_csrf(adm) -> None:
    antes = contar_versoes(adm)
    assert adm.post("/painel/receitas/importar", {"nome": NOME, "definicao_json": json.dumps(definicao_v2())}, token=False).status_code == 403
    adm.client.post("/logout", data={"csrf_token": adm.token()})
    adm.login_comum()
    assert importar(adm, definicao_v2()).status_code == 403
    assert contar_versoes(adm) == antes


# ------------------------------------------------------------------------------------------------ edicao sem perda silenciosa

def test_formulario_recusa_editar_contrato2_e_nada_e_gravado(adm) -> None:
    importar(adm, definicao_v2())
    rid = adm.contar(Receita) and 1
    antes = contar_versoes(adm)
    r = adm.client.get(f"/painel/receitas/{rid}/editar")
    assert r.status_code == 303
    texto = _flash(adm)["texto"]
    assert "não preserva" in texto and "contrato" in texto
    r = adm.post(f"/painel/receitas/{rid}/versoes", dados_receita(nome=NOME))
    assert r.status_code == 422                                                 # o formulario volta com o motivo; nada e gravado
    assert contar_versoes(adm) == antes


def test_formulario_recusa_editar_receita_simples_com_campos_que_ele_apagaria(adm) -> None:
    d = {"vinculos": [{"especialista": BEBE, "orcamento_ms": 500, "parametros": {"limiar_presente": 0.5}}],
         "regra": {"combinacao": "todas", "condicoes": [{"especialista": BEBE, "atributo": "bebe_no_colo", "igual": "sim"}]}}
    assert importar(adm, d, nome="simples-com-parametro").status_code == 303
    with adm.database.session() as db:
        original = db.query(ReceitaVersao).one().definicao_json
    r = adm.client.get("/painel/receitas/1/editar")
    assert r.status_code == 303 and "parametros" in _flash(adm)["texto"]
    r = adm.post("/painel/receitas/1/versoes", dados_receita(nome="simples-com-parametro"))
    assert r.status_code == 422 and "parametros" in r.text
    with adm.database.session() as db:
        assert db.query(ReceitaVersao).count() == 1 and db.query(ReceitaVersao).one().definicao_json == original      # nada mudou


def test_receita_simples_do_formulario_continua_editavel_e_igual(adm) -> None:
    vid = adm.criar_receita(nome="simples-form")
    adm.client.get("/painel/receitas")
    visao = adm.contexto("receitas.html")["receitas"][0]
    assert visao["contrato"] == 1 and visao["editavel"] is True and visao["motivos_nao_editavel"] == []
    assert adm.client.get("/painel/receitas/1/editar").status_code == 200
    assert adm.post("/painel/receitas/1/versoes", dados_receita(nome="simples-form")).status_code == 303
    assert adm.contar(ReceitaVersao) == 2 and vid


# ------------------------------------------------------------------------------------------------ execucao em sombra pela Cena

def _quadro(*sujeitos):
    q = np.zeros((480, 640, 3), np.uint8)
    for x, y, bgr in sujeitos:
        q[y, x] = bgr
    return q


SIM_ALVO = (230, 1, 0)       # bebe sim + uniforme azul (e, portanto, tambem "ref")
SO_REF = (10, 1, 0)          # bebe nao + uniforme azul ("ref", nunca "alvo")


class Relogio:
    def __init__(self) -> None:
        self.t = 5000.0

    def __call__(self) -> float:
        return self.t


def _cena(cam, geracao, quadro_id, t, sujeitos):
    """sujeitos: [(trilha, x, y, bgr)]; caixa 80x200 com o pixel de comando no canto superior esquerdo."""
    dets = tuple(Deteccao(tr, "person", (x, y, x + 80, y + 200), 0.9) for tr, x, y, _ in sujeitos)
    return criar_cena(f"cam_{cam}", geracao, quadro_id, t, 640, 480, dets, _quadro(*[(x, y, bgr) for _, x, y, bgr in sujeitos]))


def _eventos(adm, cam):
    return [e for e in adm.servicos.sombra.eventos(cam, limite=200) if e.get("tipo") == "cena"]


def _preparar_v2(adm):
    cam = adm.criar_camera()
    assert importar(adm, definicao_v2()).status_code == 303
    vid = adm.ultima_versao_id()
    adm.associar_legado(cam, vid)
    adm.rodar_ia()
    _ligar(adm, cam)
    adm.servicos.sombra.reconcile()
    relogio = Relogio()
    adm.servicos.sombra._slots[cam].motor._relogio = relogio                       # tempo CONTROLADO (nada de sleeps)
    return cam, relogio


def _enviar(adm, cam, relogio, t, sujeitos, geracao=1, quadro_id=None):
    relogio.t = t
    adm.porta(cam).processar_cena(_cena(cam, geracao, quadro_id or int(t), t, sujeitos))
    assert adm.porta(cam).alvo.aguardar_ociosa(PRAZO)


def test_persistencia_relacao_e_encerramento_ponta_a_ponta_pelo_feed(adm) -> None:
    cam, rel = _preparar_v2(adm)
    for t in (5000.0, 5001.0, 5002.0):                                          # sozinho: sem referencia perto
        _enviar(adm, cam, rel, t, [(1, 50, 60, SIM_ALVO)])
    ev = _eventos(adm, cam)
    assert [e["veredito"] for e in reversed(ev)][:2] == ["candidato", "candidato"] and ev[0]["veredito"] != "ativo"      # 2 s < 3 s
    _enviar(adm, cam, rel, 5003.0, [(1, 50, 60, SIM_ALVO)])
    ult = _eventos(adm, cam)[0]
    assert ult["veredito"] == "ativo" and ult["eventos_regra"][0]["transicao"] == "iniciado" and ult["eventos_regra"][0]["positivo_s"] == 3.0
    assert ult["sujeitos"][0]["relacoes"] == {"sem_ref": "sim"} and ult["geracao"] == 1
    # uma referencia (so_ref) chega perto: evidencia NEGATIVA; sustentada por >= 2 s encerra como RESOLVIDO
    for t in (5004.0, 5005.0, 5006.0):
        _enviar(adm, cam, rel, t, [(1, 50, 60, SIM_ALVO), (2, 100, 60, SO_REF)])
    fim = [e for ev in _eventos(adm, cam) for e in ev["eventos_regra"] if e["transicao"] == "encerrado"]
    assert fim and fim[0]["motivo"] == "resolvido_evidencia_negativa"
    html = adm.client.get("/painel/sombra/feed").text
    assert "cena (quadro" in html and "resolvido_evidencia_negativa" in html
    assert all(e["alerta_real"] is False for e in _rastro(adm, cam))
    assert adm.contar_alertas_reais() == 0 if hasattr(adm, "contar_alertas_reais") else True


def _rastro(adm, cam) -> list[dict]:
    arq = adm.tmp / "rt" / "sombra" / f"camera-{cam}.jsonl"
    return [json.loads(x) for x in arq.read_text(encoding="utf-8").splitlines()]


def test_cena_vazia_e_perda_nao_resolvem_e_nao_completam_o_tempo(adm) -> None:
    cam, rel = _preparar_v2(adm)
    for t in (5000.0, 5001.0, 5002.0, 5003.0):
        _enviar(adm, cam, rel, t, [(1, 50, 60, SIM_ALVO)])
    assert _eventos(adm, cam)[0]["veredito"] == "ativo"
    for t in (5004.0, 5005.0, 5006.0):                                         # cena VAZIA: avanca o tempo, nao resolve
        _enviar(adm, cam, rel, t, [])
    ult = _eventos(adm, cam)[0]
    assert ult["veredito"] == "ativo" and ult["eventos_regra"][0]["sustentacao_perdida"] is True and ult["eventos_regra"][0]["idade_ultima_evidencia_s"] >= 3.0
    assert not [e for ev in _eventos(adm, cam) for e in ev["eventos_regra"] if e["transicao"] == "encerrado"]
    _enviar(adm, cam, rel, 5009.0, [])                                          # ausente alem de perda_max_s (5 s): encerra, mas NAO como resolvido
    motivos = [e["motivo"] for ev in _eventos(adm, cam) for e in ev["eventos_regra"] if e["transicao"] == "encerrado"]
    assert motivos and motivos[0] in ("sujeito_ausente_prolongado", "perda_de_observacao") and "resolvido_evidencia_negativa" not in motivos


def test_reinicio_de_sessao_descarta_estado_e_encerramento_de_sessao_e_reportado(adm) -> None:
    cam, rel = _preparar_v2(adm)
    for t in (5000.0, 5001.0, 5002.0, 5003.0):
        _enviar(adm, cam, rel, t, [(1, 50, 60, SIM_ALVO)])
    assert _eventos(adm, cam)[0]["veredito"] == "ativo"
    _enviar(adm, cam, rel, 5004.0, [(1, 50, 60, SIM_ALVO)], geracao=2, quadro_id=1)      # sessao reiniciada: mesma trilha, geracao nova
    ult = _eventos(adm, cam)[0]
    assert ult["geracao"] == 2 and ult["veredito"] == "candidato" and ult["eventos_regra"][0]["positivo_s"] == 0.0     # nada herdado
    assert any(e["motivo"] == "geracao_encerrada" for e in ult["eventos_regra"])                     # o ativo da geracao 1 foi DESCARTADO
    adm.porta(cam).encerrar_sessao(2)                                                                # sessao 2 termina
    assert adm.porta(cam).alvo.aguardar_ociosa(PRAZO)
    assert adm.servicos.sombra._slots[cam].motor.contadores["evento_encerrado_sessao_encerrada"] == 0   # candidato nao era evento ativo
    adm.porta(cam).encerrar_sessao(1)                                                                # sessao antiga terminando atrasada: ignorada
    assert adm.porta(cam).alvo.aguardar_ociosa(PRAZO)


def test_troca_de_receita_descarta_o_estado_e_desligar_para_tudo(adm) -> None:
    cam, rel = _preparar_v2(adm)
    for t in (5000.0, 5001.0):
        _enviar(adm, cam, rel, t, [(1, 50, 60, SIM_ALVO)])
    assert _eventos(adm, cam)[0]["eventos_regra"][0]["positivo_s"] == 1.0
    assert importar(adm, definicao_v2(eventos=[{**definicao_v2()["eventos"][0], "duracao_s": 9}])).status_code == 303   # versao 2 (config diferente)
    adm.associar_legado(cam, adm.ultima_versao_id())
    adm.servicos.sombra.reconcile()
    adm.servicos.sombra._slots[cam].motor._relogio = rel
    _enviar(adm, cam, rel, 5002.0, [(1, 50, 60, SIM_ALVO)])
    ult = _eventos(adm, cam)[0]
    assert ult["receita"].endswith("v2") and ult["eventos_regra"][0]["positivo_s"] == 0.0                 # estado novo, nada herdado
    _desligar(adm, cam)
    assert _feed(adm) == [] and adm.servicos.sombra.threads_vivas() == 0


def test_duas_cameras_isoladas_com_a_mesma_trilha(adm) -> None:
    cam, rel = _preparar_v2(adm)
    cam2 = adm.criar_camera(nome="Corredor", ip="10.0.0.6")
    adm.associar_legado(cam2, adm.ultima_versao_id())
    adm.rodar_ia()
    _ligar(adm, cam2)
    adm.servicos.sombra.reconcile()
    rel2 = Relogio()
    adm.servicos.sombra._slots[cam2].motor._relogio = rel2
    for t in (5000.0, 5001.0, 5002.0, 5003.0):
        _enviar(adm, cam, rel, t, [(1, 50, 60, SIM_ALVO)])
    _enviar(adm, cam2, rel2, 5003.0, [(1, 50, 60, SIM_ALVO)])                    # MESMA trilha 1, outra camera: comeca do zero
    assert _eventos(adm, cam)[0]["veredito"] == "ativo"
    assert _eventos(adm, cam2)[0]["veredito"] == "candidato" and _eventos(adm, cam2)[0]["eventos_regra"][0]["positivo_s"] == 0.0
