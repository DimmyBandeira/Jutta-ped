"""Popup de alerta pediatrico local, isolado do WebGuardiao principal."""
from __future__ import annotations

import time

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QPixmap
from PyQt6.QtWidgets import (
    QDialog,
    QFrame,
    QGraphicsDropShadowEffect,
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
            background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                stop:0 #020814, stop:0.55 #061322, stop:1 #020814);
            color: #eef5ff;
            font-family: Segoe UI, Arial, sans-serif;
        }
        QFrame#popupCard {
            background: rgba(4, 14, 28, 230);
            border: 1px solid rgba(84, 150, 214, 170);
            border-radius: 18px;
        }
        QLabel#brand {
            color: #f5f8ff;
            font-size: 18px;
            font-weight: 900;
        }
        QLabel#moduleBadge {
            color: #13aaff;
            border: 1px solid rgba(0, 157, 255, 120);
            border-radius: 13px;
            padding: 5px 10px;
            background: rgba(0, 120, 255, 25);
            font-weight: 700;
        }
        QLabel#severity {
            color: #ffcc66;
            background: rgba(255, 159, 28, 32);
            border: 1px solid rgba(255, 190, 90, 130);
            border-radius: 10px;
            padding: 6px 10px;
            font-size: 12px;
            font-weight: 800;
        }
        QLabel#title {
            color: #f5f8ff;
            font-size: 21px;
            font-weight: 900;
        }
        QLabel#subtitle {
            color: #b9c6d6;
            font-size: 13px;
        }
        QLabel#info {
            color: #dfeaf6;
            background: rgba(2, 10, 22, 150);
            border: 1px solid rgba(71, 105, 140, 135);
            border-radius: 12px;
            padding: 12px 14px;
            font-size: 13px;
            line-height: 150%;
        }
        QLabel#badge {
            color: #9caec2;
            font-size: 10px;
        }
        QLabel#evidence {
            background: #02070d;
            border: 1px solid rgba(0, 157, 255, 150);
            border-radius: 12px;
            padding: 4px;
        }
        QPushButton {
            background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                stop:0 #008cff, stop:1 #0752c9);
            color: #FFFFFF;
            border: 1px solid #209bff;
            padding: 8px 28px;
            font-size: 13px;
            font-weight: 800;
            border-radius: 8px;
            min-width: 170px;
            min-height: 30px;
        }
        QPushButton:hover {
            background: #009dff;
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

        self.setWindowTitle("WebGuardiao - IA Pediatria")
        self.setWindowFlags(
            Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.Dialog
        )
        has_images = self.evidence_pixmap is not None or self.crop_pixmap is not None
        self.setMinimumWidth(760 if has_images else 500)
        self.setStyleSheet(self._STYLE)

        root = QVBoxLayout(self)
        root.setContentsMargins(18, 18, 18, 18)
        root.setSpacing(0)

        card = QFrame()
        card.setObjectName("popupCard")
        shadow = QGraphicsDropShadowEffect(card)
        shadow.setBlurRadius(32)
        shadow.setOffset(0, 0)
        shadow.setColor(QColor(0, 160, 255, 60))
        card.setGraphicsEffect(shadow)
        root.addWidget(card)

        layout = QVBoxLayout(card)
        layout.setSpacing(14)
        layout.setContentsMargins(24, 22, 24, 20)

        header = QHBoxLayout()
        brand = QLabel(
            '<span style="color:#00a7ff;">WEB</span>'
            '<span style="color:#f5f8ff;">GUARDIAO</span>'
        )
        brand.setObjectName("brand")
        header.addWidget(brand)
        header.addStretch(1)
        module = QLabel("IA Pediatria")
        module.setObjectName("moduleBadge")
        header.addWidget(module)
        layout.addLayout(header)

        title_text = (
            "Crianca se afastando do acompanhante"
            if self.alert_state == "CHILD_SEPARATED"
            else "Possivel crianca desacompanhada"
        )
        severity = QLabel("ALERTA OPERACIONAL")
        severity.setObjectName("severity")
        severity.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(severity, alignment=Qt.AlignmentFlag.AlignHCenter)

        title = QLabel(title_text)
        title.setObjectName("title")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(title)

        subtitle = QLabel("Revise a evidencia visual antes de qualquer acao.")
        subtitle.setObjectName("subtitle")
        subtitle.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(subtitle)

        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet("color: rgba(0, 157, 255, 95); max-height: 1px;")
        layout.addWidget(sep)

        evidence_row = QHBoxLayout()
        evidence_row.setSpacing(12)
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
            f"Camera: {self.camera_id}\n"
            f"Pessoa em analise: #{self.track_id}\n"
            f"Horario: {datetime.now().strftime('%H:%M:%S')}\n"
            f"Confianca: {self.confidence:.0%}"
        )
        info.setObjectName("info")
        layout.addWidget(info)

        btn = QPushButton("Entendi, fechar alerta")
        btn.clicked.connect(self.accept)
        layout.addWidget(btn, alignment=Qt.AlignmentFlag.AlignCenter)

        badge = QLabel("Demo Viewer local - evidencia registrada na sessao")
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

        label.setPixmap(
            pixmap.scaled(
                width,
                height,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        )
        return label


