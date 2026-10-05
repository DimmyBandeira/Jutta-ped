"""Validacao funcional do motor com TRES cameras de configuracao independente (05f, lote de evidencia de execucao).

Caminho real de producao, sem atalhos: painel (rotas + banco) -> `GerenteSombra`/`PortaSombra` -> `MotorSombra` -> `Executor` ->
pacotes instalados e ATIVADOS pelo `Instalador`. Unica parte sintetica: o backend de inferencia (`BackendFalso`, deterministico:
le os primeiros pixels do recorte). Portanto estes testes provam identidade, isolamento, invalidacao, resultados tardios e
indeterminacao; NAO provam qualidade de modelo (ver `tools/validar_motor_especialistas.py` para o modelo real).

Cameras: A e B usam o MESMO pacote (BEBE) com ajustes diferentes; C usa OUTRO pacote (COR). Sincronizacao por barreiras e relogio
de captura explicito, sem depender do acaso do video."""
from __future__ import annotations

import json
import threading
import time

import numpy as np
from painel_receitas_helpers import BEBE, COR, SIM_AZUL, adm, amb  # noqa: F401
from sqlalchemy import select
from test_especialistas_executor import ModeloFalso
from test_painel_camera_config import salvar, so_bebe, so_cor
from test_painel_sombra_corridas import _bloquear_inferencia, _desligar, _ligar

from src.jutta_ped.especialistas.executor import E_INTERNO, ErroExecucao, Executor
from src.jutta_ped.motor.cena import Deteccao, criar_cena
from src.jutta_ped.painel.models import CameraConfigVersao, EventoPediatria

PRAZO = 15
LARGURA, ALTURA = 640, 480
BBOX = (100.0, 100.0, 180.0, 300.0)               # 80 x 200 px, bem dentro do quadro
QUADRO = np.full((ALTURA, LARGURA, 3), SIM_AZUL, np.uint8)     # todo pixel = "bebe sim" e "uniforme azul"


class Mundo3:
    """Tres cameras configuradas pelo painel: A e B (pacote BEBE, ajustes distintos) e C (pacote COR)."""

    def __init__(self, adm) -> None:
        self.adm = adm
        self.g = adm.servicos.sombra
        v_bebe, v_cor = so_bebe(adm), so_cor(adm)
        self.a, self.b, self.c = (adm.criar_camera(nome=f"Camera {n}", ip=f"10.0.0.{10 + i}") for i, n in enumerate("ABC"))
        assert len({self.a, self.b, self.c}) == 3
        self.cfg_a = dict(especialista=BEBE, receita_versao_id=v_bebe, crop_margem_h=20, crop_margem_v=10, crop_minimo_px=8)
        self.cfg_b = dict(especialista=BEBE, receita_versao_id=v_bebe, crop_margem_h=5, crop_margem_v=2, crop_minimo_px=8)
        self.cfg_c = dict(especialista=COR, receita_versao_id=v_cor, crop_margem_h=10, crop_margem_v=10, crop_minimo_px=8)
        for cam, cfg in ((self.a, self.cfg_a), (self.b, self.cfg_b), (self.c, self.cfg_c)):
            assert salvar(adm, cam, **cfg).status_code == 303
        adm.rodar_ia()
        for cam in (self.a, self.b, self.c):
            _ligar(adm, cam)
        self.g.reconcile()
        self.trilha = {self.a: 11, self.b: 22, self.c: 33}
        self.quadro = {self.a: 0, self.b: 0, self.c: 0}
        self.formas: list[tuple[int, int]] = []

    def enviar(self, cam: int, *, idade_s: float = 0.0, bbox=BBOX, esperar: bool = True) -> int:
        """Uma Cena do mestre (sintetica) entregue a PORTA da camera, como o laco da sessao faria. `idade_s` envelhece a captura."""
        self.quadro[cam] += 1
        cena = criar_cena(f"cam_{cam}", 1, self.quadro[cam], time.monotonic() - idade_s, LARGURA, ALTURA,
                          [Deteccao(self.trilha[cam], "person", bbox, 0.9)], quadro=QUADRO)
        porta = self.adm.porta(cam)
        porta.processar_cena(cena)
        if esperar and porta.alvo is not None:
            assert porta.alvo.aguardar_ociosa(PRAZO)
        return self.quadro[cam]

    def eventos(self, cam: int) -> list[dict]:
        return self.g.eventos(cam, 100)

    def slot(self, cam: int):
        return self.g._slots[cam]

    def cache(self, cam: int) -> dict:
        return self.slot(cam).motor._cache.get(f"cam-{cam}", {})

    def hash_cfg(self, cam: int) -> str:
        with self.adm.database.session() as db:
            return db.scalars(select(CameraConfigVersao).where(CameraConfigVersao.camera_id == cam).order_by(CameraConfigVersao.versao.desc())).first().config_hash

    def reconciliar(self) -> None:
        self.g.reconcile()


def mundo(adm, monkeypatch) -> Mundo3:
    m = Mundo3(adm)
    original = ModeloFalso.inferir

    def registra(self, imagens):
        m.formas.extend((im.shape[1], im.shape[0]) for im in imagens)       # (largura, altura) de cada recorte realmente inferido
        return original(self, imagens)

    monkeypatch.setattr(ModeloFalso, "inferir", registra)
    return m


# ------------------------------------------------------------------------------------------------ identidade e configuracao

def test_tres_cameras_mantem_identidade_configuracao_e_recorte_proprios(adm, monkeypatch) -> None:
    m = mundo(adm, monkeypatch)
    forma = {}
    for cam in (m.a, m.b, m.c):
        m.formas.clear()
        m.enviar(cam)
        assert len(m.formas) == 1
        forma[cam] = m.formas[0]
    # 80x200: folga/lado = max(8, int(fracao*dim)). A: (16, 20); B: (8, 8); C: (8, 20)
    assert forma[m.a] == (80 + 2 * 16, 200 + 2 * 20) and forma[m.b] == (80 + 2 * 8, 200 + 2 * 8) and forma[m.c] == (80 + 2 * 8, 200 + 2 * 20)
    assert m.hash_cfg(m.a) != m.hash_cfg(m.b) != m.hash_cfg(m.c) and m.hash_cfg(m.a) != m.hash_cfg(m.c)
    esperado = {m.a: (BEBE, "bebe_no_colo"), m.b: (BEBE, "bebe_no_colo"), m.c: (COR, "cor_uniforme")}
    for cam, (pacote, atributo) in esperado.items():
        evs = m.eventos(cam)
        assert len(evs) == 1 and evs[0]["camera_id"] == cam
        e = evs[0]
        assert e["trilha_id"] == m.trilha[cam] and e["quadro_id"] == 1 and e["geracao"] == 1
        assert [v["id"] for v in e["versoes"]] == [pacote] and e["condicoes"][0]["especialista"] == pacote
        assert e["condicoes"][0]["atributo"] == atributo and e["veredito"] == "sim"
    rastro = {cam: [json.loads(x) for x in (adm.tmp / "rt" / "sombra" / f"camera-{cam}.jsonl").read_text(encoding="utf-8").splitlines()] for cam in (m.a, m.b, m.c)}
    for cam, linhas in rastro.items():
        assert [l["camera_ref"] for l in linhas] == [f"cam-{cam}"] and [l["trilha_id"] for l in linhas] == [m.trilha[cam]]
        assert all(l["modo"] == "sombra" and l["alerta_real"] is False for l in linhas)
    assert adm.contar(EventoPediatria) == 0          # nada da sombra entrou no fluxo de alertas operacionais


# ------------------------------------------------------------------------------------------------ mudanca de uma camera

def test_alterar_crop_de_a_invalida_so_a(adm, monkeypatch) -> None:
    m = mundo(adm, monkeypatch)
    for cam in (m.a, m.b, m.c):
        m.enviar(cam)
    antes = {cam: (m.slot(cam).motor, m.slot(cam).montagem, m.slot(cam).assinatura, dict(m.cache(cam)), m.slot(cam).porta.ajustes) for cam in (m.b, m.c)}
    assert m.cache(m.a) and m.slot(m.a).porta.recebidos == 1
    motor_a = m.slot(m.a).motor
    assert salvar(adm, m.a, **{**m.cfg_a, "crop_margem_h": 30}).status_code == 303
    m.reconciliar()
    assert m.slot(m.a).motor is not motor_a and m.cache(m.a) == {}                      # estado de A zerado, motor novo
    assert m.eventos(m.a) == []                                                          # feed de A nao mostra o que era da config anterior
    for cam in (m.b, m.c):                                                               # B e C: nada mudou
        motor, montagem, assinatura, cache, ajustes = antes[cam]
        assert m.slot(cam).motor is motor and m.slot(cam).montagem == montagem and m.slot(cam).assinatura == assinatura
        assert m.cache(cam) == cache and m.slot(cam).porta.ajustes is ajustes and len(m.eventos(cam)) == 1
    m.formas.clear()
    m.enviar(m.a)
    assert m.formas == [(80 + 2 * 24, 200 + 2 * 20)]                                    # 30% de 80 = 24: o novo crop vale ja no proximo quadro
    e = m.eventos(m.a)[0]
    assert e["quadro_id"] == 2 and all(not c["reutilizado"] for c in e["condicoes"])    # nada herdado da config anterior
    assert m.hash_cfg(m.a) != m.hash_cfg(m.b)


def test_roi_de_a_exclui_o_alvo_sem_inventar_resultado_e_sem_afetar_b_e_c(adm, monkeypatch) -> None:
    m = mundo(adm, monkeypatch)
    for cam in (m.a, m.b, m.c):
        m.enviar(cam)
    ligados = {cam: m.slot(cam).motor for cam in (m.b, m.c)}
    # ROI so no quadrante inferior direito (60-100% x 60-100%): o ponto de apoio do alvo (140, 300) fica FORA
    assert salvar(adm, m.a, **m.cfg_a, roi_ativa="on", roi_x1=60, roi_y1=60, roi_x2=100, roi_y2=100).status_code == 303
    m.reconciliar()
    m.formas.clear()
    m.enviar(m.a)
    assert m.formas == [] and m.eventos(m.a) == []                                      # nada executado e NENHUM veredito (nem "nao")
    assert m.slot(m.a).motor._cache.get("cam-%d" % m.a) == {} and m.slot(m.a).motor.contadores["sujeito_fora_da_roi"] == 1
    for cam in (m.b, m.c):
        m.enviar(cam)
        assert m.slot(cam).motor is ligados[cam] and m.eventos(cam)[0]["quadro_id"] == 2 and m.eventos(cam)[0]["veredito"] == "sim"


def test_resultado_em_andamento_da_config_anterior_de_a_nao_reaparece(adm, monkeypatch) -> None:
    m = mundo(adm, monkeypatch)
    m.eventos(m.b)
    dentro, solta = _bloquear_inferencia(monkeypatch)             # a PROXIMA inferencia (A) fica presa no meio
    m.enviar(m.a, esperar=False)
    assert dentro.wait(PRAZO)
    assert salvar(adm, m.a, **{**m.cfg_a, "crop_margem_h": 25}).status_code == 303
    m.reconciliar()                                               # config nova montada; a antiga e aposentada com trabalho pendente
    m.enviar(m.b)                                                 # B segue normalmente enquanto A espera
    solta.set()
    m.g._juntar_aposentados(PRAZO)
    assert m.eventos(m.a) == [] and m.slot(m.a).tardios == 1      # o resultado atrasado nao e vigente (ficou so no rastro, marcado)
    m.enviar(m.a)
    evs = m.eventos(m.a)
    assert len(evs) == 1 and evs[0]["quadro_id"] == 2             # so o quadro novo, processado pela config nova
    marcados = [json.loads(x) for x in (adm.tmp / "rt" / "sombra" / f"camera-{m.a}.jsonl").read_text(encoding="utf-8").splitlines()]
    assert [x.get("aposentado", False) for x in marcados] == [True, False]
    assert len(m.eventos(m.b)) == 1 and m.slot(m.b).tardios == 0 and m.slot(m.c).tardios == 0


def test_receita_de_a_trocada_nao_toca_b_nem_c(adm, monkeypatch) -> None:
    m = mundo(adm, monkeypatch)
    for cam in (m.a, m.b, m.c):
        m.enviar(cam)
    base = {cam: (m.slot(cam).motor, m.slot(cam).montagem) for cam in (m.b, m.c)}
    nova = so_bebe(adm, "so-bebe-v2")
    assert salvar(adm, m.a, **{**m.cfg_a, "receita_versao_id": nova}).status_code == 303
    m.reconciliar()
    assert m.cache(m.a) == {} and m.slot(m.a).montagem is not None
    for cam in (m.b, m.c):
        assert (m.slot(cam).motor, m.slot(cam).montagem) == base[cam] and m.cache(cam)
    m.enviar(m.a)
    assert m.eventos(m.a)[0]["receita"].startswith("so-bebe-v2")
    assert m.eventos(m.b)[0]["receita"].startswith("so-bebe ")


# ------------------------------------------------------------------------------------------------ liga/desliga

def test_desligar_e_religar_a_sombra_nao_reaproveita_estado(adm, monkeypatch) -> None:
    m = mundo(adm, monkeypatch)
    for cam in (m.a, m.b, m.c):
        m.enviar(cam)
    antigo_b, cache_c = m.slot(m.b).motor, dict(m.cache(m.c))
    _desligar(adm, m.b)
    m.reconciliar()
    assert m.eventos(m.b) == [] and m.slot(m.b).motor is None and m.g.estado(m.b)["estado"] == "desligada"
    _ligar(adm, m.b)
    m.reconciliar()
    assert m.slot(m.b).motor is not None and m.slot(m.b).motor is not antigo_b and m.cache(m.b) == {} and m.eventos(m.b) == []
    m.enviar(m.b)
    e = m.eventos(m.b)[0]
    assert e["quadro_id"] == 2 and all(not c["reutilizado"] for c in e["condicoes"])
    assert m.cache(m.c) == cache_c and len(m.eventos(m.c)) == 1             # C nao foi tocada pelo ciclo de B


# ------------------------------------------------------------------------------------------------ indeterminado por contrato

def _falhar_camera(monkeypatch, camera_ref: str, codigo: str | None = None):
    """O executor falha so para pedidos desta camera (as demais seguem pelo executor real)."""
    original = Executor.executar

    def executar(self, pid, pedidos):
        if pedidos and pedidos[0].camera_ref == camera_ref:
            raise ErroExecucao(codigo or E_INTERNO, "falha injetada no teste")
        return original(self, pid, pedidos)

    monkeypatch.setattr(Executor, "executar", executar)


def _so_indeterminado(ev: dict, motivo: str) -> None:
    assert ev["veredito"] == "indeterminado", ev
    assert ev["condicoes"][0]["resultado"] == "indeterminado" and ev["condicoes"][0]["motivo"].startswith(motivo), ev["condicoes"]


def test_erro_do_especialista_vira_indeterminado_so_na_camera_afetada(adm, monkeypatch) -> None:
    m = mundo(adm, monkeypatch)
    _falhar_camera(monkeypatch, "cam-%d" % m.a)
    for cam in (m.a, m.b, m.c):
        m.enviar(cam)
    _so_indeterminado(m.eventos(m.a)[0], "E_INTERNO")
    assert m.eventos(m.b)[0]["veredito"] == "sim" and m.eventos(m.c)[0]["veredito"] == "sim"


def test_prazo_esgotado_antes_de_enviar_vira_indeterminado_atrasado(adm, monkeypatch) -> None:
    m = mundo(adm, monkeypatch)
    m.formas.clear()
    m.enviar(m.a, idade_s=2.0)                                               # captura ha 2 s: o orcamento de 500 ms ja estourou
    _so_indeterminado(m.eventos(m.a)[0], "atrasado")
    assert m.formas == []                                                    # o especialista NEM foi chamado
    m.enviar(m.b)
    assert m.eventos(m.b)[0]["veredito"] == "sim"                            # outra camera, mesma hora: prazo cumprido


def test_atributo_expirado_vira_indeterminado_e_nao_nao(adm, monkeypatch) -> None:
    m = mundo(adm, monkeypatch)
    assert salvar(adm, m.a, **{**m.cfg_a, "exec_orcamento_ms": 5000, "exec_validade_max_ms": 50}).status_code == 303
    m.reconciliar()
    m.enviar(m.a)
    assert m.eventos(m.a)[0]["veredito"] == "sim"                            # fresco: dentro da validade apertada pela camera (50 ms)
    _falhar_camera(monkeypatch, "cam-%d" % m.a)                              # a partir daqui nenhum resultado novo chega
    time.sleep(0.12)                                                         # > 50 ms: o atributo guardado vence
    m.enviar(m.a)
    ev = m.eventos(m.a)[0]
    _so_indeterminado(ev, "expirado")
    assert ev["condicoes"][0]["falha_recente"] is None or ev["condicoes"][0]["falha_recente"].startswith("E_INTERNO")


def test_validade_da_camera_nunca_amplia_a_do_contrato(adm, monkeypatch) -> None:
    m = mundo(adm, monkeypatch)
    # contrato do pacote: 1500 ms. Pedir 60 000 ms nao pode manter vivo um atributo alem do contrato.
    salvar(adm, m.a, **{**m.cfg_a, "exec_validade_max_ms": 60000})
    cond = m.slot(m.a).motor._receitas
    m.reconciliar()
    receita = next(iter(m.slot(m.a).motor._receitas.values()))
    assert all(c.get("validade_ms", 1500) <= 1500 for c in receita["regra"]["condicoes"]), (cond, receita)


def test_estouro_de_prazo_apos_o_especialista_nao_vira_resultado_vigente(adm, monkeypatch) -> None:
    """Resultado que volta DEPOIS do orcamento nao e guardado (indeterminado 'atrasado'), mesmo tendo sido calculado."""
    m = mundo(adm, monkeypatch)
    assert salvar(adm, m.a, **{**m.cfg_a, "exec_orcamento_ms": 150}).status_code == 303
    m.reconciliar()
    original = ModeloFalso.inferir

    def lento(self, imagens):
        time.sleep(0.25)                                                     # > 150 ms de orcamento
        return original(self, imagens)

    monkeypatch.setattr(ModeloFalso, "inferir", lento)
    m.enviar(m.a)
    ev = m.eventos(m.a)[0]
    assert ev["veredito"] == "indeterminado" and ev["condicoes"][0]["motivo"] in {"atrasado", "E_PRAZO_ESGOTADO"}, ev["condicoes"]
    assert m.cache(m.a) == {}
