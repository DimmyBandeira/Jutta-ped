from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from modulo.pediatria.model_registry_service import ModelRegistryService
from src.jutta_ped.ui.model_manager_dialog import ModelManagerDialog


def _make_pt_file(tmp_path: Path, name: str = "fake_model.pt") -> Path:
    path = tmp_path / name
    path.write_bytes(b"weights")
    return path


def test_dialog_opens_with_empty_registry_without_crashing(qtbot, tmp_path: Path) -> None:
    service = ModelRegistryService(root=tmp_path)
    dialog = ModelManagerDialog(registry_service=service)
    qtbot.addWidget(dialog)

    assert dialog.table.rowCount() == 0
    assert "usando caminho padrao" in dialog.active_label.text()


def test_dialog_lists_imported_entries(qtbot, tmp_path: Path) -> None:
    service = ModelRegistryService(root=tmp_path)
    entry = service.import_pt(_make_pt_file(tmp_path), name="meu_modelo", version="v1", role="specialist")

    dialog = ModelManagerDialog(registry_service=service)
    qtbot.addWidget(dialog)

    assert dialog.table.rowCount() == 1
    assert dialog.table.item(0, 1).text() == "meu_modelo"
    assert dialog.table.item(0, 2).text() == "v1"
    assert dialog.table.item(0, 4).text() == ""  # ainda nao esta ativo

    dialog.table.selectRow(0)
    dialog._set_active()

    assert service.get_active_entry("specialist").id == entry.id
    assert dialog.table.item(0, 4).text() == "sim"
    assert "Ativado" in dialog.status_label.text()


def test_set_active_without_selection_shows_message_and_does_not_raise(
    qtbot, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = ModelRegistryService(root=tmp_path)
    dialog = ModelManagerDialog(registry_service=service)
    qtbot.addWidget(dialog)

    infos = []
    monkeypatch.setattr(
        "src.jutta_ped.ui.model_manager_dialog.QMessageBox.information",
        lambda self, title, text: infos.append(text),
    )
    dialog._set_active()  # nenhuma linha selecionada -- nao deve travar nem lancar

    assert len(infos) == 1


def test_set_active_syncs_live_parent_viewer_fields(qtbot, tmp_path: Path) -> None:
    service = ModelRegistryService(root=tmp_path)
    entry = service.import_pt(_make_pt_file(tmp_path), name="meu_modelo", version="v1", role="specialist")

    fake_viewer = SimpleNamespace(model_value=SimpleNamespace(setText=lambda text: setattr(fake_viewer, "_last_text", text)))
    dialog = ModelManagerDialog(registry_service=service, viewer=fake_viewer)
    qtbot.addWidget(dialog)

    dialog.table.selectRow(0)
    dialog._set_active()

    resolved = service.resolve_active_path("specialist")
    assert fake_viewer._last_text == str(resolved)


def test_test_load_button_uses_registry_service_and_reports_status(qtbot, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    service = ModelRegistryService(root=tmp_path)
    service.import_pt(_make_pt_file(tmp_path), name="meu_modelo", version="v1", role="specialist")

    dialog = ModelManagerDialog(registry_service=service)
    qtbot.addWidget(dialog)
    dialog.table.selectRow(0)

    calls = []

    def fake_test_load(entry, **kwargs):
        calls.append(entry.id)
        return True, "Modelo carregado com sucesso."

    monkeypatch.setattr(service, "test_load", fake_test_load)
    dialog._test_load()

    assert len(calls) == 1
    assert "OK" in dialog.status_label.text()


def test_test_load_reports_failure(qtbot, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    service = ModelRegistryService(root=tmp_path)
    service.import_pt(_make_pt_file(tmp_path), name="meu_modelo", version="v1", role="specialist")

    dialog = ModelManagerDialog(registry_service=service)
    qtbot.addWidget(dialog)
    dialog.table.selectRow(0)

    monkeypatch.setattr(service, "test_load", lambda entry, **kwargs: (False, "arquivo corrompido"))
    monkeypatch.setattr(
        "src.jutta_ped.ui.model_manager_dialog.QMessageBox.warning",
        lambda *args, **kwargs: None,
    )
    dialog._test_load()

    assert "FALHOU" in dialog.status_label.text()
    assert "arquivo corrompido" in dialog.status_label.text()


def test_import_pt_flow_with_mocked_file_and_input_dialogs(qtbot, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    service = ModelRegistryService(root=tmp_path)
    source = _make_pt_file(tmp_path, "importado.pt")

    dialog = ModelManagerDialog(registry_service=service)
    qtbot.addWidget(dialog)

    monkeypatch.setattr(
        "src.jutta_ped.ui.model_manager_dialog.QFileDialog.getOpenFileName",
        lambda *args, **kwargs: (str(source), ""),
    )
    monkeypatch.setattr(
        "src.jutta_ped.ui.model_manager_dialog.QInputDialog.getItem",
        lambda *args, **kwargs: ("detector", True),
    )
    text_calls = iter(["modelo_importado", "v2"])
    monkeypatch.setattr(
        "src.jutta_ped.ui.model_manager_dialog.QInputDialog.getText",
        lambda *args, **kwargs: (next(text_calls), True),
    )

    dialog._import_pt()

    entries = service.list_entries("detector")
    assert len(entries) == 1
    assert entries[0].name == "modelo_importado"
    assert entries[0].version == "v2"
    assert dialog.table.rowCount() == 1


def test_import_pt_cancelled_file_dialog_does_nothing(qtbot, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    service = ModelRegistryService(root=tmp_path)
    dialog = ModelManagerDialog(registry_service=service)
    qtbot.addWidget(dialog)

    monkeypatch.setattr(
        "src.jutta_ped.ui.model_manager_dialog.QFileDialog.getOpenFileName",
        lambda *args, **kwargs: ("", ""),
    )
    dialog._import_pt()

    assert service.list_entries() == []


def test_import_openvino_requires_bin_shows_warning_not_crash(qtbot, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    service = ModelRegistryService(root=tmp_path)
    xml_only = tmp_path / "incompleto.xml"
    xml_only.write_text("<net/>", encoding="utf-8")

    dialog = ModelManagerDialog(registry_service=service)
    qtbot.addWidget(dialog)

    monkeypatch.setattr(
        "src.jutta_ped.ui.model_manager_dialog.QFileDialog.getOpenFileName",
        lambda *args, **kwargs: (str(xml_only), ""),
    )
    monkeypatch.setattr(
        "src.jutta_ped.ui.model_manager_dialog.QInputDialog.getItem",
        lambda *args, **kwargs: ("specialist", True),
    )
    monkeypatch.setattr(
        "src.jutta_ped.ui.model_manager_dialog.QInputDialog.getText",
        lambda *args, **kwargs: ("x", True),
    )
    warnings = []
    monkeypatch.setattr(
        "src.jutta_ped.ui.model_manager_dialog.QMessageBox.warning",
        lambda self, title, text: warnings.append(text),
    )

    dialog._import_openvino()

    assert service.list_entries() == []
    assert len(warnings) == 1
    assert ".bin" in warnings[0]
