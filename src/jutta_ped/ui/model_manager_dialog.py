"""Tela tecnica escondida de gerenciamento de modelos (Ctrl+Shift+M).

Fala com o registry so por `ModelRegistryService` (modulo/pediatria) --
igual a regra ja usada em service_client.py para a UI nunca pular a
fronteira ui -> service/core diretamente. Nao muda nenhuma logica de
inferencia: so importa arquivos, troca qual entrada esta marcada como
ativa e, opcionalmente, tenta carregar o modelo sob demanda para validar.

Nao aparece na navegacao principal -- so abre via atalho (ver
_open_model_manager em demo_viewer.py). Fechar o dialog nao afeta uma
analise em andamento; a troca de modelo ativo so vale a partir do proximo
"Iniciar" (ou do proximo restart do app, para o servico headless).
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from modulo.pediatria.model_registry import ModelEntry
from modulo.pediatria.model_registry_service import ModelImportError, ModelRegistryService

_ROLES = ["specialist", "detector"]
_ROLE_LABELS = {"specialist": "Especialista (adult/child)", "detector": "Detector de pessoa"}
_TABLE_COLUMNS = ["Papel", "Nome", "Versao", "Backend", "Ativo", "Criado em"]


class ModelManagerDialog(QDialog):
    def __init__(
        self,
        parent: Any = None,
        *,
        registry_service: ModelRegistryService | None = None,
        viewer: Any = None,
    ) -> None:
        super().__init__(parent)
        self.registry_service = registry_service or ModelRegistryService()
        # `viewer` e o Demo Viewer cujos campos model_value/person_model_value
        # sincronizamos apos ativar um modelo (ver _sync_live_viewer_fields).
        # Separado de `parent` (posse/ciclo de vida Qt) de proposito: parent
        # precisa ser um QWidget de verdade, viewer so precisa responder a
        # duck typing (facilita teste com um objeto qualquer). Por padrao
        # usa o proprio parent, que e o caso real de uso em demo_viewer.py.
        self._viewer = viewer if viewer is not None else parent
        self._entries: list[ModelEntry] = []

        self.setWindowTitle("IA Pediatria - Gerenciador de Modelos (tecnico)")
        self.resize(760, 480)

        layout = QVBoxLayout(self)

        self.active_label = QLabel()
        self.active_label.setWordWrap(True)
        layout.addWidget(self.active_label)

        self.table = QTableWidget(0, len(_TABLE_COLUMNS))
        self.table.setHorizontalHeaderLabels(_TABLE_COLUMNS)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self.table)

        self.status_label = QLabel("")
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)

        buttons_row1 = QHBoxLayout()
        self.import_pt_button = QPushButton("Importar .pt")
        self.import_openvino_button = QPushButton("Importar OpenVINO (.xml)")
        self.open_folder_button = QPushButton("Abrir pasta de modelos")
        buttons_row1.addWidget(self.import_pt_button)
        buttons_row1.addWidget(self.import_openvino_button)
        buttons_row1.addWidget(self.open_folder_button)
        layout.addLayout(buttons_row1)

        buttons_row2 = QHBoxLayout()
        self.set_active_button = QPushButton("Definir como ativo")
        self.test_load_button = QPushButton("Testar carregamento")
        self.close_button = QPushButton("Fechar")
        buttons_row2.addWidget(self.set_active_button)
        buttons_row2.addWidget(self.test_load_button)
        buttons_row2.addStretch(1)
        buttons_row2.addWidget(self.close_button)
        layout.addLayout(buttons_row2)

        self.import_pt_button.clicked.connect(self._import_pt)
        self.import_openvino_button.clicked.connect(self._import_openvino)
        self.open_folder_button.clicked.connect(self._open_models_folder)
        self.set_active_button.clicked.connect(self._set_active)
        self.test_load_button.clicked.connect(self._test_load)
        self.close_button.clicked.connect(self.close)

        self.refresh()

    # -- estado / tabela --------------------------------------------------

    def refresh(self) -> None:
        registry = self.registry_service.load()
        self._entries = sorted(
            registry.entries, key=lambda item: (item.role, item.created_at), reverse=True
        )

        active_lines = []
        for role in _ROLES:
            entry = self.registry_service.get_active_entry(role)
            if entry is None:
                active_lines.append(f"{_ROLE_LABELS[role]}: usando caminho padrao do repositorio (sem entrada ativa no registry).")
            else:
                ok, reason = self.registry_service.validate_entry(entry)
                status = "OK" if ok else f"INVALIDO ({reason})"
                active_lines.append(
                    f"{_ROLE_LABELS[role]}: {entry.name} {entry.version} [{entry.backend}] -- {status}"
                )
        self.active_label.setText("Modelo ativo atual:\n" + "\n".join(active_lines))

        self.table.setRowCount(len(self._entries))
        for row, entry in enumerate(self._entries):
            is_active = registry.active_by_role.get(entry.role) == entry.id
            values = [
                _ROLE_LABELS.get(entry.role, entry.role),
                entry.name,
                entry.version,
                entry.backend,
                "sim" if is_active else "",
                entry.created_at,
            ]
            for col, value in enumerate(values):
                item = QTableWidgetItem(str(value))
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                self.table.setItem(row, col, item)

    def _selected_entry(self) -> ModelEntry | None:
        row = self.table.currentRow()
        if row < 0 or row >= len(self._entries):
            return None
        return self._entries[row]

    # -- acoes --------------------------------------------------------

    def _prompt_metadata(self, default_name: str) -> tuple[str, str, str] | None:
        role, ok = QInputDialog.getItem(
            self, "Papel do modelo", "Este modelo e usado para:", _ROLES, 0, False
        )
        if not ok:
            return None
        name, ok = QInputDialog.getText(self, "Nome do modelo", "Nome:", text=default_name)
        if not ok or not name.strip():
            return None
        version, ok = QInputDialog.getText(self, "Versao", "Versao:", text="v1")
        if not ok or not version.strip():
            return None
        return role, name.strip(), version.strip()

    def _import_pt(self) -> None:
        file_path, _ = QFileDialog.getOpenFileName(
            self, "Selecionar modelo .pt", "", "Modelos PyTorch (*.pt)"
        )
        if not file_path:
            return
        metadata = self._prompt_metadata(Path(file_path).stem)
        if metadata is None:
            return
        role, name, version = metadata
        try:
            entry = self.registry_service.import_pt(
                Path(file_path), name=name, version=version, role=role
            )
        except ModelImportError as exc:
            QMessageBox.warning(self, "Falha ao importar", str(exc))
            return
        self.refresh()
        self.status_label.setText(f"Importado: {entry.name} {entry.version} ({entry.id}).")

    def _import_openvino(self) -> None:
        file_path, _ = QFileDialog.getOpenFileName(
            self, "Selecionar modelo OpenVINO (.xml)", "", "OpenVINO IR (*.xml)"
        )
        if not file_path:
            return
        metadata = self._prompt_metadata(Path(file_path).stem)
        if metadata is None:
            return
        role, name, version = metadata
        try:
            entry = self.registry_service.import_openvino(
                Path(file_path), name=name, version=version, role=role
            )
        except ModelImportError as exc:
            QMessageBox.warning(self, "Falha ao importar", str(exc))
            return
        self.refresh()
        self.status_label.setText(f"Importado: {entry.name} {entry.version} ({entry.id}).")

    def _set_active(self) -> None:
        entry = self._selected_entry()
        if entry is None:
            QMessageBox.information(self, "Nenhum modelo selecionado", "Selecione uma linha da tabela primeiro.")
            return
        try:
            self.registry_service.set_active(entry.role, entry.id)
        except ModelImportError as exc:
            QMessageBox.warning(self, "Falha ao ativar", str(exc))
            return
        self.refresh()
        self.status_label.setText(
            f"Ativado: {entry.name} {entry.version} para '{_ROLE_LABELS.get(entry.role, entry.role)}'. "
            "Vale a partir do proximo 'Iniciar' (ou do proximo restart, no servico headless)."
        )
        self._sync_live_viewer_fields(entry)

    def _sync_live_viewer_fields(self, entry: ModelEntry) -> None:
        """Atualiza os campos de caminho do Demo Viewer que abriu este
        dialog, se houver, para o novo modelo valer sem precisar reiniciar
        o processo. Best-effort via duck typing (nao importa a classe do
        Demo Viewer aqui, evita acoplamento/import circular)."""
        if self._viewer is None:
            return
        resolved = self.registry_service.resolve_active_path(entry.role)
        if resolved is None:
            return
        field_name = "model_value" if entry.role == "specialist" else "person_model_value"
        field = getattr(self._viewer, field_name, None)
        if field is not None and hasattr(field, "setText"):
            field.setText(str(resolved))

    def _test_load(self) -> None:
        entry = self._selected_entry()
        if entry is None:
            QMessageBox.information(self, "Nenhum modelo selecionado", "Selecione uma linha da tabela primeiro.")
            return
        self.status_label.setText(f"Carregando {entry.name} {entry.version} para testar...")
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        QApplication.processEvents()
        try:
            ok, message = self.registry_service.test_load(entry)
        finally:
            QApplication.restoreOverrideCursor()
        self.status_label.setText(f"{'OK' if ok else 'FALHOU'}: {message}")
        if not ok:
            QMessageBox.warning(self, "Teste de carregamento falhou", message)

    def _open_models_folder(self) -> None:
        self.registry_service.models_dir.mkdir(parents=True, exist_ok=True)
        path = str(self.registry_service.models_dir)
        try:
            os.startfile(path)  # type: ignore[attr-defined]
        except (AttributeError, OSError) as exc:
            QMessageBox.information(self, "Pasta de modelos", f"{path}\n\n(Nao foi possivel abrir automaticamente: {exc})")
