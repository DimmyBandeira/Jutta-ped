"""Diagnostico do mestre e montagem da sombra com a config da camera (05f): liga/desliga independente, JPEG, isolamento entre cameras,
salvar sem reiniciar a IA e sem publicacao tardia."""
from __future__ import annotations

import time

import cv2
import numpy as np
from painel_receitas_helpers import BEBE, COR, adm, amb  # noqa: F401
from test_motor_sombra import SIM_AZUL, obs
from test_motor_sombra_runtime import quadro_com
from test_painel_camera_config import so_bebe, so_cor, salvar

from src.jutta_ped.motor.cena import Deteccao, criar_cena

CHAVES_DIAG = {"ativo", "recebido_ha_s", "quadro_id", "geracao", "largura", "altura", "roi", "ajustes", "sujeitos", "contadores", "estado"}


def cena(cam: int, quadro_id: int = 1, geracao: int = 1):
    q = np.full((480, 640, 3), 90, np.uint8)
    dets = [Deteccao(5, "person", (100.0, 100.0, 220.0, 380.0), 0.9), Deteccao(6, "person", (400.0, 50.0, 480.0, 200.0), 0.8)]
    return criar_cena(f"cam_{cam}", geracao, quadro_id, time.monotonic(), 640, 480, dets, q)


def diag_ctx(adm, cam: int) -> dict:
    adm.limpar_contextos()
    assert adm.client.get(f"/painel/cameras/{cam}/diagnostico").status_code == 200
    return adm.contexto("cameras/diagnostico.html")


def ligar(adm, cam: int, acao: str = "ligar"):
    return adm.post(f"/painel/cameras/{cam}/diagnostico", {"acao": acao})


# ------------------------------------------------------------------------------------------------ diagnostico

def test_diagnostico_desligado_tem_dict_valido_e_jpeg_404(adm) -> None:
    cam = adm.criar_camera()
    ctx = diag_ctx(adm, cam)
    assert set(ctx["diag"]) == CHAVES_DIAG and ctx["diag"]["estado"] == "desligado" and ctx["diag"]["contadores"] == {"cenas": 0, "vazias": 0, "recusadas": 0}
    assert ctx["cam"] == {"id": cam, "nome": "Recepção"} and ctx["efetivo"] is None
    assert adm.client.get(f"/painel/cameras/{cam}/diagnostico/quadro.jpg").status_code == 404
    assert adm.client.get(f"/painel/cameras/{cam}/diagnostico/recorte/5.jpg").status_code == 404
    assert adm.servicos.sombra.diagnostico(cam) is None


def test_liga_sem_sombra_e_sem_receita_recebe_cenas_e_serve_jpeg(adm) -> None:
    cam = adm.criar_camera()
    adm.rodar_ia()
    porta = adm.porta(cam)
    assert porta.ativa is False
    assert ligar(adm, cam).status_code == 303 and "camera_diagnostico_ligado" in [a for a in _acoes(adm)]
    c = adm.camera(cam)
    assert c.diagnostico_ativo is True and c.sombra_ativa is False and c.receita_versao_id is None
    d = adm.servicos.sombra.diagnostico(cam)
    assert d is not None and porta.diagnostico is d and porta.ativa is True and porta.alvo is None
    assert len(adm.gerente.partidas) == 1                                    # nao reiniciou a IA
    assert diag_ctx(adm, cam)["diag"]["estado"] == "aguardando"            # ligado, ainda sem cena
    porta.processar_cena(cena(cam))
    ctx = diag_ctx(adm, cam)
    assert set(ctx["diag"]) == CHAVES_DIAG and ctx["diag"]["estado"] == "ativo" and ctx["diag"]["contadores"]["cenas"] == 1
    assert {s["trilha_id"] for s in ctx["diag"]["sujeitos"]} == {5, 6}
    r = adm.client.get(f"/painel/cameras/{cam}/diagnostico/quadro.jpg")
    assert r.status_code == 200 and r.headers["content-type"] == "image/jpeg" and r.headers["cache-control"] == "no-store"
    assert cv2.imdecode(np.frombuffer(r.content, np.uint8), cv2.IMREAD_COLOR).shape[:2] == (480, 640)
    r = adm.client.get(f"/painel/cameras/{cam}/diagnostico/recorte/5.jpg")
    assert r.status_code == 200 and cv2.imdecode(np.frombuffer(r.content, np.uint8), cv2.IMREAD_COLOR) is not None
    assert r.headers["cache-control"] == "no-store"
    assert adm.client.get(f"/painel/cameras/{cam}/diagnostico/recorte/99.jpg").status_code == 404
    assert adm.client.get(f"/painel/cameras/{cam}/diagnostico/recorte/abc.jpg").status_code == 404
    assert adm.client.get(f"/painel/cameras/{cam}/diagnostico/recorte/-1.jpg").status_code == 404
    adm.limpar_contextos()
    assert adm.client.get(f"/painel/cameras/{cam}/diagnostico/resumo").status_code == 200
    assert adm.contexto("partials/diagnostico_mestre.html")["diag"]["estado"] == "ativo"


def test_ligado_sem_ia_rodando_fica_aguardando_e_sem_objeto_o_dict_e_valido(adm) -> None:
    cam = adm.criar_camera()
    ligar(adm, cam)
    d = diag_ctx(adm, cam)["diag"]
    assert d["estado"] == "aguardando" and set(d) == CHAVES_DIAG and d["sujeitos"] == [] and d["ajustes"]["crop"]["margem_h"] == 0.0


def test_desligar_limpa_a_memoria_e_desacopla_da_porta(adm) -> None:
    cam = adm.criar_camera()
    adm.rodar_ia()
    ligar(adm, cam)
    porta, d = adm.porta(cam), adm.servicos.sombra.diagnostico(cam)
    porta.processar_cena(cena(cam))
    assert d.resumo()["ativo"] is True
    assert ligar(adm, cam, "desligar").status_code == 303 and "camera_diagnostico_desligado" in _acoes(adm)
    assert adm.servicos.sombra.diagnostico(cam) is None and porta.diagnostico is None and porta.ativa is False
    assert d.resumo()["ativo"] is False and d.resumo()["contadores"]["cenas"] == 0 and d.jpeg_quadro() is None
    assert adm.client.get(f"/painel/cameras/{cam}/diagnostico/quadro.jpg").status_code == 404
    assert diag_ctx(adm, cam)["diag"]["estado"] == "desligado"
    assert ligar(adm, cam, "talvez").status_code == 303 and adm.camera(cam).diagnostico_ativo is False


def test_diagnostico_independe_da_sombra_com_receita(adm) -> None:
    cam, vid = adm.criar_camera(), so_bebe(adm)
    salvar(adm, cam, especialista=BEBE, receita_versao_id=vid)
    adm.rodar_ia()
    adm.post(f"/painel/cameras/{cam}/sombra", {"acao": "ligar"})
    adm.servicos.sombra.reconcile()
    ligar(adm, cam)
    porta = adm.porta(cam)
    assert porta.alvo is not None and porta.diagnostico is not None
    adm.post(f"/painel/cameras/{cam}/sombra", {"acao": "desligar"})
    adm.servicos.sombra.reconcile()
    assert porta.alvo is None and porta.diagnostico is not None and adm.camera(cam).diagnostico_ativo is True
    ligar(adm, cam, "desligar")
    adm.post(f"/painel/cameras/{cam}/sombra", {"acao": "ligar"})
    adm.servicos.sombra.reconcile()
    assert porta.alvo is not None and porta.diagnostico is None


def test_diagnostico_usa_os_ajustes_efetivos_e_zera_ao_mudar_a_config(adm) -> None:
    cam = adm.criar_camera()
    adm.rodar_ia()
    salvar(adm, cam, roi_ativa="1", roi_x1=0, roi_y1=0, roi_x2=50, roi_y2=100, crop_margem_h=10, crop_margem_v=10)
    ligar(adm, cam)
    porta = adm.porta(cam)
    assert porta.ajustes.roi.x2 == 0.5
    porta.processar_cena(cena(cam))
    s = {x["trilha_id"]: x for x in diag_ctx(adm, cam)["diag"]["sujeitos"]}
    assert s[5]["na_roi"] is True and s[6]["na_roi"] is False and s[6]["motivo_sem_recorte"] == "fora_da_roi" and s[5]["recorte"]["folgas_px"]["esq"] == 12
    assert diag_ctx(adm, cam)["diag"]["roi"]["px"] == [0.0, 0.0, 320.0, 480.0]
    salvar(adm, cam, crop_margem_h=20)                                       # nova config: memoria antiga nao vale
    d = diag_ctx(adm, cam)["diag"]
    assert d["contadores"]["cenas"] == 0 and d["roi"] is None and d["ajustes"]["crop"]["margem_h"] == 0.2
    assert len(adm.gerente.partidas) == 1 and adm.porta(cam) is porta        # salvar nao reiniciou a IA


# ------------------------------------------------------------------------------------------------ sombra e config

def _montar(adm, cam: int, vid: int, **ajustes) -> None:
    salvar(adm, cam, especialista=ajustes.pop("esp", BEBE), receita_versao_id=vid, **ajustes)
    adm.rodar_ia()
    adm.post(f"/painel/cameras/{cam}/sombra", {"acao": "ligar"})
    adm.servicos.sombra.reconcile()


def test_salvar_nao_reinicia_a_ia_remonta_a_sombra_e_nao_publica_tardio(adm) -> None:
    cam, vid = adm.criar_camera(), so_bebe(adm)
    _montar(adm, cam, vid, exec_orcamento_ms=500)
    g, porta = adm.servicos.sombra, adm.porta(cam)
    adm.alimentar(cam, quadro_id=1)
    [e1] = g.eventos(cam)
    antigo, motor_antigo = porta.alvo, g._slots[cam].motor
    assert salvar(adm, cam, especialista=BEBE, receita_versao_id=vid, exec_orcamento_ms=300, crop_margem_h=20).status_code == 303
    g.reconcile()
    assert porta.alvo is not antigo and not antigo.viva and g._slots[cam].motor is not motor_antigo
    assert len(adm.gerente.partidas) == 1 and adm.porta(cam) is porta
    assert g._slots[cam].motor._receitas[("so-bebe", "1")]["vinculos"][0]["orcamento_ms"] == 300
    assert g._slots[cam].motor._ajustes[porta.ref].crop.margem_h == 0.2
    n = len(g.eventos(cam))
    motor_antigo.processar(porta.ref, 1, quadro_com(50, 60, SIM_AZUL), [obs(5, 50, 60, cam=porta.ref, quadro_id=2, t=time.monotonic())])
    assert len(g.eventos(cam)) == n and g._slots[cam].tardios >= 1          # resultado da config antiga nao entra no feed
    adm.alimentar(cam, quadro_id=3)
    novo = g.eventos(cam)[0]
    assert novo["config_hash"] != e1["config_hash"] and novo["quadro_id"] == 3


def test_salvar_igual_nao_remonta(adm) -> None:
    cam, vid = adm.criar_camera(), so_bebe(adm)
    _montar(adm, cam, vid, crop_margem_h=10)
    alvo = adm.porta(cam).alvo
    salvar(adm, cam, especialista=BEBE, receita_versao_id=vid, crop_margem_h=10)
    adm.servicos.sombra.reconcile()
    assert adm.porta(cam).alvo is alvo


def test_duas_cameras_mesmo_pacote_ajustes_distintos_sem_vazamento(adm) -> None:
    c1, c2, vid = adm.criar_camera(), adm.criar_camera(nome="Corredor", ip="10.0.0.6"), so_bebe(adm)
    salvar(adm, c1, especialista=BEBE, receita_versao_id=vid, exec_orcamento_ms=500)
    salvar(adm, c2, especialista=BEBE, receita_versao_id=vid, exec_orcamento_ms=300, crop_margem=25, exec_intervalo_min_s=1)
    adm.rodar_ia()
    for c in (c1, c2):
        adm.post(f"/painel/cameras/{c}/sombra", {"acao": "ligar"})
    g = adm.servicos.sombra
    g.reconcile()
    for q in (1, 2):
        adm.alimentar(c1, quadro_id=q)
    adm.alimentar(c2, quadro_id=1)
    e1, e2 = g.eventos(c1), g.eventos(c2)
    assert len(e1) == 2 and len(e2) == 1
    assert {e["config_hash"] for e in e1}.isdisjoint({e["config_hash"] for e in e2}) and len({e["config_hash"] for e in e1}) == 1
    assert g._slots[c1].motor is not g._slots[c2].motor and adm.porta(c1) is not adm.porta(c2)
    assert adm.porta(c1).ajustes.execucao.intervalo_min_s == 0.0 and adm.porta(c2).ajustes.execucao.intervalo_min_s == 1.0
    assert g._slots[c2].motor._receitas[("so-bebe", "1")]["vinculos"][0]["orcamento_ms"] == 300
    assert g._slots[c1].motor._receitas[("so-bebe", "1")]["vinculos"][0]["orcamento_ms"] == 500


def test_duas_cameras_pacotes_diferentes_sem_vazamento_de_atributos(adm) -> None:
    c1, c2 = adm.criar_camera(), adm.criar_camera(nome="Corredor", ip="10.0.0.6")
    v_bebe, v_cor = so_bebe(adm), so_cor(adm)
    salvar(adm, c1, especialista=BEBE, receita_versao_id=v_bebe)
    salvar(adm, c2, especialista=COR, receita_versao_id=v_cor)
    adm.rodar_ia()
    for c in (c1, c2):
        adm.post(f"/painel/cameras/{c}/sombra", {"acao": "ligar"})
    adm.servicos.sombra.reconcile()
    adm.alimentar(c1)
    adm.alimentar(c2)
    [e1], [e2] = adm.servicos.sombra.eventos(c1), adm.servicos.sombra.eventos(c2)
    assert {c["especialista"] for c in e1["condicoes"]} == {BEBE} and {c["especialista"] for c in e2["condicoes"]} == {COR}
    assert e1["receita"] == "so-bebe v1" and e2["receita"] == "so-cor v1" and e1["config_hash"] != e2["config_hash"]
    assert [v["id"] for v in e1["versoes"]] == [BEBE] and [v["id"] for v in e2["versoes"]] == [COR]


def test_camera_sem_configuracao_segue_o_caminho_legado(adm) -> None:
    cam, multi = adm.criar_camera(), adm.criar_receita()
    adm.associar_legado(cam, multi)
    adm.rodar_ia()
    adm.post(f"/painel/cameras/{cam}/sombra", {"acao": "ligar"})
    adm.servicos.sombra.reconcile()
    porta = adm.porta(cam)
    assert porta.alvo is not None and porta.ajustes is None and adm.servicos.sombra._slots[cam].motor._ajustes == {}
    adm.alimentar(cam)
    assert {e["veredito"] for e in adm.servicos.sombra.eventos(cam)} == {"sim"}


def _acoes(adm) -> list[str]:
    from sqlalchemy import select

    from src.jutta_ped.painel.models import Auditoria
    with adm.database.session() as db:
        return [a.acao for a in db.scalars(select(Auditoria)).all()]


def test_paginas_de_diagnostico_com_especialista_e_receita_configurados_nao_dao_500(adm) -> None:
    """Regressao achada na validacao REAL: com config + receita salvas e o diagnostico sem objeto (desligado ou ligado sem cena),
    a pagina, o fragmento e os JPEG nao podem falhar (faltava `definicao_da_camera`)."""
    cam = adm.criar_camera()
    vid = so_bebe(adm)
    assert salvar(adm, cam, especialista=BEBE, receita_versao_id=vid, perfil="pediatria_legado", roi_ativa="1", roi_x1=10, roi_y1=0, roi_x2=90, roi_y2=100).status_code == 303
    assert adm.client.get(f"/painel/cameras/{cam}/diagnostico").status_code == 200                # desligado
    assert adm.client.get(f"/painel/cameras/{cam}/diagnostico/resumo").status_code == 200
    assert ligar(adm, cam).status_code == 303
    assert adm.client.get(f"/painel/cameras/{cam}/diagnostico").status_code == 200                # ligado, sem IA/cena ainda
    assert adm.client.get(f"/painel/cameras/{cam}/diagnostico/resumo").status_code == 200
    ctx = adm.contexto("partials/diagnostico_mestre.html")
    assert ctx["diag"]["ajustes"]["crop"]["margem_h"] == 0.12 and ctx["diag"]["estado"] in ("aguardando", "desligado")
    assert adm.client.get(f"/painel/cameras/{cam}/diagnostico/quadro.jpg").status_code == 404


def test_miniaturas_do_recorte_usam_url_absoluta_e_o_quadro_do_resumo(adm) -> None:
    """Regressao da validacao REAL: URL relativa resolvia contra o DOCUMENTO (/painel/cameras/1/recorte/..: 404 no navegador)."""
    cam = adm.criar_camera()
    adm.rodar_ia()
    assert ligar(adm, cam).status_code == 303
    adm.porta(cam).processar_cena(cena(cam, quadro_id=42))
    base = f"/painel/cameras/{cam}/diagnostico"
    pagina = adm.client.get(base).text
    fragmento = adm.client.get(base + "/resumo").text
    for html in (pagina, fragmento):
        assert f'src="{base}/recorte/5.jpg?q=42"' in html and f'src="{base}/recorte/6.jpg?q=42"' in html
        assert 'src="recorte/' not in html                                                   # nunca relativa
    assert adm.client.get(f"{base}/recorte/5.jpg?q=42").status_code == 200
    adm.porta(cam).processar_cena(cena(cam, quadro_id=43))                                   # chega cena nova: o quadro 42 ainda e servido
    assert adm.client.get(f"{base}/recorte/5.jpg?q=42").status_code == 200
    assert adm.client.get(f"{base}/recorte/5.jpg?q=7").status_code == 404                    # quadro nao retido
