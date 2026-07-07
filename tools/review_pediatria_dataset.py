from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QKeySequence, QPixmap, QShortcut
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REVIEW_ROOT = (
    ROOT
    / "pediatria_results"
    / "v5_pediatria_local"
    / "dataset_review_collection"
    / "review_priority"
)
VALID_LABELS = {"child", "adult", "uncertain", "ignore"}


@dataclass
class ReviewItem:
    index: int
    file: Path
    source_file: Path
    source: str
    frame: int | None
    track: int | None
    predicted_role: str
    confidence: float | None
    metadata: dict[str, Any] = field(default_factory=dict)
    human_label: str = ""
    training_eligible: bool = False
    notes: str = ""


def _as_bool(value: Any) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "sim", "yes", "y"}


def _as_float(value: Any) -> float | None:
    text = str(value or "").strip().replace(",", ".")
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _as_int(value: Any) -> int | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return int(text)
    except ValueError:
        return None


def _resolve_path(value: str, base: Path) -> Path:
    path = Path(str(value or ""))
    if path.is_absolute():
        return path
    return (base / path).resolve()


def _portable_path(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT).as_posix()
    except ValueError:
        return str(path)


def load_review_priority_csv(path: Path) -> list[ReviewItem]:
    base = ROOT
    items: list[ReviewItem] = []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            review_copy = _resolve_path(row.get("review_copy", ""), base)
            source_file = _resolve_path(row.get("crop", ""), base)
            label = str(row.get("human_label") or "").strip().lower()
            if label not in VALID_LABELS:
                label = ""
            items.append(
                ReviewItem(
                    index=int(row.get("index") or len(items) + 1),
                    file=review_copy,
                    source_file=source_file,
                    source=str(row.get("source") or ""),
                    frame=_as_int(row.get("frame")),
                    track=_as_int(row.get("track")),
                    predicted_role=str(row.get("predicted_role") or ""),
                    confidence=_as_float(row.get("confidence")),
                    metadata=dict(row),
                    human_label=label,
                    training_eligible=_as_bool(row.get("training_eligible")),
                    notes=str(row.get("notes") or ""),
                )
            )
    return items


def load_collection_manifests(root: Path) -> list[ReviewItem]:
    items: list[ReviewItem] = []
    for manifest in sorted(root.rglob("review_manifest.jsonl")):
        for line in manifest.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            payload = json.loads(line)
            file = _resolve_path(payload.get("crop_path", ""), ROOT)
            label = str(payload.get("human_label") or "").strip().lower()
            if label not in VALID_LABELS:
                label = ""
            items.append(
                ReviewItem(
                    index=len(items) + 1,
                    file=file,
                    source_file=file,
                    source=str(payload.get("source") or ""),
                    frame=_as_int(payload.get("frame_index")),
                    track=_as_int(payload.get("track_id")),
                    predicted_role=str(payload.get("predicted_role") or ""),
                    confidence=_as_float(payload.get("confidence")),
                    metadata=payload,
                    human_label=label,
                    training_eligible=_as_bool(payload.get("training_eligible")),
                    notes=str(payload.get("review_notes") or ""),
                )
            )
    return items


def load_review_items(input_path: Path) -> list[ReviewItem]:
    if input_path.is_file() and input_path.suffix.lower() == ".csv":
        return load_review_priority_csv(input_path)
    if input_path.is_dir():
        csv_path = input_path / "review_priority.csv"
        if csv_path.is_file():
            return load_review_priority_csv(csv_path)
        return load_collection_manifests(input_path)
    raise FileNotFoundError(f"Entrada de revisao nao encontrada: {input_path}")


def write_review_outputs(
    items: list[ReviewItem],
    output_json: Path,
    output_csv: Path,
) -> None:
    labels: list[dict[str, Any]] = []
    for item in items:
        label = item.human_label.strip().lower()
        eligible = item.training_eligible and label in {"child", "adult", "uncertain"}
        labels.append(
            {
                "file": _portable_path(item.source_file),
                "review_copy": _portable_path(item.file),
                "human_label": label,
                "training_eligible": eligible,
                "source": item.source,
                "source_group": item.source,
                "frame": item.frame,
                "track_id": item.track,
                "predicted_role": item.predicted_role,
                "confidence": item.confidence,
                "notes": item.notes,
                "metadata": item.metadata,
            }
        )
    output_json.parent.mkdir(parents=True, exist_ok=True)
    output_json.write_text(
        json.dumps(
            {
                "mode": "pediatria_manual_review",
                "classes": ["adult", "child", "uncertain"],
                "labels": labels,
                "counts": _counts(items),
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    with output_csv.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "index",
                "human_label",
                "training_eligible",
                "predicted_role",
                "confidence",
                "source",
                "frame",
                "track",
                "file",
                "source_file",
                "notes",
            ],
        )
        writer.writeheader()
        for item in items:
            writer.writerow(
                {
                    "index": item.index,
                    "human_label": item.human_label,
                    "training_eligible": item.training_eligible,
                    "predicted_role": item.predicted_role,
                    "confidence": item.confidence,
                    "source": item.source,
                    "frame": item.frame,
                    "track": item.track,
                    "file": _portable_path(item.file),
                    "source_file": _portable_path(item.source_file),
                    "notes": item.notes,
                }
            )


def _counts(items: list[ReviewItem]) -> dict[str, Any]:
    reviewed = [item for item in items if item.human_label]
    eligible = [
        item
        for item in reviewed
        if item.training_eligible and item.human_label in {"child", "adult", "uncertain"}
    ]
    return {
        "total": len(items),
        "reviewed": len(reviewed),
        "eligible": len(eligible),
        "by_human_label": dict(Counter(item.human_label for item in reviewed)),
        "eligible_by_label": dict(Counter(item.human_label for item in eligible)),
        "by_predicted_role": dict(Counter(item.predicted_role for item in items)),
    }


class PediatriaReviewWindow(QMainWindow):
    def __init__(
        self,
        *,
        items: list[ReviewItem],
        output_json: Path,
        output_csv: Path,
    ) -> None:
        super().__init__()
        if not items:
            raise ValueError("Nenhuma imagem encontrada para revisao.")
        self.items = items
        self.output_json = output_json
        self.output_csv = output_csv
        self.position = self._first_unreviewed_index()
        self.setWindowTitle("Revisao pediatrica de crops")
        self.resize(1120, 780)
        self._build_ui()
        self._install_shortcuts()
        self._show_current()

    def _build_ui(self) -> None:
        central = QWidget()
        layout = QVBoxLayout(central)

        self.header = QLabel()
        self.header.setStyleSheet("font-size: 18px; font-weight: bold;")
        layout.addWidget(self.header)

        self.image = QLabel()
        self.image.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.image.setMinimumSize(780, 500)
        self.image.setStyleSheet("background: #111; color: #ddd;")
        layout.addWidget(self.image, 1)

        self.details = QLabel()
        self.details.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self.details)

        buttons = QHBoxLayout()
        self.child_button = QPushButton("1 - Crianca")
        self.adult_button = QPushButton("2 - Adulto")
        self.uncertain_button = QPushButton("3 - Incerto")
        self.ignore_button = QPushButton("4 - Ignorar")
        self.prev_button = QPushButton("Anterior")
        self.next_button = QPushButton("Proximo")
        self.save_button = QPushButton("Salvar")
        for button in (
            self.child_button,
            self.adult_button,
            self.uncertain_button,
            self.ignore_button,
            self.prev_button,
            self.next_button,
            self.save_button,
        ):
            buttons.addWidget(button)
        layout.addLayout(buttons)

        options = QHBoxLayout()
        self.training_eligible = QCheckBox("Usar no treino")
        self.training_eligible.setChecked(True)
        self.auto_next = QCheckBox("Avancar automatico")
        self.auto_next.setChecked(True)
        options.addWidget(self.training_eligible)
        options.addWidget(self.auto_next)
        options.addStretch()
        layout.addLayout(options)

        self.notes = QTextEdit()
        self.notes.setPlaceholderText("Observacoes opcionais")
        self.notes.setMaximumHeight(80)
        layout.addWidget(self.notes)

        self.status = QLabel()
        self.status.setStyleSheet("background: #20242b; color: white; padding: 8px;")
        layout.addWidget(self.status)
        self.setCentralWidget(central)

        self.child_button.clicked.connect(lambda: self._apply_label("child"))
        self.adult_button.clicked.connect(lambda: self._apply_label("adult"))
        self.uncertain_button.clicked.connect(lambda: self._apply_label("uncertain"))
        self.ignore_button.clicked.connect(lambda: self._apply_label("ignore"))
        self.prev_button.clicked.connect(self._previous)
        self.next_button.clicked.connect(self._next)
        self.save_button.clicked.connect(self._save)

    def _install_shortcuts(self) -> None:
        shortcuts = {
            "1": lambda: self._apply_label("child"),
            "2": lambda: self._apply_label("adult"),
            "3": lambda: self._apply_label("uncertain"),
            "4": lambda: self._apply_label("ignore"),
            "A": self._previous,
            "Left": self._previous,
            "D": self._next,
            "Right": self._next,
            "Ctrl+S": self._save,
        }
        for key, callback in shortcuts.items():
            shortcut = QShortcut(QKeySequence(key), self)
            shortcut.activated.connect(callback)

    def _first_unreviewed_index(self) -> int:
        for index, item in enumerate(self.items):
            if not item.human_label:
                return index
        return 0

    def _show_current(self) -> None:
        item = self.items[self.position]
        pixmap = QPixmap(str(item.file))
        if pixmap.isNull():
            self.image.setText(f"Imagem nao carregada:\n{item.file}")
        else:
            self.image.setPixmap(
                pixmap.scaled(
                    self.image.size(),
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
            )
        self.header.setText(
            f"{self.position + 1}/{len(self.items)} | "
            f"pred={item.predicted_role} conf={item.confidence}"
        )
        self.details.setText(
            f"Fonte: {item.source}\n"
            f"Frame: {item.frame} | Track: {item.track}\n"
            f"Arquivo: {item.file}\n"
            f"Rotulo humano: {item.human_label or '-'}"
        )
        self.training_eligible.setChecked(
            bool(item.training_eligible)
            if item.human_label
            else item.predicted_role in {"child", "adult"}
        )
        self.notes.setPlainText(item.notes)
        self._refresh_status()

    def resizeEvent(self, event: Any) -> None:
        super().resizeEvent(event)
        if hasattr(self, "image"):
            self._show_current()

    def _apply_label(self, label: str) -> None:
        item = self.items[self.position]
        item.human_label = label
        item.training_eligible = (
            self.training_eligible.isChecked()
            and label in {"child", "adult", "uncertain"}
        )
        if label == "ignore":
            item.training_eligible = False
        item.notes = self.notes.toPlainText().strip()
        self._save(silent=True)
        if self.auto_next.isChecked():
            self._next()
        else:
            self._show_current()

    def _save_current_notes(self) -> None:
        item = self.items[self.position]
        item.training_eligible = (
            self.training_eligible.isChecked()
            and item.human_label in {"child", "adult", "uncertain"}
        )
        item.notes = self.notes.toPlainText().strip()

    def _previous(self) -> None:
        self._save_current_notes()
        self.position = max(0, self.position - 1)
        self._show_current()

    def _next(self) -> None:
        self._save_current_notes()
        self.position = min(len(self.items) - 1, self.position + 1)
        self._show_current()

    def _save(self, *, silent: bool = False) -> None:
        self._save_current_notes()
        write_review_outputs(self.items, self.output_json, self.output_csv)
        if not silent:
            QMessageBox.information(
                self,
                "Revisao salva",
                f"Arquivos salvos:\n{self.output_json}\n{self.output_csv}",
            )
        self._refresh_status()

    def _refresh_status(self) -> None:
        counts = _counts(self.items)
        self.status.setText(
            f"Revisados: {counts['reviewed']}/{counts['total']} | "
            f"Elegiveis: {counts['eligible']} | "
            f"Labels: {counts['by_human_label']} | "
            f"Saida: {self.output_json}"
        )

    def closeEvent(self, event: Any) -> None:
        self._save(silent=True)
        super().closeEvent(event)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="UI manual para revisar crops pediatricos e gerar labels de treino."
    )
    parser.add_argument(
        "--input",
        default=str(DEFAULT_REVIEW_ROOT),
        help="Pasta review_priority, CSV review_priority.csv ou raiz dataset_review_collection.",
    )
    parser.add_argument(
        "--output-json",
        default="",
        help="Saida JSON para tools/build_pediatria_colab_package.py.",
    )
    parser.add_argument("--output-csv", default="", help="Saida CSV de auditoria.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    input_path = Path(args.input)
    output_base = input_path if input_path.is_dir() else input_path.parent
    output_json = Path(args.output_json) if args.output_json else output_base / "human_review_labels.json"
    output_csv = Path(args.output_csv) if args.output_csv else output_base / "human_review_labels.csv"
    items = load_review_items(input_path)
    app = QApplication(sys.argv)
    window = PediatriaReviewWindow(
        items=items,
        output_json=output_json,
        output_csv=output_csv,
    )
    window.show()
    raise SystemExit(app.exec())


if __name__ == "__main__":
    main()
