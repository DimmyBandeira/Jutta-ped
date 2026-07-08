import json
from collections import Counter
from pathlib import Path
from types import SimpleNamespace

import pytest
import numpy as np
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QLabel, QPushButton

import src.jutta_ped.ui.demo_viewer as demo_viewer_module
from src.jutta_ped.ui.demo_viewer import (
    PediatriaDatasetCollector,
    PediatriaPopupDemo,
    SOURCE_FILE,
    SOURCE_URL,
    SOURCE_USB,
    dedupe_detections_by_track,
    draw_popup_alert_bbox,
    resolve_capture_source,
    select_popup_evidence_detection,
    source_display_name,
)
from modulo.pediatria.detector_mvp import PediatricDetection, PediatricsDetectorMvpRunner
from modulo.pediatria.mvp_popup import PediatriaAlertPopup
from modulo.pediatria.identity_stabilizer import PediatricIdentityStabilizer
from modulo.pediatria.weak_child_promoter import WeakChildCandidatePromoter
from src.jutta_ped.service.telemetry import SessionTelemetry


def test_popup_demo_defaults_to_v6_jutta_and_is_standalone() -> None:
    source = Path("src/jutta_ped/ui/demo_viewer.py").read_text(encoding="utf-8")

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
    source = Path("src/jutta_ped/ui/demo_viewer.py").read_text(encoding="utf-8")

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
    import src.jutta_ped.ui.demo_viewer as popup_tool

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

    assert window.stack.currentIndex() == 0
    assert window.windowFlags() & Qt.WindowType.FramelessWindowHint
    assert window.testAttribute(Qt.WidgetAttribute.WA_TranslucentBackground) is True
    assert "loginShell" in window.login_page.styleSheet()
    assert window.login_drag_header.objectName() == "windowHeader"
    assert window.login_minimize_button.text() == "-"
    assert window.login_close_button.text() == "X"
    assert window.login_button.text() == "Entrar"
    assert window.demo_access_button.text() == "Acesso Rapido (Demo)"
    assert window.back_to_login_button.text() == "Sair"
    assert window.service_status_label.text() == "API: verificando..."
    assert window.service_button.text() == "Ativar API"

    window.login_user_value.setText("admin")
    window.login_password_value.setText("errada")
    window._login_to_viewer()
    assert window.stack.currentIndex() == 0
    assert window.login_error_label.isHidden() is False
    assert "admin/admin" in window.login_error_label.text()

    window.login_password_value.setText("admin")
    window._login_to_viewer()
    assert window.stack.currentIndex() == 1
    assert window.login_error_label.isHidden() is True

    window._logout_to_login()
    assert window.stack.currentIndex() == 0
    assert window.login_password_value.text() == ""

    window._enter_viewer()
    assert window.stack.currentIndex() == 1

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


def test_alert_popup_uses_demo_viewer_theme(qtbot) -> None:
    popup = PediatriaAlertPopup(
        camera_id="Camera Social",
        track_id=7,
        confidence=0.82,
        alert_state="CHILD_ALONE",
    )
    qtbot.addWidget(popup)

    assert popup.windowTitle() == "WebGuardiao - IA Pediatria"
    assert "popupCard" in popup.styleSheet()
    assert "Possivel crianca desacompanhada" in popup.findChild(QLabel, "title").text()
    buttons = popup.findChildren(QPushButton)
    assert buttons[0].text() == "Entendi, fechar alerta"

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


def _make_bare_viewer_for_reconnect_tests(tmp_path: Path) -> PediatriaPopupDemo:
    """Instancia PediatriaPopupDemo sem __init__ (sem Qt de verdade) para
    testar a logica de reconexao de _next_frame isoladamente. self.status e
    self._reconnect_timer sao stand-ins sem Qt real: nao dependem do widget
    ter sido construido, e _attempt_reconnect_capture so precisa dos metodos
    setText/start/stop -- ver nota de RuntimeError em instancias PyQt6 bare
    (mesmo padrao ja usado em test_event_evidence_writes_frame_metadata_and_session_summary).
    """
    demo = PediatriaPopupDemo.__new__(PediatriaPopupDemo)
    demo.session_id = "20260707_120000"
    demo.evidence_dir = tmp_path / "evidence" / "sessions" / demo.session_id
    demo.events_log_path = demo.evidence_dir / "events.jsonl"
    demo.device = "cpu"
    demo.telemetry = SessionTelemetry()
    demo.status = SimpleNamespace(setText=lambda text: None)
    demo._reconnect_timer = SimpleNamespace(start=lambda ms: None, stop=lambda: None)
    demo._reconnecting = False
    demo._reconnect_attempts = 0
    demo._reconnect_max_attempts = 5
    demo._reconnect_backoff_seconds = 2.0
    demo._reconnect_backoff_max_seconds = 30.0
    demo.capture = None
    demo.current_source = None
    return demo


def _read_jsonl_events(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def test_begin_reconnect_releases_capture_and_logs_event(tmp_path: Path) -> None:
    """Antes desta correcao, capture.read()==False numa fonte de rede/USB
    chamava stop_analysis() direto -- exigindo clicar 'Iniciar' de novo.
    Agora o primeiro passo e liberar o capture antigo e entrar em modo de
    reconexao, sem encerrar a analise."""

    class FakeCapture:
        def __init__(self) -> None:
            self.released = False

        def release(self) -> None:
            self.released = True

    demo = _make_bare_viewer_for_reconnect_tests(tmp_path)
    capture = FakeCapture()
    demo.capture = capture
    demo.current_source = "rtsp://camera.local/stream"

    demo._begin_reconnect()

    assert capture.released is True
    assert demo.capture is None
    assert demo._reconnecting is True
    events = _read_jsonl_events(demo.events_log_path)
    assert [item["event_type"] for item in events] == ["reconnect_started"]


def test_attempt_reconnect_capture_recovers_without_stopping(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    demo = _make_bare_viewer_for_reconnect_tests(tmp_path)
    demo.current_source = "rtsp://camera.local/stream"
    demo._reconnecting = True
    demo._reconnect_attempts = 0

    class RecoveredCapture:
        def isOpened(self) -> bool:
            return True

        def read(self):
            return True, np.zeros((4, 4, 3), dtype=np.uint8)

        def release(self) -> None:
            pass

    monkeypatch.setattr(demo_viewer_module, "open_capture_source", lambda source: RecoveredCapture())

    demo._attempt_reconnect_capture()

    assert demo._reconnecting is False
    assert demo.capture is not None
    events = _read_jsonl_events(demo.events_log_path)
    assert [item["event_type"] for item in events] == ["reconnected"]


def test_reconnect_gives_up_after_max_attempts_and_stops_analysis(qtbot, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    window = PediatriaPopupDemo(
        model=tmp_path / "model.pt",
        report=tmp_path / "report.json",
        confidence=0.25,
        cooldown_seconds=120.0,
        device="cpu",
    )
    qtbot.addWidget(window)
    window.current_source = "rtsp://camera.local/stream"
    window._reconnecting = True
    window._reconnect_attempts = 0
    window._reconnect_max_attempts = 2
    window._reconnect_backoff_seconds = 0.01

    class DeadCapture:
        def isOpened(self) -> bool:
            return False

        def read(self):
            return False, None

        def release(self) -> None:
            pass

    monkeypatch.setattr(demo_viewer_module, "open_capture_source", lambda source: DeadCapture())

    window._attempt_reconnect_capture()
    assert window._reconnecting is True
    assert window._reconnect_attempts == 1

    window._attempt_reconnect_capture()
    assert window._reconnecting is False
    assert window._reconnect_attempts == 2
    assert window.current_source is None
    assert window.start_button.isEnabled() is True
    assert window.stop_button.isEnabled() is False






