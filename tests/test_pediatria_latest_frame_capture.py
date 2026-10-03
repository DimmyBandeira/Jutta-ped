"""LatestFrameCapture: a IA deve analisar o frame mais recente, nunca uma fila de frames antigos.

Achado do teste operacional: leitura sequencial acumulava atraso (~12 s com varias cameras) e as caixas do
overlay ficavam descasadas do video.
"""
from __future__ import annotations

import threading
import time

import pytest

from src.jutta_ped.service import runtime as runtime_module
from src.jutta_ped.service.runtime import LatestFrameCapture


class CapturaFalsa:
    """Produz frames numerados o mais rapido possivel (ou ate `total`), como um stream mais rapido que a IA."""

    def __init__(self, total: int | None = None, intervalo: float = 0.0, aberta: bool = True) -> None:
        self.total, self.intervalo, self.aberta = total, intervalo, aberta
        self.n = 0
        self.liberada = threading.Event()
        self.bloquear = threading.Event()      # se definido, read() fica preso ate ser liberado (rede travada)
        self.destravar = threading.Event()

    def isOpened(self) -> bool:  # noqa: N802
        return self.aberta

    def read(self):
        if self.bloquear.is_set():
            self.destravar.wait(10)
            return False, None
        if self.total is not None and self.n >= self.total:
            time.sleep(0.01)
            return False, None
        if self.intervalo:
            time.sleep(self.intervalo)
        self.n += 1
        return True, self.n

    def release(self) -> None:
        self.liberada.set()

    def get(self, prop):
        return 42.0


def _esperar(condicao, timeout=3.0):
    limite = time.monotonic() + timeout
    while time.monotonic() < limite:
        if condicao():
            return True
        time.sleep(0.005)
    return False


def test_entrega_o_frame_mais_recente_e_descarta_os_velhos():
    falsa = CapturaFalsa(intervalo=0.001)
    cap = LatestFrameCapture(falsa)
    try:
        time.sleep(0.3)                           # a IA "demora": o stream produz centenas de frames neste tempo
        ok, frame = cap.read()
        assert ok and frame >= 100                # pulou direto para um frame recente, nao para o 1
        assert cap.dropped >= 100
        ok2, frame2 = cap.read()
        assert ok2 and frame2 > frame             # e nunca repete o mesmo frame
    finally:
        cap.release()


def test_cada_frame_e_entregue_no_maximo_uma_vez():
    cap = LatestFrameCapture(CapturaFalsa(intervalo=0.02))
    try:
        vistos = []
        for _ in range(8):
            ok, frame = cap.read()
            assert ok
            vistos.append(frame)
        assert vistos == sorted(set(vistos))
    finally:
        cap.release()


def test_falha_do_leitor_chega_ao_consumidor_depois_do_ultimo_frame():
    cap = LatestFrameCapture(CapturaFalsa(total=3))
    try:
        assert _esperar(lambda: cap._falhou)
        ok, frame = cap.read()                    # ainda entrega o ultimo frame bom
        assert ok and frame == 3
        assert cap.read() == (False, None)        # e so entao sinaliza a falha (aciona a reconexao do runtime)
    finally:
        cap.release()


def test_sem_frame_novo_no_prazo_retorna_falha_em_vez_de_travar():
    falsa = CapturaFalsa(intervalo=5.0)           # stream parado
    cap = LatestFrameCapture(falsa, read_timeout=0.2)
    inicio = time.monotonic()
    assert cap.read() == (False, None)
    assert time.monotonic() - inicio < 1.5
    falsa.aberta = False
    cap._parar = True
    with cap._cond:
        cap._cond.notify_all()


def test_release_libera_a_captura_e_encerra_a_thread():
    falsa = CapturaFalsa(intervalo=0.001)
    cap = LatestFrameCapture(falsa)
    cap.read()
    cap.release()
    assert falsa.liberada.is_set()
    assert _esperar(lambda: not cap._thread.is_alive())


def test_release_com_leitura_de_rede_travada_nao_bloqueia_e_libera_quando_o_read_volta():
    falsa = CapturaFalsa()
    falsa.bloquear.set()
    cap = LatestFrameCapture(falsa)
    inicio = time.monotonic()
    cap.release()                                 # o leitor esta preso dentro do read()
    assert time.monotonic() - inicio < 5.0
    assert not falsa.liberada.is_set()            # nao libera a captura por baixo de um read() em andamento
    falsa.destravar.set()                         # o read() finalmente volta...
    assert falsa.liberada.wait(3)                 # ...e o proprio leitor libera a captura


def test_captura_fechada_nao_inicia_thread_e_delega_get():
    cap = LatestFrameCapture(CapturaFalsa(aberta=False))
    assert cap.isOpened() is False and cap._thread is None
    assert cap.get(1) == 42.0                     # atributos desconhecidos vao para a captura real
    cap.release()


@pytest.mark.parametrize(
    "fonte,embrulha",
    [("rtsp://x/y", True), ("rtsps://x/y", True), ("http://x/y", True), (0, True), ("C:/videos/demo.mp4", False), ("videos/a.avi", False)],
)
def test_so_fontes_ao_vivo_usam_o_leitor_de_frame_mais_recente(monkeypatch, fonte, embrulha):
    monkeypatch.setattr(runtime_module.cv2, "VideoCapture", lambda _s: CapturaFalsa(aberta=False))
    aberta = runtime_module.open_capture_source(fonte)
    assert isinstance(aberta, LatestFrameCapture) is embrulha     # arquivo: todos os frames, em sequencia


def test_ffmpeg_configurado_para_baixa_latencia_por_padrao():
    opcoes = runtime_module.os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"]
    assert "nobuffer" in opcoes and "low_delay" in opcoes and "rtsp_transport;tcp" in opcoes
