from pathlib import Path

from src.jutta_ped.service.runtime import (
    DEFAULT_REPORT_DIR,
    SessionStartConfig,
    redact_source_value,
    source_display_name,
)


def test_runtime_config_defaults_do_not_require_ui() -> None:
    assert SessionStartConfig.report_dir == DEFAULT_REPORT_DIR
    config = SessionStartConfig(source="rtsp://user:secret@camera.local/stream")
    assert config.force_cpu is False
    assert config.modo_coleta is False


def test_runtime_redacts_camera_credentials() -> None:
    assert redact_source_value("rtsp://user:secret@camera.local:554/live") == "rtsp://camera.local:554/live"
    assert source_display_name("rtsp://user:secret@camera.local/live") == "camera_remota"
    assert source_display_name(0) == "camera_usb_0"
