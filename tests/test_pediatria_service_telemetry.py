import json
import time
from pathlib import Path

from src.jutta_ped.service import telemetry as telemetry_module
from src.jutta_ped.service.telemetry import (
    DiskUsageAccumulator,
    ResourceSampler,
    RunningStat,
    SessionTelemetry,
)


def test_running_stat_tracks_avg_and_peak() -> None:
    stat = RunningStat()
    for value in (10.0, 20.0, 30.0):
        stat.add(value)
    assert stat.count == 3
    assert stat.avg == 20.0
    assert stat.peak == 30.0


def test_running_stat_ignores_negative_values() -> None:
    stat = RunningStat()
    stat.add(-5.0)
    assert stat.count == 0
    assert stat.avg is None


def test_running_stat_snapshot_is_none_when_empty() -> None:
    stat = RunningStat()
    snapshot = stat.snapshot(avg_key="avg_x", peak_key="peak_x")
    assert snapshot == {"avg_x": None, "peak_x": None}


def test_running_stat_snapshot_without_peak_key() -> None:
    stat = RunningStat()
    stat.add(5.0)
    snapshot = stat.snapshot(avg_key="avg_x")
    assert snapshot == {"avg_x": 5.0}


def test_disk_usage_accumulator_sums_categories_and_total() -> None:
    disk = DiskUsageAccumulator()
    disk.record_bytes("evidence_frames_bytes", 100)
    disk.record_bytes("evidence_frames_bytes", 50)
    disk.record_bytes("metadata_bytes", 20)
    snapshot = disk.snapshot()
    assert snapshot["evidence_frames_bytes"] == 150
    assert snapshot["metadata_bytes"] == 20
    assert snapshot["total_bytes_written"] == 170


def test_disk_usage_accumulator_record_text_uses_utf8_byte_length() -> None:
    disk = DiskUsageAccumulator()
    disk.record_text("events_jsonl_bytes", "ção\n")  # multibyte chars
    snapshot = disk.snapshot()
    assert snapshot["events_jsonl_bytes"] == len("ção\n".encode("utf-8"))


def test_disk_usage_accumulator_record_file_reads_actual_size(tmp_path: Path) -> None:
    disk = DiskUsageAccumulator()
    file_path = tmp_path / "frame.jpg"
    file_path.write_bytes(b"0123456789")
    disk.record_file("evidence_frames_bytes", file_path)
    assert disk.snapshot()["evidence_frames_bytes"] == 10


def test_disk_usage_accumulator_record_file_ignores_missing_file(tmp_path: Path) -> None:
    disk = DiskUsageAccumulator()
    disk.record_file("evidence_frames_bytes", tmp_path / "does_not_exist.jpg")
    assert disk.snapshot()["total_bytes_written"] == 0


def test_resource_sampler_disabled_when_psutil_missing(monkeypatch) -> None:
    monkeypatch.setattr(telemetry_module, "psutil", None)
    sampler = ResourceSampler(sample_interval_seconds=0.5)
    assert sampler.enabled is False
    sampler.maybe_sample()  # nao deve levantar mesmo sem psutil
    snapshot = sampler.snapshot()
    assert snapshot["enabled"] is False
    assert snapshot["process_cpu_percent_avg"] is None


def test_resource_sampler_respects_time_gate() -> None:
    sampler = ResourceSampler(sample_interval_seconds=60.0)
    if not sampler.enabled:
        return  # psutil indisponivel neste ambiente; nada a testar aqui
    sampler.maybe_sample()
    first_count = sampler.cpu_percent.count
    sampler.maybe_sample()  # dentro do intervalo -> nao deve amostrar de novo
    assert sampler.cpu_percent.count == first_count


def test_resource_sampler_samples_when_interval_elapsed() -> None:
    sampler = ResourceSampler(sample_interval_seconds=0.5)
    if not sampler.enabled:
        return
    sampler.maybe_sample()
    first_count = sampler.cpu_percent.count
    sampler._last_sample_monotonic = time.monotonic() - 10.0  # forca o intervalo a ter passado
    sampler.maybe_sample()
    assert sampler.cpu_percent.count == first_count + 1
    assert len(sampler.samples) == sampler.cpu_percent.count


def test_session_telemetry_performance_snapshot_computes_effective_fps() -> None:
    session = SessionTelemetry()
    session.process_frame_ms.add(10.0)
    session.process_frame_ms.add(30.0)
    session.started_monotonic = time.monotonic() - 2.0  # sessao "durou" 2s
    snapshot = session.performance_snapshot(frames_processed=20)
    assert snapshot["avg_process_frame_ms"] == 20.0
    assert snapshot["effective_analysis_fps_avg"] == 10.0  # 20 frames / 2s
    assert snapshot["elapsed_seconds"] >= 2.0


def test_session_telemetry_performance_snapshot_merges_extra() -> None:
    session = SessionTelemetry()
    snapshot = session.performance_snapshot(frames_processed=0, extra={"avg_person_detector_ms": 5.0})
    assert snapshot["avg_person_detector_ms"] == 5.0
    assert snapshot["effective_analysis_fps_avg"] == 0.0


def test_session_telemetry_write_resource_samples(tmp_path: Path) -> None:
    session = SessionTelemetry()
    session.resource_sampler.samples = [
        {"timestamp": 1.0, "process_cpu_percent": 5.0, "process_rss_mb": 100.0, "system_cpu_percent": 10.0}
    ]
    out_path = tmp_path / "resource_samples.jsonl"
    session.write_resource_samples(out_path)
    lines = out_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0])["process_cpu_percent"] == 5.0


def test_session_telemetry_write_resource_samples_skips_empty(tmp_path: Path) -> None:
    session = SessionTelemetry()
    out_path = tmp_path / "resource_samples.jsonl"
    session.write_resource_samples(out_path)
    assert not out_path.exists()
