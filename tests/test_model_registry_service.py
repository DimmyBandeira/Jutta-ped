from __future__ import annotations

from pathlib import Path

import pytest

from modulo.pediatria.model_registry import ModelEntry, ModelRegistry
from modulo.pediatria.model_registry_service import (
    ModelImportError,
    ModelRegistryService,
    resolve_default_model_path,
)


@pytest.fixture
def service(tmp_path: Path) -> ModelRegistryService:
    return ModelRegistryService(root=tmp_path)


def _make_pt_file(tmp_path: Path, name: str = "fake_model.pt", content: bytes = b"weights") -> Path:
    path = tmp_path / name
    path.write_bytes(content)
    return path


def _make_openvino_pair(tmp_path: Path, stem: str = "fake_model", with_metadata: bool = False) -> Path:
    xml_path = tmp_path / f"{stem}.xml"
    xml_path.write_text("<net/>", encoding="utf-8")
    bin_path = tmp_path / f"{stem}.bin"
    bin_path.write_bytes(b"weights")
    if with_metadata:
        (tmp_path / "metadata.yaml").write_text("names: {0: adult, 1: child}", encoding="utf-8")
    return xml_path


# -- load() / registry vazio --------------------------------------------


def test_load_returns_empty_registry_when_file_missing(service: ModelRegistryService) -> None:
    registry = service.load()
    assert registry.entries == []
    assert registry.active_by_role == {}
    assert not service.registry_path.exists()  # load() nao escreve nada


def test_load_returns_empty_registry_on_corrupted_json(service: ModelRegistryService) -> None:
    service.registry_path.parent.mkdir(parents=True, exist_ok=True)
    service.registry_path.write_text("{ isso nao e json valido", encoding="utf-8")
    registry = service.load()
    assert registry.entries == []


def test_resolve_active_path_returns_none_without_registry(service: ModelRegistryService) -> None:
    assert service.resolve_active_path("specialist") is None


def test_app_never_needs_to_create_registry_to_resolve(service: ModelRegistryService) -> None:
    """App abre sem runtime/model_registry.json (criterio obrigatorio)."""
    service.resolve_active_path("specialist")
    service.resolve_active_path("detector")
    assert not service.registry_path.exists()


# -- import .pt -----------------------------------------------------------


def test_import_pt_copies_file_and_creates_entry(tmp_path: Path, service: ModelRegistryService) -> None:
    source = _make_pt_file(tmp_path)
    entry = service.import_pt(source, name="meu_modelo", version="v1", role="specialist")

    assert entry.backend == "pt"
    assert entry.role == "specialist"
    assert entry.sha256 is not None
    dest = service.root / entry.path
    assert dest.is_file()
    assert dest.read_bytes() == b"weights"
    assert source.is_file()  # original nao foi movido/apagado


def test_import_pt_rejects_wrong_extension(tmp_path: Path, service: ModelRegistryService) -> None:
    bogus = tmp_path / "not_a_model.txt"
    bogus.write_text("oi", encoding="utf-8")
    with pytest.raises(ModelImportError):
        service.import_pt(bogus, name="x", version="y", role="specialist")


def test_import_pt_rejects_missing_source(tmp_path: Path, service: ModelRegistryService) -> None:
    with pytest.raises(ModelImportError):
        service.import_pt(tmp_path / "nao_existe.pt", name="x", version="y", role="specialist")


def test_import_pt_appends_without_deleting_previous_entries(
    tmp_path: Path, service: ModelRegistryService
) -> None:
    first = service.import_pt(_make_pt_file(tmp_path, "a.pt", b"v1"), name="m", version="v1", role="specialist")
    second = service.import_pt(_make_pt_file(tmp_path, "b.pt", b"v2"), name="m", version="v2", role="specialist")

    entries = service.list_entries("specialist")
    assert {entry.id for entry in entries} == {first.id, second.id}
    assert (service.root / first.path).is_file()
    assert (service.root / second.path).is_file()


# -- import OpenVINO --------------------------------------------------------


def test_import_openvino_copies_xml_and_bin(tmp_path: Path, service: ModelRegistryService) -> None:
    xml_path = _make_openvino_pair(tmp_path)
    entry = service.import_openvino(xml_path, name="especialista", version="v6", role="specialist")

    assert entry.backend == "openvino"
    dest_dir = service.root / entry.path
    assert (dest_dir / "fake_model.xml").is_file()
    assert (dest_dir / "fake_model.bin").is_file()
    ok, reason = service.validate_entry(entry)
    assert ok, reason


def test_import_openvino_copies_metadata_when_present(tmp_path: Path, service: ModelRegistryService) -> None:
    xml_path = _make_openvino_pair(tmp_path, with_metadata=True)
    entry = service.import_openvino(xml_path, name="especialista", version="v6", role="specialist")
    dest_dir = service.root / entry.path
    assert (dest_dir / "metadata.yaml").is_file()


def test_import_openvino_requires_bin_pair(tmp_path: Path, service: ModelRegistryService) -> None:
    xml_only = tmp_path / "incompleto.xml"
    xml_only.write_text("<net/>", encoding="utf-8")
    with pytest.raises(ModelImportError):
        service.import_openvino(xml_only, name="x", version="y", role="detector")


def test_import_openvino_rejects_missing_xml(tmp_path: Path, service: ModelRegistryService) -> None:
    with pytest.raises(ModelImportError):
        service.import_openvino(tmp_path / "nao_existe.xml", name="x", version="y", role="detector")


# -- set_active / rollback / backup -----------------------------------------


def test_set_active_persists_and_resolves(tmp_path: Path, service: ModelRegistryService) -> None:
    entry = service.import_pt(_make_pt_file(tmp_path), name="m", version="v1", role="specialist")
    service.set_active("specialist", entry.id)

    resolved = service.resolve_active_path("specialist")
    assert resolved == service.root / entry.path
    assert service.get_active_entry("specialist").id == entry.id


def test_set_active_rejects_unknown_entry(service: ModelRegistryService) -> None:
    with pytest.raises(ModelImportError):
        service.set_active("specialist", "nao-existe")


def test_set_active_rejects_role_mismatch(tmp_path: Path, service: ModelRegistryService) -> None:
    entry = service.import_pt(_make_pt_file(tmp_path), name="m", version="v1", role="specialist")
    with pytest.raises(ModelImportError):
        service.set_active("detector", entry.id)


def test_rollback_reactivates_older_entry(tmp_path: Path, service: ModelRegistryService) -> None:
    old = service.import_pt(_make_pt_file(tmp_path, "old.pt", b"v1"), name="m", version="v1", role="specialist")
    new = service.import_pt(_make_pt_file(tmp_path, "new.pt", b"v2"), name="m", version="v2", role="specialist")
    service.set_active("specialist", new.id)
    assert service.get_active_entry("specialist").id == new.id

    service.set_active("specialist", old.id)  # rollback
    assert service.get_active_entry("specialist").id == old.id
    # a entrada "nova" continua no registry, so nao esta mais ativa
    assert old.id in {e.id for e in service.list_entries("specialist")}
    assert new.id in {e.id for e in service.list_entries("specialist")}


def test_save_backs_up_previous_registry_file(tmp_path: Path, service: ModelRegistryService) -> None:
    entry1 = service.import_pt(_make_pt_file(tmp_path, "a.pt"), name="m", version="v1", role="specialist")
    backup_path = service.registry_path.with_suffix(service.registry_path.suffix + ".bak")
    assert not backup_path.exists()  # ainda nao houve um segundo save

    service.set_active("specialist", entry1.id)
    assert backup_path.exists()  # save() de set_active fez backup do estado anterior


def test_restarting_app_keeps_active_model(tmp_path: Path) -> None:
    """Reiniciar o app == criar um ModelRegistryService novo apontando pro
    mesmo root e ler de novo; simula isso sem reiniciar processo de verdade."""
    first_service = ModelRegistryService(root=tmp_path)
    entry = first_service.import_pt(
        _make_pt_file(tmp_path), name="m", version="v1", role="specialist"
    )
    first_service.set_active("specialist", entry.id)

    second_service = ModelRegistryService(root=tmp_path)
    resolved = second_service.resolve_active_path("specialist")
    assert resolved == second_service.root / entry.path


# -- test_load ---------------------------------------------------------------


def test_test_load_uses_injected_runner_factory(tmp_path: Path, service: ModelRegistryService) -> None:
    entry = service.import_pt(_make_pt_file(tmp_path), name="m", version="v1", role="specialist")
    calls: list[str] = []

    def fake_runner(path: str) -> object:
        calls.append(path)
        return object()

    ok, message = service.test_load(entry, runner_factory=fake_runner)
    assert ok, message
    assert calls == [str(service.root / entry.path)]


def test_test_load_reports_failure_when_runner_raises(tmp_path: Path, service: ModelRegistryService) -> None:
    entry = service.import_pt(_make_pt_file(tmp_path), name="m", version="v1", role="specialist")

    def failing_runner(path: str) -> object:
        raise ValueError("modelo corrompido")

    ok, message = service.test_load(entry, runner_factory=failing_runner)
    assert not ok
    assert "modelo corrompido" in message


def test_test_load_fails_fast_when_file_missing_without_calling_runner(
    tmp_path: Path, service: ModelRegistryService
) -> None:
    entry = ModelEntry(
        id="ghost", name="fantasma", version="v1", role="specialist",
        backend="pt", path="runtime/models/nao_existe.pt", created_at="",
    )
    calls: list[str] = []
    ok, message = service.test_load(entry, runner_factory=lambda path: calls.append(path))
    assert not ok
    assert calls == []  # nao tenta carregar arquivo que nao existe


# -- resolve_default_model_path (usado por runtime.py/demo_viewer.py) -------


def test_resolve_default_model_path_falls_back_without_active_entry(tmp_path: Path) -> None:
    fallback = tmp_path / "fallback_path"
    resolved = resolve_default_model_path("specialist", fallback, root=tmp_path)
    assert resolved == fallback


def test_resolve_default_model_path_uses_active_entry(tmp_path: Path) -> None:
    service = ModelRegistryService(root=tmp_path)
    entry = service.import_pt(_make_pt_file(tmp_path), name="m", version="v1", role="specialist")
    service.set_active("specialist", entry.id)

    fallback = tmp_path / "fallback_path"
    resolved = resolve_default_model_path("specialist", fallback, root=tmp_path)
    assert resolved == service.root / entry.path
    assert resolved != fallback


def test_resolve_default_model_path_never_raises(tmp_path: Path) -> None:
    fallback = tmp_path / "fallback_path"
    # registry corrompido de proposito
    (tmp_path / "runtime").mkdir()
    (tmp_path / "runtime" / "model_registry.json").write_text("{{{ invalido", encoding="utf-8")
    resolved = resolve_default_model_path("specialist", fallback, root=tmp_path)
    assert resolved == fallback
