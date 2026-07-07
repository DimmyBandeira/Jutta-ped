"""Popup de alerta pediatrico local, isolado do WebGuardiao principal."""
from __future__ import annotations

import time

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QPixmap
from PyQt6.QtWidgets import (
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
)


class AlertCooldownGate:
    """Impede spam de alertas repetidos: 1 alerta por camera_id a cada cooldown_seconds."""

    def __init__(self, cooldown_seconds: float = 120.0) -> None:
        self._cooldown = cooldown_seconds
        self._last: dict[str, float] = {}

    def allowed(self, camera_id: str) -> bool:
        return time.monotonic() - self._last.get(camera_id, 0.0) >= self._cooldown

    def mark_sent(self, camera_id: str) -> None:
        self._last[camera_id] = time.monotonic()

    def seconds_until_next(self, camera_id: str) -> float:
        return max(0.0, self._cooldown - (time.monotonic() - self._last.get(camera_id, 0.0)))


class AlertEventLatch:
    """Emite uma vez por episodio e respeita cooldown global por camera."""

    def __init__(self, cooldown_seconds: float = 120.0) -> None:
        self._cooldown = cooldown_seconds
        self._claimed_cameras: set[str] = set()
        self._last_by_camera: dict[str, float] = {}
        self._last_by_camera_track: dict[tuple[str, int], float] = {}

    def claim(self, camera_id: str, alert_state: str, track_id: int) -> bool:
        del alert_state
        if camera_id in self._claimed_cameras:
            return False
        key = (camera_id, track_id)
        now = time.monotonic()
        last_camera_alert = self._last_by_camera.get(camera_id)
        if (
            last_camera_alert is not None
            and now - last_camera_alert < self._cooldown
        ):
            return False
        last_track_alert = self._last_by_camera_track.get(key)
        if last_track_alert is not None and now - last_track_alert < self._cooldown:
            return False
        self._claimed_cameras.add(camera_id)
        self._last_by_camera[camera_id] = now
        self._last_by_camera_track[key] = now
        return True

    def resolve_camera(self, camera_id: str) -> None:
        self._claimed_cameras.discard(camera_id)

    def clear(self) -> None:
        self._claimed_cameras.clear()
        self._last_by_camera.clear()
        self._last_by_camera_track.clear()


class PediatriaAlertPopup(QDialog):
    """Janela local exibida para evidencia persistente de desacompanhamento."""

    _STYLE = """
        QDialog {
            background-color: #120000;
        }
        QLabel#title {
            color: #FF4040;
            font-size: 17px;
            font-weight: bold;
        }
        QLabel#info {
            color: #EEEEEE;
            font-size: 13px;
            line-height: 160%;
        }
        QLabel#badge {
            color: #666666;
            font-size: 10px;
        }
        QPushButton {
            background-color: #CC2222;
            color: #FFFFFF;
            border: none;
            padding: 10px 36px;
            font-size: 13px;
            font-weight: bold;
            border-radius: 4px;
            min-width: 180px;
        }
        QPushButton:hover {
            background-color: #EE3333;
        }
    """

    def __init__(
        self,
        parent=None,
        *,
        camera_id: str = "Pediatria Local",
        track_id: int = 0,
        confidence: float = 0.0,
        alert_state: str = "CHILD_ALONE",
        evidence_pixmap: QPixmap | None = None,
        crop_pixmap: QPixmap | None = None,
    ) -> None:
        super().__init__(parent)
        self.camera_id = camera_id
        self.track_id = track_id
        self.confidence = confidence
        self.alert_state = alert_state
        self.evidence_pixmap = evidence_pixmap
        self.crop_pixmap = crop_pixmap
        self._build_ui()

    def _build_ui(self) -> None:
        from datetime import datetime

        self.setWindowTitle("ATENCAO PEDIATRIA")
        self.setWindowFlags(
            Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.Dialog
        )
        self.setMinimumWidth(
            720 if self.evidence_pixmap is not None or self.crop_pixmap is not None else 420
        )
        self.setStyleSheet(self._STYLE)

        layout = QVBoxLayout(self)
        layout.setSpacing(16)
        layout.setContentsMargins(32, 32, 32, 28)

        title_text = (
            "ATENCAO: CRIANÇA SE AFASTANDO DO ACOMPANHANTE"
            if self.alert_state == "CHILD_SEPARATED"
            else "ATENCAO: POSSIVEL CRIANÇA DESACOMPANHADA"
        )
        title = QLabel(title_text)
        title.setObjectName("title")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(title)

        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet("background-color: #882222; max-height: 1px;")
        layout.addWidget(sep)

        evidence_row = QHBoxLayout()
        if self.evidence_pixmap is not None and not self.evidence_pixmap.isNull():
            evidence = self._image_label(
                self.evidence_pixmap,
                "Cena com evidencia",
                420,
                236,
            )
            evidence_row.addWidget(evidence)
        if self.crop_pixmap is not None and not self.crop_pixmap.isNull():
            crop = self._image_label(
                self.crop_pixmap,
                "Pessoa em analise",
                220,
                236,
            )
            evidence_row.addWidget(crop)
        if evidence_row.count():
            layout.addLayout(evidence_row)

        info = QLabel(
            f"  Camera:      {self.camera_id}\n"
            f"  Evidencia:   pessoa em analise #{self.track_id}\n"
            f"  Horario:     {datetime.now().strftime('%H:%M:%S')}\n"
            f"  Confianca:   {self.confidence:.0%}\n\n"
            "  Verifique a cena antes de tomar qualquer acao."
        )
        info.setObjectName("info")
        layout.addWidget(info)

        btn = QPushButton("Confirmar visualizacao")
        btn.clicked.connect(self.accept)
        layout.addWidget(btn, alignment=Qt.AlignmentFlag.AlignCenter)

        badge = QLabel("DIAGNOSTICO LOCAL - nao conectado ao WebGuardiao")
        badge.setObjectName("badge")
        badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(badge)

    @staticmethod
    def _image_label(
        pixmap: QPixmap,
        accessible_name: str,
        width: int,
        height: int,
    ) -> QLabel:
        label = QLabel()
        label.setObjectName("evidence")
        label.setAccessibleName(accessible_name)
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        label.setMinimumSize(width, height)
        label.setStyleSheet(
            "background-color: #050505; border: 1px solid #772222;")
        label.setPixmap(
            pixmap.scaled(
                width,
                height,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        )
        return label
