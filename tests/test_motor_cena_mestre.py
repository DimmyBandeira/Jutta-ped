"""Mestre compartilhado (um detector por quadro) -> Cena -> motor em sombra. Sem modelo real: detector, especialista e motor sao dubles."""
from __future__ import annotations

import threading
import time
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from test_pediatria_person_crop_pipeline import _make_pipeline, _person
from test_pediatria_service_runtime_reconnect import ScriptedCapture, make_bare_session

from modulo.pediatria.detector_mvp import PediatricDetection
from src.jutta_ped.motor.cena import Cena, criar_cena
from src.jutta_ped.motor.despacho import DespachoSombra
from src.jutta_ped.painel.sombra_service import PortaSombra
from src.jutta_ped.service.pediatria_service import PediatriaService, PediatriaServiceConfig
from src.jutta_ped.service.runtime import SessionStartConfig

FRAME = np.zeros((480, 640, 3), np.uint8)
KW = dict(device="cpu", person_confidence=0.2, specialist_accept_confidence=0.25, cache_ttl_frames=12)


# --- dubles do mestre ---------------------------------------------------------------------------------------------

class DetectorFalso:
    """Substitui `person_model`: conta chamadas a `track`; o parse e trocado pelo pipeline para devolver `pessoas`."""

    def __init__(self) -> None:
        self.chamadas = 0

    def track(self, frame, **kw):
        self.chamadas += 1
        return [SimpleNamespace(n=self.chamadas)]


class EspecialistaFalso:
    def __init__(self) -> None:
        self.chamadas = 0

    def predict(self, crop, **kw):
        self.chamadas += 1
        return [SimpleNamespace(boxes=None)]


def pipeline_com_dubles(pessoas=None):
    p = _make_pipeline()
    p.person_model = DetectorFalso()
    p.specialist_model = EspecialistaFalso()
    pessoas = pessoas if pessoas is not None else [_person(1), _person(2, bbox=(300.0, 100.0, 360.0, 260.0))]
    p._parse_person_result = lambda r: list(pessoas)
    return p


# --- (i)/(ii)/(iii) pipeline --------------------------------------------------------------------------------------

def test_mestre_nao_toca_classificador_nem_cache() -> None:
    p = pipeline_com_dubles()
    pessoas = p.detectar_pessoas(FRAME, frame_index=1, **{k: KW[k] for k in ("device", "person_confidence")})
    assert [x.track_id for x in pessoas] == [1, 2]
    assert p.person_model.chamadas == 1 and p.specialist_model.chamadas == 0
    assert p._cache == {} and p.stats["specialist_inference"] == 0 and p.last_trace == {}


def test_detect_and_classify_consome_a_reserva_e_nao_roda_o_detector_de_novo() -> None:
    p = pipeline_com_dubles()
    p.detectar_pessoas(FRAME, frame_index=5, device="cpu", person_confidence=0.2)
    dets, persons = p.detect_and_classify(FRAME, frame_index=5, **KW)
    assert p.person_model.chamadas == 1                                   # UM detector por quadro
    assert [x.track_id for x in persons] == [1, 2] and len(dets) == 2
    assert p.specialist_model.chamadas == 2                               # a logica pediatrica segue normal
    assert p._reserva is None                                             # reserva limpa depois do uso


def test_sem_reserva_roda_uma_vez_como_antes() -> None:
    p = pipeline_com_dubles()
    p.detect_and_classify(FRAME, frame_index=1, **KW)
    assert p.person_model.chamadas == 1 and p.last_frame_meta["detector_reused_frame"] is False


def test_reserva_de_outro_frame_e_ignorada_e_descartada() -> None:
    p = pipeline_com_dubles()
    p.detectar_pessoas(FRAME, frame_index=5, device="cpu", person_confidence=0.2)
    p.detect_and_classify(FRAME, frame_index=6, **KW)
    assert p.person_model.chamadas == 2 and p._reserva is None
    p.detect_and_classify(FRAME, frame_index=7, **KW)                     # reserva nao vaza para o frame seguinte
    assert p.person_model.chamadas == 3


def test_reserva_e_consumida_uma_unica_vez() -> None:
    p = pipeline_com_dubles()
    p.detectar_pessoas(FRAME, frame_index=5, device="cpu", person_confidence=0.2)
    p.detect_and_classify(FRAME, frame_index=5, **KW)
    p.detect_and_classify(FRAME, frame_index=5, **KW)
    assert p.person_model.chamadas == 2


def test_stride_do_detector_e_respeitado_com_e_sem_mestre() -> None:
    for com_mestre in (True, False):
        p = pipeline_com_dubles()
        vistos = []
        for i in range(1, 7):
            if com_mestre:
                vistos.append(p.detectar_pessoas(FRAME, frame_index=i, device="cpu", person_confidence=0.2, detector_stride_frames=3))
            p.detect_and_classify(FRAME, frame_index=i, detector_stride_frames=3, **KW)
        assert p.person_model.chamadas == 2                                # roda nos frames 1 e 4
        if com_mestre:
            assert all([x.track_id for x in v] == [1, 2] for v in vistos)  # nos frames pulados o mestre reaproveita a ultima leitura
    p = pipeline_com_dubles()
    p.detectar_pessoas(FRAME, frame_index=1, device="cpu", person_confidence=0.2, detector_stride_frames=3)
    p.detect_and_classify(FRAME, frame_index=1, detector_stride_frames=3, **KW)
    p.detectar_pessoas(FRAME, frame_index=2, device="cpu", person_confidence=0.2, detector_stride_frames=3)
    p.detect_and_classify(FRAME, frame_index=2, detector_stride_frames=3, **KW)
    assert p.last_frame_meta["detector_reused_frame"] is True


def test_resultado_de_detect_and_classify_identico_com_e_sem_reserva() -> None:
    a, b = pipeline_com_dubles(), pipeline_com_dubles()
    a.detectar_pessoas(FRAME, frame_index=1, device="cpu", person_confidence=0.2)
    ra = a.detect_and_classify(FRAME, frame_index=1, **KW)
    rb = b.detect_and_classify(FRAME, frame_index=1, **KW)
    assert ra == rb and a.last_trace == b.last_trace and a.stats == b.stats


def test_reset_limpa_a_reserva() -> None:
    p = pipeline_com_dubles()
    p.detectar_pessoas(FRAME, frame_index=1, device="cpu", person_confidence=0.2)
    p.reset()
    assert p._reserva is None


# --- PediatriaService.observar_mestre ------------------------------------------------------------------------------

class Quebra:
    def __getattr__(self, nome):
        raise AssertionError(f"nao deveria tocar em {nome}")


def montar_servico(tmp_path: Path, *, crop: bool = True, stride: int = 1):
    pipeline = pipeline_com_dubles()
    modelo = tmp_path / "pessoa.pt"
    modelo.write_bytes(b"x")
    estab = SimpleNamespace(reset=lambda: None, stabilize=lambda r, f, frame_index: r, last_merges=[])
    prom = SimpleNamespace(reset=lambda: None, promote=lambda *a, **k: [], last_promotions=[])
    svc = PediatriaService(
        PediatriaServiceConfig(specialist_model_path=tmp_path / "esp.pt", person_model_path=modelo, person_crop_pipeline=crop, detector_stride_frames=stride),
        runner_factory=lambda p: SimpleNamespace(model=SimpleNamespace(track=lambda *a, **k: None), names={0: "adult", 1: "child"}, _parse_result=lambda r: []),
        crop_pipeline_factory=lambda **kw: pipeline,
        identity_stabilizer_factory=lambda: estab,
        weak_child_promoter_factory=lambda: prom,
    )
    svc.start_session()
    return svc, pipeline


def test_observar_mestre_nao_roda_classificador_companhia_nem_regras(tmp_path: Path) -> None:
    svc, p = montar_servico(tmp_path)
    svc.analyzer = Quebra()
    svc.identity_stabilizer = Quebra()
    svc.weak_child_promoter = Quebra()
    svc.role_adjuster = Quebra()
    pessoas = svc.observar_mestre(FRAME, 1)
    assert [x.track_id for x in pessoas] == [1, 2] and all(isinstance(x, PediatricDetection) for x in pessoas)
    assert p.specialist_model.chamadas == 0 and p.person_model.chamadas == 1


def test_process_frame_reaproveita_a_deteccao_do_mestre(tmp_path: Path) -> None:
    svc, p = montar_servico(tmp_path)
    svc.observar_mestre(FRAME, 1)
    r = svc.process_frame(FRAME, frame_id=1)
    assert p.person_model.chamadas == 1 and [x.track_id for x in r.person_detections] == [1, 2]
    svc.process_frame(FRAME, frame_id=2)                                   # sem mestre: roda o detector como antes
    assert p.person_model.chamadas == 2


def test_process_frame_sem_mestre_roda_o_detector_uma_vez(tmp_path: Path) -> None:
    svc, p = montar_servico(tmp_path)
    svc.process_frame(FRAME, frame_id=1)
    assert p.person_model.chamadas == 1


def test_observar_mestre_devolve_none_sem_crop_pipeline(tmp_path: Path) -> None:
    svc, _ = montar_servico(tmp_path, crop=False)
    assert svc.observar_mestre(FRAME, 1) is None


def test_observar_mestre_usa_o_stride_da_configuracao(tmp_path: Path) -> None:
    svc, p = montar_servico(tmp_path, stride=2)
    for i in range(1, 5):
        svc.observar_mestre(FRAME, i)
        svc.process_frame(FRAME, frame_id=i)
    assert p.person_model.chamadas == 2                                    # frames 1 e 3


# --- (iv) runtime --------------------------------------------------------------------------------------------------

def pessoa(track, x=50.0, y=60.0, w=80.0, h=200.0, conf=0.9):
    return SimpleNamespace(track_id=track, bbox_xyxy=(x, y, x + w, y + h), confidence=conf, role="adult")


class ServicoMestreFalso:
    """Servico de IA falso com mestre: registra a ORDEM das chamadas. `pessoas_por_quadro` alimenta a Cena."""

    def __init__(self, pessoas_por_quadro, *, state=None, resolved=None):
        self.pessoas, self.n_mestre, self.n_proc, self.ordem = pessoas_por_quadro, 0, 0, []
        self.state = state or SimpleNamespace(stable_state="NO_CHILD", children=[])
        self.resolved = resolved or []
        self._ultimo = None

    def observar_mestre(self, frame, frame_id):
        self.ordem.append(("mestre", frame_id))
        p = self.pessoas[min(self.n_mestre, len(self.pessoas) - 1)]
        self.n_mestre += 1
        self._ultimo = p
        return p

    def process_frame(self, frame, *, frame_id):
        self.ordem.append(("process_frame", frame_id))
        pessoas = self._ultimo if self.n_mestre > self.n_proc else self.pessoas[min(self.n_proc, len(self.pessoas) - 1)]
        self.n_proc += 1
        return SimpleNamespace(latency_ms=1.0, state=self.state, resolved_detections=self.resolved, person_detections=pessoas)


class MotorCenaFalso:
    seguro_para_ao_vivo = True

    def __init__(self, servico=None, falha: bool = False):
        self.cenas: list[Cena] = []
        self.antigas: list = []
        self.servico, self.falha = servico, falha

    def processar_cena(self, cena):
        if self.servico is not None:
            assert self.servico.ordem[-1][0] == "mestre"                   # a Cena chega ANTES do pipeline pediatrico
        if self.falha:
            raise RuntimeError("boom")
        self.cenas.append(cena)
        return []

    def processar(self, *a):
        self.antigas.append(a)


class CapturaComRecepcao(ScriptedCapture):
    ultimo_recebido_em = 123.25


def roda(tmp_path: Path, servico, motor, *, quadros=3, fonte=None, pasta="a"):
    fonte = fonte or str(tmp_path / "v.mp4")
    s = make_bare_session(SessionStartConfig(source=fonte, report_dir=tmp_path / pasta, motor_sombra=motor, reconnect_enabled=False), tmp_path / pasta)
    s._geracao_sombra = 9
    s.service = servico
    s.capture = CapturaComRecepcao([(True, np.zeros((480, 640, 3), np.uint8)) for _ in range(quadros)] + [(False, None)])
    s._run_loop()
    return s


def test_runtime_entrega_uma_cena_por_quadro_antes_do_pipeline_pediatrico(tmp_path: Path) -> None:
    svc = ServicoMestreFalso([[pessoa(5)], [pessoa(5), pessoa(6, x=300.0)], [pessoa(5)]])
    motor = MotorCenaFalso(svc)
    s = roda(tmp_path, svc, motor)
    assert [(c.camera_ref, c.geracao, c.quadro_id, c.recebido_em, c.largura, c.altura) for c in motor.cenas] == [
        ("test_camera", 9, i, 123.25, 640, 480) for i in (1, 2, 3)]
    assert [[d.trilha_id for d in c.deteccoes] for c in motor.cenas] == [[5], [5, 6], [5]]
    assert all(d.classe == "person" for c in motor.cenas for d in c.deteccoes) and motor.cenas[0].quadro.shape == (480, 640, 3)
    assert svc.ordem == [x for i in (1, 2, 3) for x in (("mestre", i), ("process_frame", i))]
    assert motor.antigas == [] and s.evidence_error_counts["motor_sombra"] == 0 and s.state.frames_processed == 3


def test_cena_vazia_tambem_e_entregue(tmp_path: Path) -> None:
    svc = ServicoMestreFalso([[pessoa(5)], [], []])
    motor = MotorCenaFalso()
    roda(tmp_path, svc, motor)
    assert [c.vazia for c in motor.cenas] == [False, True, True] and [c.quadro_id for c in motor.cenas] == [1, 2, 3]


def test_recebido_em_cai_no_relogio_local_sem_recepcao_da_captura(tmp_path: Path) -> None:
    svc = ServicoMestreFalso([[]])
    motor = MotorCenaFalso()
    s = make_bare_session(SessionStartConfig(source=str(tmp_path / "v.mp4"), report_dir=tmp_path, motor_sombra=motor, reconnect_enabled=False), tmp_path)
    s.service = svc
    s.capture = ScriptedCapture([(True, np.zeros((480, 640, 3), np.uint8)), (False, None)])
    antes = time.monotonic()
    s._run_loop()
    assert antes <= motor.cenas[0].recebido_em <= time.monotonic()


def _servico_com_crianca():
    child = PediatricDetection((50.0, 60.0, 130.0, 260.0), "child", 0.9, 1)
    state = SimpleNamespace(stable_state="CHILD_ALONE", raw_state="CHILD_ALONE", reason="teste", children=[SimpleNamespace(child_track_id=1, state="CHILD_ALONE")])
    return ServicoMestreFalso([[pessoa(1)]], state=state, resolved=[child])


def test_saida_operacional_identica_com_e_sem_sombra(tmp_path: Path) -> None:
    com = roda(tmp_path, _servico_com_crianca(), MotorCenaFalso(), pasta="com")
    sem = roda(tmp_path, _servico_com_crianca(), None, pasta="sem")
    assert com.state.popup_count == sem.state.popup_count == 1
    for campo in ("popup_count", "suppressed_count", "frames_processed", "status_final", "last_error", "stop_reason"):
        assert getattr(com.state, campo) == getattr(sem.state, campo), campo
    assert dict(com.event_counts) == dict(sem.event_counts) and dict(com.stable_state_counts) == dict(sem.stable_state_counts)
    assert dict(com.evidence_error_counts) == dict(sem.evidence_error_counts)
    assert com.last_overlay_frame["objects"] == sem.last_overlay_frame["objects"]


def test_motor_que_levanta_nao_afeta_a_sessao(tmp_path: Path) -> None:
    com = roda(tmp_path, _servico_com_crianca(), MotorCenaFalso(falha=True), pasta="com")
    sem = roda(tmp_path, _servico_com_crianca(), None, pasta="sem")
    assert com.evidence_error_counts["motor_sombra"] == 3
    assert com.state.frames_processed == 3 and com.state.stop_reason == "eof" and com.state.last_error is None
    assert (com.state.popup_count, com.state.suppressed_count) == (sem.state.popup_count, sem.state.suppressed_count)
    assert dict(com.event_counts) == dict(sem.event_counts)


def test_cena_invalida_e_contada_e_a_analise_segue(tmp_path: Path) -> None:
    svc = ServicoMestreFalso([[pessoa(1, w=0.0)], [pessoa(1)]])                    # bbox sem largura no 1o quadro
    motor = MotorCenaFalso()
    s = roda(tmp_path, svc, motor, quadros=2)
    assert s.evidence_error_counts["motor_sombra"] == 1 and [c.quadro_id for c in motor.cenas] == [2]
    assert svc.n_proc == 2 and s.state.frames_processed == 2


def test_motor_sincrono_em_fonte_ao_vivo_continua_recusado(tmp_path: Path) -> None:
    class Sincrono(MotorCenaFalso):
        seguro_para_ao_vivo = False

    svc, motor = ServicoMestreFalso([[pessoa(1)]]), Sincrono()
    s = roda(tmp_path, svc, motor, quadros=2, fonte="rtsp://x/cam")
    assert motor.cenas == [] and motor.antigas == [] and svc.n_mestre == 0 and svc.n_proc == 2
    assert s.evidence_error_counts["motor_sombra_sincrono_recusado_ao_vivo"] == 2
    svc2, motor2 = ServicoMestreFalso([[pessoa(1)]]), Sincrono()
    roda(tmp_path, svc2, motor2, quadros=2, pasta="arq")                            # em arquivo e aceito
    assert len(motor2.cenas) == 2


def test_sem_processar_cena_no_motor_mantem_o_caminho_antigo(tmp_path: Path) -> None:
    class Antigo:
        def __init__(self):
            self.chamadas = []

        def processar(self, cam, ger, frame, obs):
            self.chamadas.append((cam, ger, [o.trilha_id for o in obs]))

    svc, motor = ServicoMestreFalso([[pessoa(7)]]), Antigo()
    roda(tmp_path, svc, motor, quadros=2)
    assert motor.chamadas == [("test_camera", 9, [7])] * 2 and svc.n_mestre == 0    # nenhuma passagem de mestre extra


def test_servico_sem_mestre_cai_no_caminho_antigo(tmp_path: Path) -> None:
    class SemMestre(ServicoMestreFalso):
        observar_mestre = None

    class SemCrop(ServicoMestreFalso):
        def observar_mestre(self, frame, frame_id):
            return None                                                            # pipeline de crop desligado

    for classe in (SemMestre, SemCrop):
        motor = MotorCenaFalso()
        s = roda(tmp_path, classe([[pessoa(7)]]), motor, quadros=2, pasta=classe.__name__)
        assert motor.cenas == [] and len(motor.antigas) == 2 and s.evidence_error_counts["motor_sombra"] == 0


# --- (v) despacho e porta ------------------------------------------------------------------------------------------

def cena(quadro_id: int, geracao: int = 1, camera_ref: str = "cam-x") -> Cena:
    return criar_cena(camera_ref, geracao, quadro_id, float(quadro_id), 640, 480, [])


class MotorDeCenas:
    """Motor falso do despacho: so `processar_cena`; pode segurar o primeiro item ate ser liberado."""

    def __init__(self, segurar_primeiro: bool = False) -> None:
        self.habilitado = True
        self.vistos: list[tuple[int, int]] = []
        self.entrou, self.libera = threading.Event(), threading.Event()
        if not segurar_primeiro:
            self.libera.set()

    def processar_cena(self, c):
        self.vistos.append((c.geracao, c.quadro_id))
        self.entrou.set()
        self.libera.wait(5)
        return []


def test_processar_cena_nao_bloqueia_com_motor_lento_e_a_fila_e_limitada() -> None:
    motor = MotorDeCenas(segurar_primeiro=True)
    d = DespachoSombra(motor, max_pendentes_por_camera=1, nome="cena-lenta")
    try:
        t0 = time.perf_counter()
        d.processar_cena(cena(0))
        assert motor.entrou.wait(2)                                                # primeiro em execucao, preso
        for i in range(1, 21):
            assert d.processar_cena(cena(i)) == []
        assert time.perf_counter() - t0 < 0.5
        assert d.contadores["descartado_fila_cheia"] == 19                         # so o mais recente fica pendente
        motor.libera.set()
        assert d.aguardar_ociosa(5)
        assert motor.vistos == [(1, 0), (1, 20)]
    finally:
        motor.libera.set()
        d.parar()


def test_cenas_de_uma_camera_chegam_ao_motor_em_ordem() -> None:
    motor = MotorDeCenas(segurar_primeiro=True)
    d = DespachoSombra(motor, max_pendentes_por_camera=50, nome="cena-ordem")
    try:
        for i in range(30):
            d.processar_cena(cena(i))
        motor.libera.set()
        assert d.aguardar_ociosa(5)
        assert [q for _, q in motor.vistos] == list(range(30))
    finally:
        motor.libera.set()
        d.parar()


def test_cena_de_geracao_antiga_e_recusada_e_pendente_antigo_e_descartado() -> None:
    motor = MotorDeCenas(segurar_primeiro=True)
    d = DespachoSombra(motor, max_pendentes_por_camera=5, nome="cena-geracao")
    try:
        d.processar_cena(cena(1, 1))
        assert motor.entrou.wait(2)                                                # geracao 1 em execucao
        d.processar_cena(cena(2, 1))                                               # pendente da geracao 1
        d.processar_cena(cena(3, 2))                                               # geracao nova: descarta o pendente velho
        assert d.contadores["descartado_geracao_obsoleta"] == 1
        d.processar_cena(cena(4, 1))                                               # retardatario da geracao antiga
        assert d.contadores["recusado_geracao_obsoleta"] == 1
        motor.libera.set()
        assert d.aguardar_ociosa(5)
        assert motor.vistos == [(1, 1), (2, 3)]
    finally:
        motor.libera.set()
        d.parar()


def test_cena_nao_entra_com_motor_desabilitado_nem_depois_de_parar() -> None:
    motor = MotorDeCenas()
    motor.habilitado = False
    d = DespachoSombra(motor, nome="cena-desab")
    d.processar_cena(cena(1))
    assert d.contadores["enfileirado"] == 0
    motor.habilitado = True
    d.parar()
    d.processar_cena(cena(2))
    assert d.contadores["enfileirado"] == 0 and motor.vistos == []


def test_erro_no_motor_ao_processar_cena_nao_mata_o_worker() -> None:
    class Quebrado(MotorDeCenas):
        def processar_cena(self, c):
            super().processar_cena(c)
            raise RuntimeError("boom")

    d = DespachoSombra(Quebrado(), nome="cena-quebrada")
    try:
        d.processar_cena(cena(1))
        assert d.aguardar_ociosa(5)
        d.processar_cena(cena(2))
        assert d.aguardar_ociosa(5) and d.contadores["erro_no_motor"] == 2 and d.viva
    finally:
        d.parar()


def test_porta_reescreve_camera_ref_e_mantem_a_regra_de_geracao() -> None:
    motor = MotorDeCenas()
    d = DespachoSombra(motor, max_pendentes_por_camera=5, nome="cena-porta")
    porta = PortaSombra(7)
    novas: list[int] = []
    porta.ao_nova_geracao = novas.append
    recebidas: list[Cena] = []
    original = motor.processar_cena
    motor.processar_cena = lambda c: (recebidas.append(c), original(c))[1]        # type: ignore[method-assign]
    try:
        assert porta.processar_cena(cena(1, 1)) == [] and porta.geracao_vigente == 1     # sem alvo: nada, mas a geracao sobe
        porta.definir_alvo(d)
        porta.processar_cena(cena(2, 1))
        porta.processar_cena(cena(3, 2))
        porta.processar_cena(cena(4, 1))                                                  # atrasada: recusada
        assert d.aguardar_ociosa(5)
        assert porta.geracao_vigente == 2 and porta.recusados_geracao == 1 and novas == [2] and porta.recebidos == 2
        vistas = [(c.geracao, c.quadro_id) for c in recebidas]          # (1, 2) pode ter sido descartado como pendente: depende do worker
        assert {c.camera_ref for c in recebidas} == {"cam-7"} and vistas[-1] == (2, 3) and (1, 4) not in vistas and (1, 1) not in vistas
    finally:
        d.parar()


def test_cena_de_geracao_antiga_em_execucao_e_geracao_nova_pela_mesma_porta() -> None:
    motor = MotorDeCenas(segurar_primeiro=True)
    d = DespachoSombra(motor, max_pendentes_por_camera=5, nome="cena-porta2")
    porta = PortaSombra(3)
    porta.definir_alvo(d)
    try:
        porta.processar_cena(cena(1, 1))
        assert motor.entrou.wait(2)                                                # geracao 1 EM EXECUCAO
        porta.processar_cena(cena(2, 2))                                           # a nova e aceita pela mesma porta
        assert porta.geracao_vigente == 2
        porta.processar_cena(cena(3, 1))                                           # e a antiga nao entra mais
        assert porta.recusados_geracao == 1
        motor.libera.set()
        assert d.aguardar_ociosa(5)
        assert motor.vistos == [(1, 1), (2, 2)]
    finally:
        motor.libera.set()
        d.parar()


def test_porta_seguro_para_ao_vivo_e_nao_bloqueia() -> None:
    motor = MotorDeCenas(segurar_primeiro=True)
    d = DespachoSombra(motor, nome="cena-porta3")
    porta = PortaSombra(1)
    porta.definir_alvo(d)
    try:
        t0 = time.perf_counter()
        for i in range(50):
            porta.processar_cena(cena(i))
        assert time.perf_counter() - t0 < 0.5
    finally:
        motor.libera.set()
        d.parar()


@pytest.mark.parametrize("n", [0])
def test_cena_vazia_atravessa_o_despacho(n) -> None:
    motor = MotorDeCenas()
    d = DespachoSombra(motor, nome="cena-vazia")
    try:
        d.processar_cena(cena(n))
        assert d.aguardar_ociosa(5) and motor.vistos == [(1, 0)] and d.contadores["processado"] == 1
    finally:
        d.parar()
