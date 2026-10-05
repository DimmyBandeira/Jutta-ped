"""Regressao: ler as classes de um modelo exportado nao pode escolher outro dispositivo (CUDA) por conta propria.

Defeito encontrado no ensaio com RTSP real: depois que o detector de pessoas roda com `device="cpu"` no mesmo processo, o Ultralytics oculta
a GPU (CUDA_VISIBLE_DEVICES) e `modelo.names` de um OpenVINO falhava com "Invalid device id" ao montar o preditor com `device=None`."""
from __future__ import annotations

from src.jutta_ped.especialistas.backend_ultralytics import ModeloUltralytics

FORMATO = {"cor": "RGB", "largura": 640, "altura": 640, "redimensionamento": "letterbox", "normalizacao": "0-1"}


class ModeloExportadoFalso:
    """Imita o `YOLO(...)` de um modelo exportado: `names` so funciona se o dispositivo ja estiver definido nos overrides."""

    def __init__(self) -> None:
        self.overrides: dict = {"device": None}

    @property
    def names(self) -> dict:
        if self.overrides.get("device") is None:                  # o preditor escolheria CUDA por padrao
            raise AssertionError("Invalid device id")
        return {0: "child", 1: "adult"}


def test_nomes_das_classes_usam_o_dispositivo_escolhido_pelo_executor() -> None:
    for dispositivo in ("cpu", "intel:gpu", "cuda:0"):
        m = ModeloUltralytics(ModeloExportadoFalso(), dispositivo, FORMATO, "detect")
        assert m.nomes == ["child", "adult"] and m.dispositivo == dispositivo


def test_modelo_sem_overrides_continua_funcionando() -> None:
    class Simples:
        names = ["a", "b"]

    assert ModeloUltralytics(Simples(), "cpu", FORMATO, "detect").nomes == ["a", "b"]


def test_runner_operacional_le_as_classes_sem_escolher_cuda(tmp_path) -> None:
    """Mesmo defeito no caminho OPERACIONAL: iniciar uma sessao nova depois de outra inferencia em CPU no processo."""
    from modulo.pediatria.detector_mvp import PediatricsDetectorMvpRunner

    modelo = ModeloExportadoFalso()
    (tmp_path / "modelo").mkdir()
    runner = PediatricsDetectorMvpRunner(tmp_path / "modelo", model_loader=lambda _p: modelo)
    assert runner.names == {0: "child", 1: "adult"} and modelo.overrides["device"] is None      # nada ficou mudado no modelo
