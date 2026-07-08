from __future__ import annotations

import json
import time
from collections import Counter
from pathlib import Path
from typing import Any

from modulo.pediatria.metrics import RunningStat

__all__ = [
    "RunningStat",
    "DiskUsageAccumulator",
    "ResourceSampler",
    "SessionTelemetry",
]

try:
    import psutil
except ImportError:  # pragma: no cover - degrada sem travar a sessao
    psutil = None  # type: ignore[assignment]


class DiskUsageAccumulator:
    """Bytes reais escritos em disco pela sessao, por categoria.

    So conta o que o proprio codigo confirma ter escrito com sucesso (sem
    varrer diretorio nem estimar por aproximacao).
    """

    def __init__(self) -> None:
        self._bytes: Counter[str] = Counter()

    def record_bytes(self, category: str, num_bytes: int) -> None:
        if num_bytes > 0:
            self._bytes[category] += num_bytes

    def record_file(self, category: str, path: Path) -> None:
        try:
            size = path.stat().st_size
        except OSError:
            return
        self.record_bytes(category, size)

    def record_text(self, category: str, text: str) -> None:
        self.record_bytes(category, len(text.encode("utf-8")))

    def snapshot(self) -> dict[str, int]:
        data = {key: int(value) for key, value in self._bytes.items()}
        data["total_bytes_written"] = sum(data.values())
        return data


class ResourceSampler:
    """Amostragem periodica (por tempo, nao por frame) de CPU/RAM do processo.

    `psutil.Process.cpu_percent(interval=None)` e nao-bloqueante: calcula a
    porcentagem a partir do delta desde a ultima chamada, entao mesmo
    chamando a cada poucos segundos o custo de cada amostra e minimo (nao ha
    sleep/blocking dentro do loop de analise).
    """

    def __init__(self, sample_interval_seconds: float = 2.0) -> None:
        self.sample_interval_seconds = max(0.5, float(sample_interval_seconds))
        self._process = psutil.Process() if psutil is not None else None
        self._last_sample_monotonic: float | None = None
        self.cpu_percent = RunningStat()
        self.rss_mb = RunningStat()
        self.system_cpu_percent = RunningStat()
        self.samples: list[dict[str, Any]] = []
        self._enabled = self._process is not None
        if self._enabled:
            try:
                self._process.cpu_percent(interval=None)  # prime o contador interno
                psutil.cpu_percent(interval=None)
            except Exception:
                self._enabled = False

    @property
    def enabled(self) -> bool:
        return self._enabled

    def maybe_sample(self) -> None:
        """So faz o trabalho real se o intervalo de amostragem ja passou."""
        if not self._enabled:
            return
        now = time.monotonic()
        if (
            self._last_sample_monotonic is not None
            and (now - self._last_sample_monotonic) < self.sample_interval_seconds
        ):
            return
        self._last_sample_monotonic = now
        try:
            cpu = self._process.cpu_percent(interval=None)
            rss_mb = self._process.memory_info().rss / (1024 * 1024)
            system_cpu = psutil.cpu_percent(interval=None)
        except Exception:
            self._enabled = False
            return
        self.cpu_percent.add(cpu)
        self.rss_mb.add(rss_mb)
        self.system_cpu_percent.add(system_cpu)
        self.samples.append(
            {
                "timestamp": time.time(),
                "process_cpu_percent": round(cpu, 2),
                "process_rss_mb": round(rss_mb, 2),
                "system_cpu_percent": round(system_cpu, 2),
            }
        )

    def snapshot(self) -> dict[str, Any]:
        return {
            "enabled": self._enabled,
            "sample_interval_seconds": self.sample_interval_seconds,
            "samples_collected": self.cpu_percent.count,
            "process_cpu_percent_avg": round(self.cpu_percent.avg, 2) if self.cpu_percent.avg is not None else None,
            "process_cpu_percent_peak": round(self.cpu_percent.peak, 2) if self.cpu_percent.count else None,
            "process_rss_mb_avg": round(self.rss_mb.avg, 2) if self.rss_mb.avg is not None else None,
            "process_rss_mb_peak": round(self.rss_mb.peak, 2) if self.rss_mb.count else None,
            "system_cpu_percent_avg": (
                round(self.system_cpu_percent.avg, 2) if self.system_cpu_percent.avg is not None else None
            ),
        }


class SessionTelemetry:
    """Telemetria operacional leve de uma sessao (recursos + disco + tempos).

    Pensada para ser chamada dentro do loop existente sem adicionar overhead
    por frame: `sample_resources()` e barato de chamar toda iteracao porque
    o `ResourceSampler` decide internamente, por tempo, se vale a pena medir
    de verdade.
    """

    def __init__(self, *, sample_interval_seconds: float = 2.0) -> None:
        self.resource_sampler = ResourceSampler(sample_interval_seconds=sample_interval_seconds)
        self.disk_usage = DiskUsageAccumulator()
        self.process_frame_ms = RunningStat()
        self.capture_loop_ms = RunningStat()
        self.render_frame_ms = RunningStat()
        self.started_monotonic = time.monotonic()

    def sample_resources(self) -> None:
        self.resource_sampler.maybe_sample()

    def elapsed_seconds(self) -> float:
        return max(1e-6, time.monotonic() - self.started_monotonic)

    def performance_snapshot(self, *, frames_processed: int, extra: dict[str, Any] | None = None) -> dict[str, Any]:
        elapsed = self.elapsed_seconds()
        data: dict[str, Any] = {
            **self.process_frame_ms.snapshot(avg_key="avg_process_frame_ms", peak_key="peak_process_frame_ms"),
            **self.capture_loop_ms.snapshot(avg_key="avg_capture_loop_ms", peak_key="peak_capture_loop_ms"),
            **self.render_frame_ms.snapshot(avg_key="avg_render_frame_ms", peak_key="peak_render_frame_ms"),
            "effective_analysis_fps_avg": round(frames_processed / elapsed, 3) if frames_processed else 0.0,
            "elapsed_seconds": round(elapsed, 2),
        }
        if extra:
            data.update(extra)
        return data

    def resources_snapshot(self) -> dict[str, Any]:
        return self.resource_sampler.snapshot()

    def disk_usage_snapshot(self) -> dict[str, Any]:
        return self.disk_usage.snapshot()

    def write_resource_samples(self, path: Path) -> None:
        if not self.resource_sampler.samples:
            return
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("w", encoding="utf-8") as handle:
                for sample in self.resource_sampler.samples:
                    handle.write(json.dumps(sample, ensure_ascii=False) + "\n")
        except OSError:
            pass
