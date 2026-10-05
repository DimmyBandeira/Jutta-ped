"""Backend Ultralytics: modelo OpenVINO exportado com batch fixo recebe o lote do executor FATIADO nesse tamanho (achado da fumaça real)."""
from __future__ import annotations

import numpy as np

from src.jutta_ped.especialistas.backend_ultralytics import ModeloUltralytics

FORMATO = {"cor": "RGB", "largura": 64, "altura": 64, "redimensionamento": "letterbox", "normalizacao": "0-1"}


class _Caixas:
    def __init__(self) -> None:
        self.cls = _T(np.array([1]))
        self.conf = _T(np.array([0.9]))

    def __len__(self) -> int:
        return 1


class _T:
    def __init__(self, a) -> None:
        self._a = a

    def detach(self):
        return self

    def cpu(self):
        return self

    def numpy(self):
        return self._a


class _Resultado:
    names = {0: "adult", 1: "child"}
    probs = None

    def __init__(self) -> None:
        self.boxes = _Caixas()


class _ModeloFalso:
    names = {0: "adult", 1: "child"}

    def __init__(self) -> None:
        self.lotes: list[int] = []

    def predict(self, imagens, **_kw):
        self.lotes.append(len(imagens))
        return [_Resultado() for _ in imagens]


def _imgs(n: int) -> list:
    return [np.zeros((50, 30, 3), np.uint8) for _ in range(n)]


def test_lote_fixo_do_export_fatia_o_lote_e_preserva_ordem_e_quantidade() -> None:
    m = _ModeloFalso()
    saida = ModeloUltralytics(m, "cpu", FORMATO, "detect", lote_exportado=1).inferir(_imgs(5))
    assert m.lotes == [1, 1, 1, 1, 1] and len(saida) == 5 and saida[0]["deteccoes"] == [("child", 0.9)]


def test_lote_exportado_maior_fatia_em_blocos() -> None:
    m = _ModeloFalso()
    assert len(ModeloUltralytics(m, "cpu", FORMATO, "detect", lote_exportado=4).inferir(_imgs(10))) == 10
    assert m.lotes == [4, 4, 2]


def test_sem_lote_exportado_mantem_lote_unico_e_lista_vazia_e_segura() -> None:
    m = _ModeloFalso()
    assert len(ModeloUltralytics(m, "cpu", FORMATO, "detect").inferir(_imgs(6))) == 6 and m.lotes == [6]
    assert ModeloUltralytics(m, "cpu", FORMATO, "detect", lote_exportado=1).inferir([]) == []
