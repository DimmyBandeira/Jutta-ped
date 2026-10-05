"""Sombra por camera no painel: porta entregue ao iniciar a IA, ligar/desligar/reconfigurar a quente, isolamento dos alertas reais, ciclo de vida."""
from __future__ import annotations

import json
import threading
from pathlib import Path

from painel_receitas_helpers import BEBE, COR, adm, amb, dados_receita  # noqa: F401
from test_motor_sombra import _ativar_bebe_v2
from test_motor_sombra_runtime import ServicoFalso, det, quadro_com
from test_pediatria_service_runtime_reconnect import ScriptedCapture, make_bare_session

from src.jutta_ped.motor.adaptador import proxima_geracao
from src.jutta_ped.motor.despacho import DespachoSombra
from src.jutta_ped.painel.models import Entrega, EventoPediatria
from src.jutta_ped.painel.sombra_service import PortaSombra, ref_da_camera
from src.jutta_ped.service.runtime import SessionStartConfig


def _threads() -> list[str]:
    return [t.name for t in threading.enumerate() if t.name.startswith("motor-sombra") and t.is_alive()]


def _preparar(adm, ligar: bool = True) -> tuple[int, int]:
    """Camera + receita associada + IA rodando (+ sombra ligada e reconciliada). Devolve (camera_id, versao_id)."""
    cam, vid = adm.criar_camera(), adm.criar_receita()
    adm.associar_legado(cam, vid)
    adm.rodar_ia()
    if ligar:
        assert adm.post(f"/painel/cameras/{cam}/sombra", {"acao": "ligar"}).status_code == 303
        adm.servicos.sombra.reconcile()
    return cam, vid


def _flash(adm) -> dict:
    adm.client.get("/painel/receitas")
    return adm.contexto("receitas.html")["flash"]


# ------------------------------------------------------------------------------------------------ liga/desliga

def test_sombra_nasce_desligada_e_ligar_sem_receita_e_recusado(adm) -> None:
    cam = adm.criar_camera()
    assert adm.camera(cam).sombra_ativa is False
    r = adm.post(f"/painel/cameras/{cam}/sombra", {"acao": "ligar"})
    assert r.status_code == 303 and _flash(adm)["tipo"] == "erro"
    assert adm.camera(cam).sombra_ativa is False
    assert adm.post(f"/painel/cameras/{cam}/sombra", {"acao": "talvez"}).status_code == 303 and adm.camera(cam).sombra_ativa is False
    assert adm.post("/painel/cameras/999/sombra", {"acao": "desligar"}).status_code == 303 and _flash(adm)["tipo"] == "erro"


def test_ligar_exige_receita_ainda_valida(adm) -> None:
    cam, _ = _preparar(adm, ligar=False)
    _ativar_bebe_v2(adm.inst, adm.tmp, lambda m: m["atributos"].pop(0))
    adm.post(f"/painel/cameras/{cam}/sombra", {"acao": "ligar"})
    flash = _flash(adm)
    assert flash["tipo"] == "erro" and "inexistente" in flash["texto"] and adm.camera(cam).sombra_ativa is False


def test_ligar_e_desligar_a_sombra_nao_reinicia_a_analise(adm) -> None:
    cam, _ = _preparar(adm, ligar=False)
    assert len(adm.gerente.partidas) == 1
    porta = adm.porta(cam)
    assert isinstance(porta, PortaSombra) and porta.seguro_para_ao_vivo is True and porta.alvo is None
    adm.post(f"/painel/cameras/{cam}/sombra", {"acao": "ligar"})
    adm.servicos.sombra.reconcile()
    adm.rodar_ia()
    assert isinstance(porta.alvo, DespachoSombra) and adm.servicos.sombra.estado(cam)["estado"] == "ativa"
    adm.post(f"/painel/cameras/{cam}/sombra", {"acao": "desligar"})
    adm.servicos.sombra.reconcile()
    adm.rodar_ia()
    adm.post(f"/painel/cameras/{cam}/sombra", {"acao": "ligar"})
    adm.servicos.sombra.reconcile()
    adm.rodar_ia()
    assert len(adm.gerente.partidas) == 1                                   # enable_camera so foi chamado UMA vez
    assert adm.porta(cam) is porta and adm.gerente.partidas[0].motor_sombra is porta
    assert porta.ref == ref_da_camera(cam) == f"cam-{cam}"


def test_consultar_paginas_nao_mexe_na_analise_nem_na_sombra(adm) -> None:
    cam, _ = _preparar(adm)
    alvo = adm.porta(cam).alvo
    for rota in ("/painel/monitoramento", "/painel/receitas", "/painel/receitas-resumo", "/painel/sombra/feed", "/painel/indicadores", "/painel/processamento"):
        assert adm.client.get(rota).status_code == 200
    assert len(adm.gerente.partidas) == 1 and adm.porta(cam).alvo is alvo and _threads() == [f"motor-sombra-{cam}"]


def test_desligar_pela_rota_e_imediato_e_para_o_despacho(adm) -> None:
    cam, _ = _preparar(adm)
    alvo = adm.porta(cam).alvo
    assert alvo.viva
    adm.post(f"/painel/cameras/{cam}/sombra", {"acao": "desligar"})
    assert adm.porta(cam).alvo is None and not alvo.viva and alvo.parado           # sem esperar o laco periodico
    assert adm.servicos.sombra.estado(cam)["estado"] == "desligada" and _threads() == []


# ------------------------------------------------------------------------------------------------ execucao e feed

def test_quadros_pela_porta_geram_eventos_de_sombra_no_feed_e_no_rastro(adm) -> None:
    cam, vid = _preparar(adm)
    adm.alimentar(cam, quadro_id=1)
    adm.alimentar(cam, quadro_id=2)
    assert adm.client.get("/painel/sombra/feed").status_code == 200
    sombra = adm.contexto("partials/sombra_feed.html")["sombra"]
    assert [e["quadro_id"] for e in sombra["eventos"]] == [2, 1]                        # mais recente primeiro
    e = sombra["eventos"][0]
    assert e["camera_nome"] == "Recepção" and e["trilha_id"] == 5 and e["veredito"] == "sim" and e["receita"] == "uniforme-bebe v1"
    assert e["config_hash"].startswith("sha256:") and {"id": BEBE, "versao": "1.0.0"} in e["versoes"] and e["incompatibilidades"] == {}
    assert e["idade_max_ms"] is not None and e["quando"]
    c0 = e["condicoes"][0]
    assert set(c0) == {"especialista", "atributo", "igual", "resultado", "motivo", "idade_ms", "reutilizado", "falha_recente"} and c0["resultado"] == "sim"
    [linha] = sombra["cameras"]
    assert (linha["id"], linha["nome"], linha["estado"], linha["erro"]) == (cam, "Recepção", "ativa", None)
    assert linha["enfileirados"] == 2 and linha["processados"] == 2 and linha["descartados"] == 0
    # o rastro .jsonl proprio usa a referencia interna fixa
    rastro = adm.tmp / "rt" / "sombra" / f"camera-{cam}.jsonl"
    linhas = [json.loads(x) for x in rastro.read_text(encoding="utf-8").splitlines()]
    assert len(linhas) == 2 and {x["camera_ref"] for x in linhas} == {f"cam-{cam}"} and all(x["alerta_real"] is False and x["modo"] == "sombra" for x in linhas)
    # filtro por camera
    adm.client.get("/painel/sombra/feed?camera=999")
    assert adm.contexto("partials/sombra_feed.html")["sombra"] == {"eventos": [], "cameras": []}


def test_eventos_de_sombra_nao_chegam_aos_alertas_reais(adm) -> None:
    cam, _ = _preparar(adm)
    chamadas: list = []
    adm.servicos.eventos.on_runtime_event = lambda p: chamadas.append(p)
    adm.servicos.eventos.registrar = lambda p: chamadas.append(p)
    adm.servicos.eventos.ouvintes_pos_registro.append(lambda i: chamadas.append(i))
    for q in range(1, 6):
        adm.alimentar(cam, quadro_id=q)
    assert len(adm.servicos.sombra.eventos(cam)) == 5
    assert chamadas == [] and adm.contar(EventoPediatria) == 0 and adm.contar(Entrega) == 0
    assert adm.servicos.eventos.recentes(incluir_suprimidos=True) == [] and adm.servicos.eventos.pendentes() == []
    assert adm.client.get("/painel/alertas/historico?periodo=24h").json()["total"] == 0
    assert list(adm.tmp.rglob("events.jsonl")) == []                                   # nada no log operacional
    assert [p.name for p in (adm.tmp / "rt" / "sombra").iterdir()] == [f"camera-{cam}.jsonl"]


def test_porta_no_laco_real_de_uma_sessao_ao_vivo(adm, tmp_path: Path) -> None:
    cam, _ = _preparar(adm)
    porta = adm.porta(cam)
    s = make_bare_session(SessionStartConfig(source="rtsp://x/cam", report_dir=tmp_path / "sess", motor_sombra=porta, reconnect_enabled=False), tmp_path / "sess")
    s._analysis_fps = 0.0
    s._geracao_sombra = proxima_geracao()
    s.service = ServicoFalso([[det(1, 50, 60)]])
    from test_motor_sombra import SIM_AZUL
    s.capture = ScriptedCapture([(True, quadro_com(50, 60, SIM_AZUL)) for _ in range(3)] + [(False, None)])
    s._run_loop()
    assert porta.alvo.aguardar_ociosa(10)
    assert s.state.frames_processed == 3 and s.evidence_error_counts["motor_sombra"] == 0 and s.evidence_error_counts["motor_sombra_sincrono_recusado_ao_vivo"] == 0
    eventos = adm.servicos.sombra.eventos(cam)
    assert eventos and {e["veredito"] for e in eventos} == {"sim"} and s.state.popup_count == 0


def test_sem_alvo_a_porta_e_um_no_op_barato(adm) -> None:
    cam, _ = _preparar(adm, ligar=False)
    porta = adm.porta(cam)
    assert porta.processar("qualquer", 1, None, ["lixo"]) == [] and porta.recebidos == 0       # nem toca nas observacoes


# ------------------------------------------------------------------------------------------------ estados

def test_sem_ia_rodando_a_sombra_fica_aguardando_e_nao_e_erro(adm) -> None:
    cam, vid = adm.criar_camera(), adm.criar_receita()
    adm.associar_legado(cam, vid)
    adm.post(f"/painel/cameras/{cam}/sombra", {"acao": "ligar"})
    adm.client.get("/painel/sombra/feed")
    assert adm.contexto("partials/sombra_feed.html")["sombra"]["cameras"][0]["estado"] == "aguardando"     # antes de reconciliar
    adm.servicos.sombra.reconcile()
    assert adm.servicos.sombra.estado(cam) == {"estado": "aguardando", "erro": None}
    adm.rodar_ia()
    assert adm.servicos.sombra.estado(cam) == {"estado": "ativa", "erro": None}
    adm.client.get("/painel/monitoramento")
    [c] = adm.contexto("monitoramento.html")["cameras"]
    assert (c.sombra_ativa, c.sombra_estado, c.sombra_erro, c.receita_label) == (True, "ativa", None, "uniforme-bebe v1")
    assert adm.contexto("monitoramento.html")["cameras_opcoes"] == [{"id": cam, "nome": "Recepção"}]


def test_receita_que_deixa_de_valer_vira_erro_e_volta_sozinha(adm) -> None:
    cam, _ = _preparar(adm)
    antigo = adm.porta(cam).alvo
    _ativar_bebe_v2(adm.inst, adm.tmp, lambda m: m["atributos"].pop(0))
    adm.servicos.sombra.reconcile()
    est = adm.servicos.sombra.estado(cam)
    assert est["estado"] == "erro" and "inexistente" in est["erro"] and adm.porta(cam).alvo is None and not antigo.viva
    adm.client.get("/painel/monitoramento")
    assert adm.contexto("monitoramento.html")["cameras"][0].sombra_estado == "erro"
    adm.inst.reverter(BEBE)
    adm.servicos.sombra.reconcile()
    assert adm.servicos.sombra.estado(cam)["estado"] == "ativa" and adm.porta(cam).alvo is not None and _threads() == [f"motor-sombra-{cam}"]


def test_falha_ao_carregar_o_modelo_vira_erro_legivel_sem_derrubar_nada(adm) -> None:
    cam, vid = adm.criar_camera(), adm.criar_receita()
    adm.associar_legado(cam, vid)
    adm.rodar_ia()
    adm.backend.falha_em = {"cpu", "cuda:0"}                                             # nenhum dispositivo carrega
    adm.post(f"/painel/cameras/{cam}/sombra", {"acao": "ligar"})
    adm.servicos.sombra.reconcile()
    est = adm.servicos.sombra.estado(cam)
    assert est["estado"] == "erro" and est["erro"] and adm.porta(cam).alvo is None and len(adm.gerente.partidas) == 1 and _threads() == []


# ------------------------------------------------------------------------------------------------ ciclo de vida

def test_reconfigurar_troca_o_alvo_sem_acumular_threads_nem_reiniciar_a_ia(adm) -> None:
    cam, v1 = _preparar(adm)
    porta, alvo1 = adm.porta(cam), adm.porta(cam).alvo
    from src.jutta_ped.painel.models import ReceitaVersao
    with adm.database.session() as db:
        receita_id = db.get(ReceitaVersao, v1).receita_id
    assert adm.post(f"/painel/receitas/{receita_id}/versoes", dados_receita(cond_igual=["sim", "verde", "", ""])).status_code == 303
    v2 = adm.ultima_versao_id()
    adm.servicos.sombra.reconcile()
    assert adm.porta(cam).alvo is alvo1                                                  # criar versao nova nao troca nada sozinho
    adm.associar_legado(cam, v2)
    adm.servicos.sombra.reconcile()
    alvo2 = porta.alvo
    assert alvo2 is not alvo1 and not alvo1.viva and alvo2.viva and _threads() == [f"motor-sombra-{cam}"]
    adm.servicos.sombra.reconcile()                                                      # reconciliar de novo nao recria
    assert porta.alvo is alvo2
    for _ in range(5):                                                                   # ligar/desligar repetido
        adm.post(f"/painel/cameras/{cam}/sombra", {"acao": "desligar"})
        adm.post(f"/painel/cameras/{cam}/sombra", {"acao": "ligar"})
        adm.servicos.sombra.reconcile()
    assert len(_threads()) == 1 and len(adm.gerente.partidas) == 1 and adm.porta(cam) is porta
    adm.alimentar(cam, quadro_id=9)
    assert adm.servicos.sombra.eventos(cam)[0]["receita"] == "uniforme-bebe v2"


def test_remover_a_camera_desmonta_a_sombra(adm) -> None:
    cam, _ = _preparar(adm)
    assert _threads()
    from painel_helpers import _hx
    assert _hx(adm.client, "DELETE", f"/painel/cameras/{cam}").status_code == 200
    adm.servicos.sombra.reconcile()
    assert _threads() == [] and adm.servicos.sombra.estado(cam)["estado"] == "desligada"


def test_encerrar_o_servico_para_todos_os_despachos(adm) -> None:
    cam, _ = _preparar(adm)
    cam2 = adm.criar_camera(nome="Corredor", ip="10.0.0.6")
    vid = adm.ultima_versao_id()
    adm.associar_legado(cam2, vid)
    adm.rodar_ia()
    adm.post(f"/painel/cameras/{cam2}/sombra", {"acao": "ligar"})
    adm.servicos.sombra.reconcile()
    assert len(_threads()) == 2
    porta = adm.porta(cam)
    adm.servicos.parar()
    assert _threads() == [] and porta.alvo is None
    adm.servicos.parar()                                                                 # idempotente
    adm.servicos.sombra.reconcile()                                                      # depois de parar nao remonta
    assert _threads() == []


def test_sessao_reiniciada_reaproveita_a_mesma_porta(adm) -> None:
    cam, _ = _preparar(adm)
    porta = adm.porta(cam)
    adm.servicos.ia.reiniciar(cam)
    adm.rodar_ia()
    assert len(adm.gerente.partidas) == 2 and adm.porta(cam) is porta and porta.alvo is not None and adm.servicos.sombra.estado(cam)["estado"] == "ativa"
    adm.alimentar(cam, quadro_id=1, geracao=1)
    adm.alimentar(cam, quadro_id=2, geracao=2)                                           # nova sessao = nova geracao: segue funcionando
    evs = adm.servicos.sombra.eventos(cam)
    assert [e["geracao"] for e in evs] == [2]                                            # o feed mostra so a sessao vigente; a anterior vai ao rastro


# ------------------------------------------------------------------------------------------------ processamento

def test_processamento_mostra_metricas_reais_e_nos_remotos_nao_implementados(adm) -> None:
    cam, _ = _preparar(adm)
    adm.alimentar(cam)
    assert adm.client.get("/painel/processamento").status_code == 200
    p = adm.contexto("processamento.html")["proc"]
    assert p["servico"]["ia_disponivel"] is True and p["servico"]["max_cameras_ia"] == 4 and p["servico"]["iniciado_em"]
    assert p["go2rtc"]["api_ok"] is True and p["go2rtc"]["sincronizadas"] == 1 and p["go2rtc"]["streams"] >= 1
    [s] = p["sessoes"]
    assert s["camera"] == "Recepção" and s["estado"] == "ANALISANDO" and set(s) == {"camera", "estado", "frames", "popups", "dispositivo", "ultimo_erro"}
    assert {e["id"] for e in p["especialistas"]} == {BEBE, COR} and all(len(e["identidade_curta"]) <= 12 and e["versao"] == "1.0.0" for e in p["especialistas"])
    assert p["sombra"]["ativas"] == 1 and p["sombra"]["cameras"][0]["processados"] == 1
    assert p["nos_remotos"]["implementado"] is False and p["nos_remotos"]["texto"]


def test_processamento_sem_gerente_de_ia_e_com_go2rtc_fora(tmp_path: Path, monkeypatch) -> None:
    from painel_receitas_helpers import montar
    a = montar(tmp_path, monkeypatch, com_ia=False)
    try:
        a.login_admin()
        assert a.client.get("/painel/processamento").status_code == 200
        p = a.contexto("processamento.html")["proc"]
        assert p["servico"]["ia_disponivel"] is False and p["sessoes"] == [] and p["especialistas"] == [] and p["nos_remotos"]["implementado"] is False
    finally:
        a._fechar()
