"""Sombra DESACOPLADA do laco operacional + idade do quadro ao vivo desde a recepcao local."""
from __future__ import annotations

import threading
import time
from pathlib import Path

from test_especialistas_executor import ModeloFalso
from test_motor_sombra import SIM_AZUL, obs, quadro  # noqa: F401
from test_motor_sombra_runtime import ServicoFalso, det, motor_real, quadro_com
from test_pediatria_latest_frame_capture import CapturaFalsa
from test_pediatria_service_runtime_reconnect import ScriptedCapture, make_bare_session

from src.jutta_ped.motor.despacho import DespachoSombra
from src.jutta_ped.service.runtime import LatestFrameCapture, SessionStartConfig


def test_especialista_lento_nao_bloqueia_quem_enfileira(tmp_path: Path) -> None:
    solta = threading.Event()
    inferir = ModeloFalso.inferir
    ModeloFalso.inferir = lambda self, imgs: (solta.wait(5), inferir(self, imgs))[1]
    d = DespachoSombra(motor_real(tmp_path, relogio=time.monotonic), max_pendentes_por_camera=1)
    try:
        t0 = time.perf_counter()
        for i in range(20):
            d.processar("test_camera", 1, quadro_com(50, 60, SIM_AZUL), [obs(1, 50, 60, cam="test_camera", quadro_id=i, t=time.monotonic())])
        assert time.perf_counter() - t0 < 0.5                              # 20 envios com o especialista travado
        assert d.contadores["descartado_fila_cheia"] >= 18                 # fila limitada: descarta o mais antigo
        solta.set()
        assert d.aguardar_ociosa(10)
    finally:
        solta.set()
        ModeloFalso.inferir = inferir
        d.parar()


def test_destino_do_rastro_bloqueado_nao_bloqueia_quem_enfileira(tmp_path: Path) -> None:
    solta = threading.Event()
    d = DespachoSombra(motor_real(tmp_path, relogio=time.monotonic, destino=lambda e: solta.wait(5)))
    try:
        t0 = time.perf_counter()
        for i in range(10):
            d.processar("test_camera", 1, quadro_com(50, 60, SIM_AZUL), [obs(1, 50, 60, cam="test_camera", quadro_id=i, t=time.monotonic())])
        assert time.perf_counter() - t0 < 0.5
    finally:
        solta.set()
        d.aguardar_ociosa(10)
        d.parar()


def test_desabilitado_nao_enfileira(tmp_path: Path) -> None:
    motor = motor_real(tmp_path, relogio=time.monotonic)
    motor.habilitado = False
    d = DespachoSombra(motor)
    d.processar("test_camera", 1, quadro(), [])
    assert d.contadores["enfileirado"] == 0
    d.parar()


def test_laco_com_sombra_lenta_nao_atrasa_a_sessao(tmp_path: Path) -> None:
    solta = threading.Event()
    d = DespachoSombra(motor_real(tmp_path, relogio=time.monotonic, destino=lambda e: solta.wait(5)))
    n = 30
    s = make_bare_session(SessionStartConfig(source="rtsp://x/cam", report_dir=tmp_path, motor_sombra=d, reconnect_enabled=False), tmp_path)
    s._analysis_fps = 0.0
    s.service = ServicoFalso([[det(1, 50, 60)]])
    s.capture = ScriptedCapture([(True, quadro_com(50, 60, SIM_AZUL)) for _ in range(n)] + [(False, None)])
    t0 = time.perf_counter()
    s._run_loop()
    dt = time.perf_counter() - t0
    solta.set()
    d.aguardar_ociosa(10)
    d.parar()
    assert s.state.frames_processed == n and dt < 2.0                      # destino travado por 5 s: o laco nao esperou


def test_motor_sincrono_e_recusado_em_fonte_ao_vivo_e_aceito_em_arquivo(tmp_path: Path) -> None:
    chamadas: list = []

    class Sincrono:
        def processar(self, *a):
            chamadas.append(a)

    def roda(fonte):
        s = make_bare_session(SessionStartConfig(source=fonte, report_dir=tmp_path, motor_sombra=Sincrono(), reconnect_enabled=False), tmp_path)
        s.service = ServicoFalso([[det(1, 50, 60)]])
        s.capture = ScriptedCapture([(True, quadro_com(50, 60, SIM_AZUL)), (False, None)])
        s._run_loop()
        return s

    ao_vivo = roda("rtsp://x/cam")
    assert chamadas == [] and ao_vivo.evidence_error_counts["motor_sombra_sincrono_recusado_ao_vivo"] == 1
    roda(str(tmp_path / "v.mp4"))
    assert len(chamadas) == 1


def test_idade_do_quadro_ao_vivo_inclui_a_espera_no_buffer_local() -> None:
    cap = LatestFrameCapture(CapturaFalsa(total=1, intervalo=0.0))
    try:
        time.sleep(0.3)                                                    # o quadro ja foi recebido; ninguem consumiu ainda
        t_consumo = time.monotonic()
        ok, _ = cap.read()
        assert ok and cap.ultimo_recebido_em is not None
        assert t_consumo - cap.ultimo_recebido_em >= 0.25                  # a recepcao e ANTERIOR ao consumo
    finally:
        cap.release()


def test_laco_usa_a_recepcao_local_como_instante_de_captura(tmp_path: Path) -> None:
    visto: dict = {}

    class Espia:
        seguro_para_ao_vivo = True

        def processar(self, cam, ger, frame, observacoes):
            visto["capturado_em"] = observacoes[0].capturado_em

    class CapturaComRecepcao(ScriptedCapture):
        ultimo_recebido_em = time.monotonic() - 3.0                        # recebido ha 3 s, consumido agora

    s = make_bare_session(SessionStartConfig(source="rtsp://x/cam", report_dir=tmp_path, motor_sombra=Espia(), reconnect_enabled=False), tmp_path)
    s.service = ServicoFalso([[det(1, 50, 60)]])
    s.capture = CapturaComRecepcao([(True, quadro_com(50, 60, SIM_AZUL)), (False, None)])
    ref = s.capture.ultimo_recebido_em
    s._run_loop()
    assert visto["capturado_em"] == ref and time.monotonic() - visto["capturado_em"] >= 3.0


# --- ciclo de vida do despacho ------------------------------------------------------------------------------------

class MotorEspiao:
    """Motor falso: registra o que processa e pode segurar o primeiro item ate ser liberado."""

    def __init__(self, segurar_primeiro: bool = False) -> None:
        self.habilitado = True
        self.vistos: list[tuple[str, int]] = []
        self.entrou = threading.Event()
        self.libera = threading.Event()
        if not segurar_primeiro:
            self.libera.set()

    def processar(self, camera, geracao, quadro, observacoes):
        self.vistos.append((camera, geracao))
        self.entrou.set()
        self.libera.wait(5)
        return []


def _vivas(prefixo: str) -> list[str]:
    return [t.name for t in threading.enumerate() if t.name.startswith(prefixo) and t.is_alive()]


def test_criar_e_parar_varias_vezes_nao_deixa_threads_vivas() -> None:
    for i in range(25):
        d = DespachoSombra(MotorEspiao(), nome=f"ciclo-{i}")
        d.processar("c", 1, None, [])
        assert d.parar() is True and not d.viva
    assert _vivas("ciclo-") == []


def test_parar_duas_vezes_e_seguro_e_depois_de_parar_nada_entra() -> None:
    motor = MotorEspiao()
    d = DespachoSombra(motor, nome="para-2x")
    assert d.parar() is True and d.parar() is True and d.parar(timeout=0) is True
    assert d.processar("c", 1, None, []) == [] and d.contadores["enfileirado"] == 0 and motor.vistos == []
    assert d.aguardar_ociosa(0.5) is True                                          # parado conta como ocioso (nao fica esperando)


def test_parar_descarta_o_pendente_e_nao_comeca_item_novo() -> None:
    motor = MotorEspiao(segurar_primeiro=True)
    d = DespachoSombra(motor, nome="descarta")
    d.processar("a", 1, None, [])
    assert motor.entrou.wait(2)                                                     # 'a' esta em execucao (preso)
    d.processar("b", 1, None, [])
    d.processar("c", 1, None, [])                                                   # b e c ficam pendentes
    assert d.parar(timeout=0.2) is False                                            # item em execucao: nao deu para juntar a tempo
    assert d.contadores["descartado_ao_parar"] == 2 and d.parado
    motor.libera.set()                                                              # o item em execucao termina; nada novo comeca
    assert d.parar(timeout=3) is True and not d.viva
    assert motor.vistos == [("a", 1)]


def test_item_de_geracao_anterior_nao_e_processado() -> None:
    motor = MotorEspiao(segurar_primeiro=True)
    d = DespachoSombra(motor, nome="geracao", max_pendentes_por_camera=3)
    try:
        d.processar("c", 1, None, [])
        assert motor.entrou.wait(2)                                                 # geracao 1 em execucao
        d.processar("c", 1, None, [])                                               # pendente da geracao 1
        d.processar("c", 2, None, [])                                               # sessao nova: o pendente velho e descartado
        assert d.contadores["descartado_geracao_obsoleta"] == 1
        d.processar("c", 1, None, [])                                               # retardatario da sessao antiga e recusado
        assert d.contadores["recusado_geracao_obsoleta"] == 1
        motor.libera.set()
        assert d.aguardar_ociosa(5)
        assert motor.vistos == [("c", 1), ("c", 2)]
    finally:
        motor.libera.set()
        d.parar()


def test_aguardar_ociosa_espera_o_item_em_execucao() -> None:
    motor = MotorEspiao(segurar_primeiro=True)
    d = DespachoSombra(motor, nome="ociosa")
    try:
        d.processar("c", 1, None, [])
        assert motor.entrou.wait(2)
        assert d.aguardar_ociosa(0.1) is False                                      # fila vazia, mas ainda executando
        motor.libera.set()
        assert d.aguardar_ociosa(5) is True and d.contadores["processado"] == 1
    finally:
        motor.libera.set()
        d.parar()


def test_erro_do_motor_nao_mata_o_worker() -> None:
    class Quebrado(MotorEspiao):
        def processar(self, *a):
            super().processar(*a)
            raise RuntimeError("boom")

    d = DespachoSombra(Quebrado(), nome="quebrado")
    try:
        d.processar("c", 1, None, [])
        assert d.aguardar_ociosa(5) and d.contadores["erro_no_motor"] == 1
        d.processar("c", 1, None, [])
        assert d.aguardar_ociosa(5) and d.contadores["erro_no_motor"] == 2 and d.viva
    finally:
        d.parar()


def test_despacho_e_porta_sao_seguros_ao_vivo_e_o_sincrono_continua_recusado(tmp_path: Path) -> None:
    from src.jutta_ped.painel.sombra_service import PortaSombra
    assert DespachoSombra.seguro_para_ao_vivo is True and PortaSombra.seguro_para_ao_vivo is True
    from src.jutta_ped.motor.motor import MotorSombra
    assert getattr(MotorSombra, "seguro_para_ao_vivo", False) is False             # o motor direto NUNCA e aceito ao vivo


def test_processar_e_processar_cena_dividem_a_mesma_fila_em_ordem() -> None:
    from src.jutta_ped.motor.cena import criar_cena

    class Misto(MotorEspiao):
        def processar_cena(self, cena):
            self.vistos.append((cena.camera_ref, cena.geracao))
            return []

    motor = Misto(segurar_primeiro=True)
    d = DespachoSombra(motor, nome="misto", max_pendentes_por_camera=5)
    try:
        d.processar("c", 1, None, [])
        assert motor.entrou.wait(2)
        d.processar_cena(criar_cena("c", 1, 1, 1.0, 10, 10))
        d.processar("c", 1, None, [])
        motor.libera.set()
        assert d.aguardar_ociosa(5) and motor.vistos == [("c", 1)] * 3 and d.contadores["processado"] == 3
    finally:
        motor.libera.set()
        d.parar()
