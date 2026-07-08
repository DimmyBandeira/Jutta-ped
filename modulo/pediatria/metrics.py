from __future__ import annotations


class RunningStat:
    """Media/pico em O(1) de memoria: nunca guarda o historico completo.

    Usado pela telemetria por sessao (latencia de loop, latencia de frame,
    tempo do detector/especialista etc.) para nao crescer sem limite numa
    sessao longa. Fica no core (`modulo.pediatria`) porque e usado tanto por
    componentes do pipeline (ex.: `PersonCropSpecialistPipeline`) quanto pela
    camada de servico (`src/jutta_ped/service/telemetry.py`), que so
    reexporta esta classe.
    """

    __slots__ = ("count", "total", "peak")

    def __init__(self) -> None:
        self.count = 0
        self.total = 0.0
        self.peak = 0.0

    def add(self, value: float) -> None:
        if value < 0:
            return
        self.count += 1
        self.total += value
        if value > self.peak:
            self.peak = value

    @property
    def avg(self) -> float | None:
        return (self.total / self.count) if self.count else None

    def snapshot(self, *, avg_key: str, peak_key: str | None = None) -> dict[str, float | None]:
        data: dict[str, float | None] = {
            avg_key: round(self.avg, 3) if self.avg is not None else None,
        }
        if peak_key is not None:
            data[peak_key] = round(self.peak, 3) if self.count else None
        return data
