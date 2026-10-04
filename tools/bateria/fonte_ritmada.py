"""Fonte de video ritmada: entrega os quadros no ritmo nativo do arquivo (como uma camera ao vivo).

A bateria nao depende de go2rtc nem de RTSP: cada "camera" e um arquivo reproduzido em tempo real e em laco, embrulhado
por `LatestFrameCapture` (o mesmo wrapper que o runtime usa para fontes de rede), de modo que quadros acumulados sao
descartados quando a IA e mais lenta que a fonte. O momento em que cada quadro e entregue fica em `prod`, para medir a
IDADE da observacao (entrega do quadro -> resultado disponivel).
"""
from __future__ import annotations

import time
from collections import deque
from collections.abc import Callable
from pathlib import Path
from typing import Any

import cv2


class CapturaRitmada:
    """Interface minima de `cv2.VideoCapture` usada pelo runtime: isOpened, read, release, get, set."""

    def __init__(self, caminho: str | Path, *, loop: bool = True, relogio: Callable[[], float] = time.perf_counter,
                 dormir: Callable[[float], None] = time.sleep, captura: Any | None = None, memoria: int = 256) -> None:
        self._caminho, self._injetada = str(caminho), captura is not None
        self._cap = captura if captura is not None else cv2.VideoCapture(self._caminho)
        self.fps = float(self._cap.get(cv2.CAP_PROP_FPS) or 25.0)
        self._loop, self._relogio, self._dormir = loop, relogio, dormir
        self._proximo: float | None = None
        self.prod: dict[int, float] = {}                 # id(quadro) -> instante de entrega
        self._ordem: deque[int] = deque()
        self._memoria = memoria
        self.entregues = 0
        self.voltas = 0

    def isOpened(self) -> bool:  # noqa: N802 -- mesma assinatura do cv2
        return bool(self._cap.isOpened())

    def read(self) -> tuple[bool, Any]:
        agora = self._relogio()
        if self._proximo is None:
            self._proximo = agora
        espera = self._proximo - agora
        if espera > 0:
            self._dormir(espera)
        elif espera < -0.5:                              # muito atrasado: ressincroniza em vez de acumular divida
            self._proximo = agora
        self._proximo += 1.0 / self.fps
        ok, quadro = self._cap.read()
        if not ok and self._loop:
            self.voltas += 1
            if self._injetada:                       # captura de teste: so reposiciona
                self._cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
            else:                                    # arquivo real: REABRE (reposicionar falhou de forma intermitente em .dav)
                self._cap.release()
                self._cap = cv2.VideoCapture(self._caminho)
            ok, quadro = self._cap.read()
        if not ok:
            return False, None
        self.entregues += 1
        self.prod[id(quadro)] = self._relogio()
        self._ordem.append(id(quadro))
        while len(self._ordem) > self._memoria:
            self.prod.pop(self._ordem.popleft(), None)
        return True, quadro

    def release(self) -> None:
        self._cap.release()

    def get(self, prop: int) -> float:
        return self._cap.get(prop)

    def set(self, prop: int, valor: float) -> bool:
        return self._cap.set(prop, valor)
