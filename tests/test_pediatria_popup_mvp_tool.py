from collections import Counter
from pathlib import Path
from types import SimpleNamespace

import pytest
import numpy as np

from tools.run_pediatria_popup_mvp import (
    PediatriaDatasetCollector,
    PediatricIdentityStabilizer,
    PediatriaPopupDemo,
    SOURCE_FILE,
    SOURCE_URL,
    SOURCE_USB,
    WeakChildCandidatePromoter,
    dedupe_detections_by_track,
    draw_popup_alert_bbox,
    resolve_capture_source,
    select_popup_evidence_detection,
    source_display_name,
)
from modulo.pediatria.detector_mvp import PediatricDetection, PediatricsDetectorMvpRunner
from src.jutta_ped.service.telemetry import SessionTelemetry


def test_popup_demo_defaults_to_v6_jutta_and_is_standalone() -> None:
    source = Path("tools/run_pediatria_popup_mvp.py").read_text(encoding="utf-8")

    assert "pediatria_child_detector_v6_jutta_openvino_model" in source
    assert "pediatria_child_detector_v6_jutta.pt" in source
    assert '"publishes_alerts": False' in source
    assert "AlertDispatcher" not in source
    assert "ChromeBridge" not in source


def test_detector_runner_accepts_openvino_model_directory(tmp_path: Path) -> None:
    model_dir = tmp_path / "pediatria_child_detector_v6_jutta_openvino_model"
    model_dir.mkdir()
    (model_dir / "pediatria_child_detector_v6_jutta.xml").write_text("<xml />")
    (model_dir / "pediatria_child_detector_v6_jutta.bin").write_bytes(b"bin")

    class FakeModel:
        names = {0: "adult", 1: "child"}

    runner = PediatricsDetectorMvpRunner(
        model_dir,
        model_loader=lambda path: FakeModel(),
    )

    assert runner.model_path == model_dir
    assert runner.names == {0: "adult", 1: "child"}


def test_resolve_capture_source_supports_file_url_and_usb(tmp_path: Path) -> None:
    video = tmp_path / "demo.mp4"
    video.write_bytes(b"demo")

    assert resolve_capture_source(SOURCE_FILE, str(video), 0) == str(video)
    assert resolve_capture_source(SOURCE_URL, "rtsp://camera/stream", 0) == (
        "rtsp://camera/stream"
    )
    assert resolve_capture_source(SOURCE_USB, "", 2) == 2


def test_resolve_capture_source_rejects_invalid_input(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="Arquivo nao encontrado"):
        resolve_capture_source(SOURCE_FILE, str(tmp_path / "missing.mp4"), 0)
    with pytest.raises(ValueError, match="URL"):
        resolve_capture_source(SOURCE_URL, "camera.local", 0)


def test_source_display_name_hides_remote_credentials() -> None:
    assert source_display_name(1) == "camera_usb_1"
    assert source_display_name("rtsp://user:secret@camera/stream") == "camera_remota"
    assert source_display_name("storage/videos/demo.mp4") == "demo"


def test_report_never_serializes_raw_remote_url() -> None:
    source = Path("tools/run_pediatria_popup_mvp.py").read_text(encoding="utf-8")

    assert '"source": source_display_name(' in source


def test_popup_evidence_prefers_visible_child_over_stale_alert_track() -> None:
    stale_parent = PediatricDetection((700.0, 100.0, 880.0, 540.0), "adult", 0.56, 5)
    visible_child = PediatricDetection((480.0, 260.0, 650.0, 710.0), "child", 0.78, 153)
    small_child = PediatricDetection((200.0, 250.0, 285.0, 465.0), "child", 0.70, 100)

    selected = select_popup_evidence_detection(
        [stale_parent, visible_child, small_child],
        {5},
    )

    assert selected == visible_child


def test_popup_evidence_keeps_strong_linked_track_despite_role_flicker() -> None:
    linked_child_flicker = PediatricDetection(
        (556.0, 249.0, 706.0, 697.0), "adult", 0.77, 21
    )
    unrelated_child = PediatricDetection((0.0, 261.0, 128.0, 523.0), "child", 0.38, 73)

    selected = select_popup_evidence_detection(
        [linked_child_flicker, unrelated_child],
        {21},
    )

    assert selected == linked_child_flicker


def test_popup_evidence_prefers_alert_track_when_it_is_still_child() -> None:
    linked_child = PediatricDetection((20.0, 20.0, 80.0, 120.0), "child", 0.65, 7)
    larger_child = PediatricDetection((100.0, 100.0, 300.0, 500.0), "child", 0.80, 153)

    selected = select_popup_evidence_detection([linked_child, larger_child], {7})

    assert selected == linked_child


def test_dedupe_detections_by_track_keeps_highest_confidence() -> None:
    weak = PediatricDetection((0.0, 0.0, 10.0, 10.0), "child", 0.40, 8)
    strong = PediatricDetection((0.0, 0.0, 20.0, 20.0), "child", 0.80, 8)
    adult = PediatricDetection((50.0, 50.0, 90.0, 120.0), "adult", 0.70, 2)

    deduped = dedupe_detections_by_track([weak, adult, strong])

    assert len(deduped) == 2
    assert strong in deduped
    assert adult in deduped


def test_popup_alert_bbox_uses_fixed_alert_color_without_mutating_source(
    monkeypatch,
) -> None:
    import tools.run_pediatria_popup_mvp as popup_tool

    frame = np.zeros((80, 120, 3), dtype=np.uint8)
    original = frame.copy()
    calls = []

    def fake_rectangle(image, point_1, point_2, color, thickness):
        calls.append((point_1, point_2, color, thickness))
        return image

    monkeypatch.setattr(popup_tool.cv2, "rectangle", fake_rectangle, raising=False)

    evidence = draw_popup_alert_bbox(frame.copy(), (10.0, 12.0, 70.0, 60.0))

    assert np.array_equal(frame, original)
    assert evidence.shape == frame.shape
    assert calls[0] == ((10, 12), (70, 60), (0, 80, 255), 4)


def test_identity_stabilizer_merges_child_track_by_palette_and_position() -> None:
    stabilizer = PediatricIdentityStabilizer(ttl_frames=30)
    frame = np.zeros((240, 320, 3), dtype=np.uint8)
    frame[70:170, 90:150] = (220, 40, 20)
    frame[76:176, 98:158] = (222, 42, 18)

    first = stabilizer.stabilize(
        [PediatricDetection((80.0, 50.0, 160.0, 190.0), "child", 0.81, 20)],
        frame,
        frame_index=1,
    )
    second = stabilizer.stabilize(
        [PediatricDetection((88.0, 56.0, 168.0, 196.0), "child", 0.72, 45)],
        frame,
        frame_index=8,
    )

    assert first[0].track_id == 20
    assert second[0].track_id == 20
    assert stabilizer.last_merges[0].original_track_id == 45
    assert stabilizer.last_merges[0].stable_track_id == 20
    assert stabilizer.last_merges[0].dominant_color == "blue"


def test_identity_stabilizer_can_promote_uncertain_reappearance() -> None:
    stabilizer = PediatricIdentityStabilizer(ttl_frames=30)
    frame = np.zeros((240, 320, 3), dtype=np.uint8)
    frame[70:170, 90:150] = (220, 40, 20)
    frame[76:176, 98:158] = (222, 42, 18)

    stabilizer.stabilize(
        [PediatricDetection((80.0, 50.0, 160.0, 190.0), "child", 0.81, 20)],
        frame,
        frame_index=1,
    )
    reappeared = stabilizer.stabilize(
        [PediatricDetection((88.0, 56.0, 168.0, 196.0), "uncertain", 0.08, 45)],
        frame,
        frame_index=8,
    )

    assert reappeared[0].track_id == 20
    assert reappeared[0].role == "child"
    assert reappeared[0].confidence >= 0.60


def test_weak_child_promoter_promotes_persistent_low_confidence_child() -> None:
    promoter = WeakChildCandidatePromoter(min_hits=3)
    weak = PediatricDetection((100.0, 100.0, 160.0, 260.0), "child", 0.04, 7)

    assert promoter.promote([weak], [], frame_index=1) == []
    assert promoter.promote([weak], [], frame_index=2) == []
    promoted = promoter.promote([weak], [], frame_index=3)

    assert len(promoted) == 1
    assert promoted[0].role == "child"
    assert promoted[0].track_id == 200000
    assert promoted[0].confidence >= 0.62
    assert promoter.last_promotions[0].original_track_id == 7


def test_weak_child_promoter_blocks_child_near_adult_candidate() -> None:
    promoter = WeakChildCandidatePromoter(min_hits=2)
    weak = PediatricDetection((100.0, 100.0, 160.0, 260.0), "child", 0.04, 7)
    adult = PediatricDetection((96.0, 90.0, 170.0, 280.0), "adult", 0.08, 2)

    assert promoter.promote([weak], [adult], frame_index=1) == []
    assert promoter.promote([weak], [adult], frame_index=2) == []
    assert promoter.last_promotions == []


def test_presentation_window_exposes_source_controls(qtbot, tmp_path: Path) -> None:
    window = PediatriaPopupDemo(
        model=tmp_path / "model.pt",
        report=tmp_path / "report.json",
        confidence=0.25,
        cooldown_seconds=120.0,
        device="cpu",
    )
    qtbot.addWidget(window)

    assert window.source_mode.count() == 3
    assert window.start_button.text() == "Iniciar"
    assert window.stop_button.isEnabled() is False
    assert window.force_cpu_checkbox.isChecked() is False
    assert window.person_crop_checkbox.isChecked() is True
    assert window.model_value.text()
    assert window.model_value.isVisible() is False
    assert window.person_model_value.text()
    assert window.person_model_value.isVisible() is False
    assert window.person_crop_checkbox.isVisible() is False
    assert window.weak_child_checkbox.isVisible() is False
    assert window.video.minimumWidth() == 720
    assert window.video.minimumHeight() == 420
    assert window.weak_child_checkbox.isChecked() is True
    assert window.collect_dataset_checkbox.isChecked() is False

    window.source_mode.setCurrentText(SOURCE_URL)
    assert window.source_value.isEnabled() is True
    assert window.browse_button.isEnabled() is False

    window.source_mode.setCurrentText(SOURCE_USB)
    assert window.usb_index.isEnabled() is True
    assert window.source_value.isEnabled() is False


def test_presentation_window_can_start_with_force_cpu_checked(
    qtbot,
    tmp_path: Path,
) -> None:
    window = PediatriaPopupDemo(
        model=tmp_path / "model.pt",
        report=tmp_path / "report.json",
        confidence=0.25,
        cooldown_seconds=120.0,
        device="cuda:0",
        force_cpu_default=True,
    )
    qtbot.addWidget(window)

    assert window.force_cpu_checkbox.isChecked() is True
    assert window.requested_device == "cpu"
    assert window.device == "cpu"


def test_dataset_collector_writes_review_manifest(tmp_path: Path) -> None:
    class State:
        raw_state = "UNCERTAIN"
        stable_state = "UNCERTAIN"
        reason = "teste"
        children = []

    collector = PediatriaDatasetCollector(
        root_dir=tmp_path / "collection",
        source_name="camera_teste",
        model_path=tmp_path / "model.pt",
        requested_device="cpu",
        resolved_device="cpu",
        device_reason="teste",
        sample_interval_frames=1,
        max_crops_per_track=2,
    )
    frame = np.zeros((120, 160, 3), dtype=np.uint8)
    detection = PediatricDetection((10.0, 20.0, 80.0, 100.0), "child", 0.88, 7)

    collector.collect(
        frame_index=1,
        clean_frame=frame,
        detections=[detection],
        state=State(),
    )

    summary = collector.summary()
    assert summary["saved_frames"] == 1
    assert summary["saved_crops"] == 1
    assert collector.manifest_path.is_file()
    record = collector.manifest_path.read_text(encoding="utf-8")
    assert '"predicted_role": "child"' in record
    assert '"human_label": null' in record
    assert '"training_eligible": false' in record


def test_event_evidence_writes_frame_metadata_and_session_summary(tmp_path: Path) -> None:
    demo = PediatriaPopupDemo.__new__(PediatriaPopupDemo)
    demo.session_id = "20260707_120000"
    demo.evidence_dir = tmp_path / "evidence" / "sessions" / demo.session_id
    demo.events_log_path = demo.evidence_dir / "events.jsonl"
    demo.frame_index = 42
    demo.requested_device = "auto"
    demo.device = "cpu"
    demo.device_reason = "test"
    demo.model_path = Path("src/models/pediatria_child_detector_v6_jutta_openvino_model")
    demo.person_model_path = Path("src/models/yolo11n_openvino_model")
    demo.dataset_collector = None
    demo.crop_pipeline = None
    demo.telemetry = SessionTelemetry()
    demo.session_event_counts = Counter()
    demo.session_status_counts = Counter()
    demo.session_suppression_counts = Counter()
    demo.session_evidence_error_counts = Counter()
    demo.session_person_detector_zero = 0
    demo.states = Counter({"UNCERTAIN": 1})
    demo.latencies = [10.0]
    demo.popup_count = 0
    demo.current_source = "camera_teste"

    state = SimpleNamespace(
        raw_state="UNCERTAIN",
        stable_state="UNCERTAIN",
        reason="Sem evidencia infantil ou adulta suficiente.",
        children=[],
    )
    frame = np.zeros((80, 120, 3), dtype=np.uint8)

    evidence_paths, evidence_errors = demo._save_event_evidence(
        event_type="frame_diagnostic",
        source_name="camera_teste",
        state=state,
        child=None,
        detections=[],
        person_detections=[],
        clean_frame=frame,
        popup_emitido=False,
        suppression_reason=None,
    )
    demo._append_event_log(
        "frame_diagnostic",
        source_name="camera_teste",
        state=state,
        child=None,
        detections=[],
        person_detections=[],
        evidence_paths=evidence_paths,
        evidence_errors=evidence_errors,
    )
    summary = demo._build_session_summary()
    demo._write_session_summary_files(summary)

    assert Path(evidence_paths["frame"]).is_file()
    assert Path(evidence_paths["annotated"]).is_file()
    assert Path(evidence_paths["metadata"]).is_file()
    assert "crop" not in evidence_paths
    assert "crop:no_detection" in evidence_errors
    assert summary["total_sem_pessoa"] == 1
    assert summary["total_person_detector_zero"] == 1
    assert (demo.evidence_dir / "session_summary.json").is_file()
    assert (demo.evidence_dir / "session_summary.csv").is_file()

