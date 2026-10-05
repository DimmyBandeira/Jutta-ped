"""Corridas da sombra, reproduzidas com barreiras DETERMINISTICAS (sem sleeps como sincronizacao):
1) reconciliacao com snapshot antigo nao religa o que o admin desligou; 2) resultado de montagem aposentada nao volta ao feed.
Tambem: semantica de 'stream sincronizado' (nao e video chegando)."""
from __future__ import annotations

import json
import threading
import time

import pytest
from painel_helpers import _hx
from painel_receitas_helpers import BEBE, COR, adm, amb  # noqa: F401
from test_especialistas_executor import ModeloFalso
from test_painel_sombra import _flash, _preparar, _threads  # noqa: F401

PRAZO = 15


def _feed(adm) -> list:
    adm.limpar_contextos()
    assert adm.client.get("/painel/sombra/feed").status_code == 200
    return adm.contexto("partials/sombra_feed.html")["sombra"]["eventos"]


def _ligar(adm, cam) -> None:
    assert adm.post(f"/painel/cameras/{cam}/sombra", {"acao": "ligar"}).status_code == 303


def _desligar(adm, cam) -> None:
    assert adm.post(f"/painel/cameras/{cam}/sombra", {"acao": "desligar"}).status_code == 303


def _interceptar_uma_vez(monkeypatch, objeto, nome):
    """Substitui `objeto.nome`: a PRIMEIRA chamada avisa (`chegou`) e espera `solta`; as demais passam direto."""
    original = getattr(objeto, nome)
    chegou, solta, estado = threading.Event(), threading.Event(), {"usado": False}

    def interceptado(*a, **k):
        if not estado["usado"]:
            estado["usado"] = True
            chegou.set()
            assert solta.wait(PRAZO), "barreira nao liberada"
        return original(*a, **k)

    monkeypatch.setattr(objeto, nome, interceptado)
    return chegou, solta


def _em_thread(fn) -> threading.Thread:
    th = threading.Thread(target=fn, daemon=True)
    th.start()
    return th


# ------------------------------------------------------------------------------------------------ achado 1

def test_snapshot_antigo_nao_religa_sombra_desligada_antes_de_reconciliar_camera(adm, monkeypatch) -> None:
    cam, _ = _preparar(adm, ligar=False)
    _ligar(adm, cam)                                             # banco: ligada, ainda nao reconciliada
    g = adm.servicos.sombra
    chegou, solta = _interceptar_uma_vez(monkeypatch, g, "_reconciliar_camera")
    th = _em_thread(g.reconcile)
    assert chegou.wait(PRAZO)                                    # snapshot ja lido ("ligada"); parado antes de publicar
    _desligar(adm, cam)                                          # commit + desligar_agora + acordar
    assert adm.camera(cam).sombra_ativa is False
    solta.set()
    th.join(PRAZO)
    assert not th.is_alive()
    assert adm.camera(cam).sombra_ativa is False and g.estado(cam)["estado"] == "desligada"
    assert adm.porta(cam).alvo is None and g.threads_vivas() == 0 and _threads() == []


def test_mutacao_durante_a_carga_do_modelo_descarta_a_montagem(adm, monkeypatch) -> None:
    cam, _ = _preparar(adm, ligar=False)
    _ligar(adm, cam)
    g = adm.servicos.sombra
    chegou, solta = _interceptar_uma_vez(monkeypatch, g.executor, "contrato")      # `_montar` carrega o modelo por aqui
    th = _em_thread(g.reconcile)
    assert chegou.wait(PRAZO)
    _desligar(adm, cam)
    solta.set()
    th.join(PRAZO)
    assert not th.is_alive()
    assert g.estado(cam)["estado"] == "desligada" and adm.porta(cam).alvo is None and g.threads_vivas() == 0


def test_troca_de_receita_durante_a_reconciliacao_usa_a_ultima_decisao(adm, monkeypatch) -> None:
    cam, v1 = _preparar(adm, ligar=False)
    _ligar(adm, cam)
    v2 = adm.criar_receita(nome="receita-b")
    g = adm.servicos.sombra
    chegou, solta = _interceptar_uma_vez(monkeypatch, g, "_reconciliar_camera")
    th = _em_thread(g.reconcile)
    assert chegou.wait(PRAZO)
    adm.associar_legado(cam, v2)
    solta.set()
    th.join(PRAZO)
    assert not th.is_alive()
    ev = adm.alimentar(cam) or _feed(adm)
    assert g.estado(cam)["estado"] == "ativa"
    montado = adm.servicos.sombra._slots[cam].motor
    assert montado is not None and list(montado._receitas) == [("receita-b", "1")]            # a ultima decisao prevaleceu
    assert ev == [] or all(e["receita"].startswith("receita-b") for e in ev)


def test_remocao_da_camera_durante_a_reconciliacao_nao_monta_nada(adm, monkeypatch) -> None:
    cam, _ = _preparar(adm, ligar=False)
    _ligar(adm, cam)
    g = adm.servicos.sombra
    chegou, solta = _interceptar_uma_vez(monkeypatch, g, "_reconciliar_camera")
    th = _em_thread(g.reconcile)
    assert chegou.wait(PRAZO)
    assert _hx(adm.client, "DELETE", f"/painel/cameras/{cam}").status_code == 200
    solta.set()
    th.join(PRAZO)
    assert not th.is_alive()
    assert g.threads_vivas() == 0 and _threads() == [] and g.estado(cam)["estado"] == "desligada"


# ------------------------------------------------------------------------------------------------ achado 2

def _bloquear_inferencia(monkeypatch):
    """A primeira inferencia espera `solta` (simula trabalho em andamento); `dentro` avisa que ele comecou."""
    original = ModeloFalso.inferir
    dentro, solta, estado = threading.Event(), threading.Event(), {"usado": False}

    def lento(self, imagens):
        if not estado["usado"]:
            estado["usado"] = True
            dentro.set()
            assert solta.wait(PRAZO)
        return original(self, imagens)

    monkeypatch.setattr(ModeloFalso, "inferir", lento)
    return dentro, solta


def _enviar(adm, cam, quadro_id=1):
    from painel_receitas_helpers import SIM_AZUL, obs, quadro_com
    p = adm.porta(cam)
    o = obs(5, 50, 60, cam=f"cam_{cam}", ger=1, quadro_id=quadro_id, t=time.monotonic())
    p.processar(f"cam_{cam}", 1, quadro_com(50, 60, SIM_AZUL), [o])


def test_resultado_em_andamento_nao_reaparece_no_feed_depois_de_desligar(adm, monkeypatch) -> None:
    dentro, solta = _bloquear_inferencia(monkeypatch)
    cam, _ = _preparar(adm)
    g = adm.servicos.sombra
    _enviar(adm, cam)
    assert dentro.wait(PRAZO)                                    # trabalho de sombra em andamento
    _desligar(adm, cam)
    assert _feed(adm) == [] and g.estado(cam)["estado"] == "desligada"
    solta.set()                                                  # o trabalho antigo termina DEPOIS da limpeza
    g._juntar_aposentados(PRAZO)
    assert _feed(adm) == [] and g.eventos(cam) == []
    assert g._slots[cam].tardios == 1 and g.threads_vivas() == 0
    rastro = adm.tmp / "rt" / "sombra" / f"camera-{cam}.jsonl"
    linhas = [json.loads(x) for x in rastro.read_text(encoding="utf-8").splitlines()]
    assert len(linhas) == 1 and linhas[0]["aposentado"] is True and linhas[0]["alerta_real"] is False   # historico classificado, nao feed


def test_resultado_tardio_da_receita_antiga_nao_entra_entre_os_da_nova(adm, monkeypatch) -> None:
    dentro, solta = _bloquear_inferencia(monkeypatch)
    cam, _ = _preparar(adm)
    g = adm.servicos.sombra
    _enviar(adm, cam, quadro_id=1)                               # receita antiga: em andamento
    assert dentro.wait(PRAZO)
    v2 = adm.criar_receita(nome="receita-nova")
    adm.associar_legado(cam, v2)
    g.reconcile()                                                # monta a receita nova; a antiga e aposentada com trabalho pendente
    assert g.estado(cam)["estado"] == "ativa"
    solta.set()
    g._juntar_aposentados(PRAZO)
    adm.alimentar(cam, quadro_id=2)                              # receita nova
    evs = _feed(adm)
    assert evs and all(e["receita"].startswith("receita-nova") for e in evs), evs
    assert g._slots[cam].tardios == 1


def _enviar_geracao(adm, cam, geracao, quadro_id):
    from painel_receitas_helpers import SIM_AZUL, obs, quadro_com
    p = adm.porta(cam)
    o = obs(5, 50, 60, cam=f"cam_{cam}", ger=geracao, quadro_id=quadro_id, t=time.monotonic())
    p.processar(f"cam_{cam}", geracao, quadro_com(50, 60, SIM_AZUL), [o])


def _rastro(adm, cam) -> list[dict]:
    arq = adm.tmp / "rt" / "sombra" / f"camera-{cam}.jsonl"
    return [json.loads(x) for x in arq.read_text(encoding="utf-8").splitlines()]


def test_geracao_antiga_em_execucao_nao_publica_no_feed_da_geracao_nova(adm, monkeypatch) -> None:
    """Reproducao do Codex: inferencia da geracao 1 em andamento; a geracao 2 entra pela MESMA porta; a 1 termina depois."""
    dentro, solta = _bloquear_inferencia(monkeypatch)
    cam, _ = _preparar(adm)
    g = adm.servicos.sombra
    porta = adm.porta(cam)
    _enviar_geracao(adm, cam, 1, quadro_id=1)
    assert dentro.wait(PRAZO)                                    # geracao 1 EXECUTANDO
    _enviar_geracao(adm, cam, 2, quadro_id=1)                    # sessao reiniciou: geracao 2 aceita pela mesma porta
    assert porta.geracao_vigente == 2
    solta.set()                                                  # a geracao 1 termina depois de a 2 ser aceita
    assert porta.alvo.aguardar_ociosa(PRAZO)
    feed = _feed(adm)
    assert feed, "a geracao nova precisa aparecer"
    assert all("geracao" in e and e["geracao"] == 2 for e in feed), feed       # sem fallback: a geracao tem que estar no evento
    linhas = _rastro(adm, cam)
    por_geracao = {l["geracao"]: l for l in linhas}
    assert set(por_geracao) == {1, 2} and len(linhas) == 2
    assert por_geracao[1]["aposentado"] is True and por_geracao[1]["motivo_aposentado"] == "geracao_obsoleta"
    assert "aposentado" not in por_geracao[2] and por_geracao[2]["alerta_real"] is False
    assert g._slots[cam].tardios == 1


def test_nova_geracao_tira_do_feed_os_eventos_da_anterior_e_quadro_antigo_e_recusado(adm) -> None:
    cam, _ = _preparar(adm)
    porta = adm.porta(cam)
    _enviar_geracao(adm, cam, 1, 1)
    assert porta.alvo.aguardar_ociosa(PRAZO) and [e["geracao"] for e in _feed(adm)] == [1]
    _enviar_geracao(adm, cam, 2, 1)
    assert porta.alvo.aguardar_ociosa(PRAZO) and [e["geracao"] for e in _feed(adm)] == [2]         # a anterior vira historico (rastro)
    _enviar_geracao(adm, cam, 1, 99)                                                                 # quadro atrasado da sessao 1
    assert porta.recusados_geracao == 1 and [e["geracao"] for e in _feed(adm)] == [2]
    assert [l["geracao"] for l in _rastro(adm, cam)] == [1, 2]


def test_desligar_e_trocar_receita_continuam_funcionando_com_geracoes(adm, monkeypatch) -> None:
    cam, _ = _preparar(adm)
    _enviar_geracao(adm, cam, 1, 1)
    assert adm.porta(cam).alvo.aguardar_ociosa(PRAZO) and len(_feed(adm)) == 1
    _desligar(adm, cam)
    assert _feed(adm) == [] and adm.servicos.sombra.threads_vivas() == 0


# ------------------------------------------------------------------------------------------------ achado 3

def test_streams_sincronizados_nao_afirmam_video_e_expõem_o_estado_do_go2rtc(adm) -> None:
    cam, _ = _preparar(adm, ligar=False)
    srv = adm.servicos
    adm.limpar_contextos()
    assert adm.client.get("/painel/indicadores").status_code == 200
    ind = adm.contexto("partials/indicadores.html")["ind"]
    assert "cameras_conectadas" not in ind and ind["streams_sincronizados"] == 1
    assert ind["go2rtc"] == "desconhecido" and ind["go2rtc_verificado_ha_s"] is None          # nunca sondado: nao inventa "ok"
    srv._ciclo_go2rtc = srv._ciclo_go2rtc                                                       # (laco real roda a sondagem)
    srv.go2rtc.garantir = lambda: "ok"
    srv._ciclo_go2rtc()
    assert srv.go2rtc_estado()[0] == "ok"
    adm.go2rtc.online = False                                                                    # API sai do ar; a configuracao segue "ok"
    srv._ciclo_go2rtc()
    assert srv.go2rtc_estado()[0] == "inacessivel"
    adm.limpar_contextos()
    adm.client.get("/painel/indicadores")
    ind = adm.contexto("partials/indicadores.html")["ind"]
    assert ind["go2rtc"] == "inacessivel" and ind["streams_sincronizados"] == 1                  # contagem e configuracao; estado e dito a parte
    adm.limpar_contextos()
    html = adm.client.get("/painel/indicadores").text
    assert "go2rtc inacessível" in html and "disponível" not in html.lower().replace("indisponível", "")


def test_sondagem_antiga_vira_desatualizada_sem_sondar_na_renderizacao(adm) -> None:
    srv = adm.servicos
    relogio = {"t": 1000.0}
    srv.relogio = lambda: relogio["t"]
    srv.go2rtc.garantir = lambda: "ok"
    srv._ciclo_go2rtc()
    assert srv.go2rtc_estado() == ("ok", 0.0)
    chamadas = {"n": 0}
    original = srv.sync.sondar
    srv.sync.sondar = lambda: (chamadas.__setitem__("n", chamadas["n"] + 1), original())[1]
    relogio["t"] += srv.intervalo_go2rtc * 3 + 1
    assert srv.go2rtc_estado()[0] == "desatualizado"
    adm.client.get("/painel/indicadores")
    adm.client.get("/painel/monitoramento")
    assert chamadas["n"] == 0                                                                    # renderizar nao sonda


def test_selo_do_card_diz_stream_configurado_e_go2rtc_inacessivel(adm) -> None:
    _preparar(adm, ligar=False)
    srv = adm.servicos
    srv.go2rtc.garantir = lambda: "ok"
    srv._ciclo_go2rtc()
    html = adm.client.get("/painel/monitoramento").text
    assert "stream configurado" in html and "stream disponível" not in html
    adm.go2rtc.online = False
    srv._ciclo_go2rtc()
    assert "go2rtc inacessível" in adm.client.get("/painel/monitoramento").text
    _ = pytest, BEBE, COR
