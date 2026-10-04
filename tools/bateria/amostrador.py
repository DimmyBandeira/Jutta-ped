"""Amostragem de recursos (CPU/RAM/VRAM/GPU) e da IDADE das observacoes durante um ensaio.

* Recursos a cada `intervalo_recursos` s: CPU do processo (e filhos) e do sistema, RSS, utilizacao e memoria da GPU NVIDIA.
* Idade em uso a cada `intervalo_idade` s por camera: `agora - instante em que o quadro do ultimo resultado foi entregue pela
  fonte`. E o que um consumidor (regra/popup) que consulta o resultado mais recente enxerga: inclui a espera entre
  resultados, nao so a latencia de processamento.
"""
from __future__ import annotations

import os
import threading
import time
from collections.abc import Callable
from typing import Any

import psutil


class _Nvml:
    def __init__(self) -> None:
        self.ok = False
        try:
            import pynvml

            pynvml.nvmlInit()
            self._n = pynvml
            self.h = pynvml.nvmlDeviceGetHandleByIndex(0)
            self.ok = True
        except Exception:  # noqa: BLE001 -- sem NVML a bateria segue sem as colunas de GPU
            pass

    def ler(self) -> tuple[float | None, float | None]:
        if not self.ok:
            return None, None
        try:
            util = self._n.nvmlDeviceGetUtilizationRates(self.h).gpu
            usada = self._n.nvmlDeviceGetMemoryInfo(self.h).used / (1024 * 1024)
            return float(util), float(usada)
        except Exception:  # noqa: BLE001
            return None, None


class AmostradorRecursos(threading.Thread):
    def __init__(self, observacoes: dict[str, list], *, intervalo_recursos: float = 1.0, intervalo_idade: float = 0.05,
                 relogio: Callable[[], float] = time.perf_counter) -> None:
        super().__init__(name="amostrador-bateria", daemon=True)
        self._obs, self._ir, self._ii, self._relogio = observacoes, intervalo_recursos, intervalo_idade, relogio
        self._parar = threading.Event()
        self.recursos: list[dict[str, Any]] = []
        self.idades: dict[str, list[tuple[float, float]]] = {c: [] for c in observacoes}
        self._proc = psutil.Process(os.getpid())
        self._nvml = _Nvml()
        self.nucleos = psutil.cpu_count(logical=True) or 1

    def vram_usada_mb(self) -> float | None:
        return self._nvml.ler()[1]

    def _arvore(self) -> list[psutil.Process]:
        try:
            return [self._proc, *self._proc.children(recursive=True)]
        except psutil.Error:
            return [self._proc]

    def _amostrar_recursos(self, t: float) -> None:
        cpu = rss = 0.0
        for p in self._arvore():
            try:
                cpu += p.cpu_percent(None)
                rss += p.memory_info().rss
            except psutil.Error:
                continue
        util, vram = self._nvml.ler()
        self.recursos.append({"t": t, "cpu_processo_pct_de_um_nucleo": cpu, "cpu_processo_pct_do_total": cpu / self.nucleos,
                              "cpu_sistema_pct": psutil.cpu_percent(None), "rss_mb": rss / (1024 * 1024),
                              "gpu_util_pct": util, "vram_usada_mb": vram})

    def iniciar(self) -> None:
        for p in self._arvore():            # primeira chamada de cpu_percent so inicializa o contador
            try:
                p.cpu_percent(None)
            except psutil.Error:
                pass
        psutil.cpu_percent(None)
        self.start()

    def run(self) -> None:
        proxima_res = self._relogio() + self._ir
        while not self._parar.wait(self._ii):
            agora = self._relogio()
            for camera, lista in self._obs.items():
                if lista:
                    t_prod = lista[-1][0]
                    if t_prod is not None:
                        self.idades[camera].append((agora, agora - t_prod))
            if agora >= proxima_res:
                self._amostrar_recursos(agora)
                proxima_res = agora + self._ir

    def parar(self) -> None:
        self._parar.set()
        self.join(timeout=5)
