"""Servico do registry de modelos externos (runtime/model_registry.json).

Fica em modulo/pediatria (core) porque tanto o servico headless
(src/jutta_ped/service/runtime.py) quanto o Demo Viewer
(src/jutta_ped/ui/demo_viewer.py) precisam resolver o mesmo caminho de
modelo ativo, sem duplicar a logica -- mesma regra de dependencia
ui -> service -> core do resto do projeto.

Principios (ver docs/build_nuitka_release.md):
- O app precisa continuar funcionando sem nenhum registry e sem nenhum
  modelo em runtime/models: load() nunca lanca so por o arquivo faltar, e
  nunca escreve nada em disco so por ser lido (load() e efeito-colateral
  livre; escrita so acontece em save(), chamada por acoes explicitas do
  tecnico -- importar, ativar).
- Modelos nunca sao apagados aqui (import so adiciona; trocar o ativo so
  muda o ponteiro active_by_role, nao remove a entrada antiga -- e assim
  que "rollback" funciona: reativar uma entrada mais velha que ainda esta
  na lista).
- Nao carrega nenhum modelo so para listar/validar presenca de arquivo.
  Carregar de verdade (`test_load`) e uma acao explicita, sob demanda.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from .model_registry import ModelEntry, ModelProfile, ModelRegistry

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MODELS_DIR = ROOT / "runtime" / "models"
DEFAULT_REGISTRY_PATH = ROOT / "runtime" / "model_registry.json"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _slugify(value: str) -> str:
    allowed = []
    for char in str(value or "modelo"):
        if char.isalnum() or char in {"-", "_"}:
            allowed.append(char)
        elif char in {" ", "."}:
            allowed.append("_")
    slug = "".join(allowed).strip("_")
    return slug[:60] or "modelo"


def sha256_of_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class ModelImportError(Exception):
    """Erro amigavel de import/ativacao/validacao do registry de modelos."""


def resolve_default_model_path(role: str, fallback: Path, *, root: Path | None = None) -> Path:
    """Resolve o caminho de modelo para `role`: entrada ativa do registry
    tecnico primeiro, `fallback` (o DEFAULT_*_PATH ja calculado por quem
    chama, do jeito que sempre funcionou) se nao houver entrada valida.

    Nunca lanca -- qualquer erro ao ler o registry (arquivo corrompido,
    permissao, etc.) cai silenciosamente no fallback, porque resolver o
    caminho padrao nao pode impedir o app de abrir.
    """
    try:
        service = ModelRegistryService(root=root)
        resolved = service.resolve_active_path(role)
        if resolved is not None:
            return resolved
    except Exception:
        pass
    return fallback


class ModelRegistryService:
    """Ponto unico de leitura/escrita do registry de modelos.

    `root` e sobrescrevivel para testes (nao usa nenhum caminho absoluto
    hardcoded fora do repo/instalacao).
    """

    def __init__(self, root: Path | None = None) -> None:
        self.root = Path(root) if root is not None else ROOT
        self.models_dir = self.root / "runtime" / "models"
        self.registry_path = self.root / "runtime" / "model_registry.json"

    # -- leitura ------------------------------------------------------

    def load(self) -> ModelRegistry:
        """Le o registry do disco. Se o arquivo nao existir, retorna um
        registry vazio em memoria (nao escreve nada). Se o arquivo existir
        mas estiver corrompido/ilegivel, tambem cai para vazio em vez de
        propagar a excecao -- um registry ilegivel nao pode impedir o app
        de abrir.
        """
        if not self.registry_path.is_file():
            return ModelRegistry()
        try:
            data = json.loads(self.registry_path.read_text(encoding="utf-8"))
            return ModelRegistry.from_dict(data)
        except (json.JSONDecodeError, OSError, KeyError, TypeError, ValueError):
            return ModelRegistry()

    def list_entries(self, role: str | None = None) -> list[ModelEntry]:
        registry = self.load()
        return registry.entries_by_role(role) if role else list(registry.entries)

    def get_active_entry(self, role: str) -> ModelEntry | None:
        registry = self.load()
        entry_id = registry.active_by_role.get(role)
        if entry_id is None:
            return None
        return registry.entry_by_id(entry_id)

    def resolve_active_path(self, role: str) -> Path | None:
        """Caminho absoluto do modelo ativo para `role`, so se a entrada
        existir no registry E o arquivo/pasta ainda existir em disco.
        Retorna None em qualquer outro caso -- quem chama decide o
        fallback (ver resolve_default_model_path em runtime.py/demo_viewer.py).
        """
        entry = self.get_active_entry(role)
        if entry is None or not entry.enabled:
            return None
        candidate = self._resolve_entry_path(entry)
        ok, _reason = self.validate_entry(entry)
        return candidate if ok else None

    def build_profile(self, roles: list[str]) -> ModelProfile:
        return ModelProfile(active_by_role={role: self.get_active_entry(role) for role in roles})

    def _resolve_entry_path(self, entry: ModelEntry) -> Path:
        path = Path(entry.path)
        return path if path.is_absolute() else (self.root / path)

    # -- validacao ------------------------------------------------------

    def validate_entry(self, entry: ModelEntry) -> tuple[bool, str]:
        path = self._resolve_entry_path(entry)
        if entry.backend == "openvino":
            if not path.is_dir() and path.suffix.lower() != ".xml":
                return False, f"Caminho OpenVINO invalido: {path}"
            xml_path = path if path.suffix.lower() == ".xml" else self._find_xml_in_dir(path)
            if xml_path is None or not xml_path.is_file():
                return False, f"Arquivo .xml nao encontrado em {path}"
            bin_path = xml_path.with_suffix(".bin")
            if not bin_path.is_file():
                return False, f"Arquivo .bin correspondente nao encontrado: {bin_path}"
            return True, "ok"
        if entry.backend == "pt":
            if not path.is_file():
                return False, f"Arquivo .pt nao encontrado: {path}"
            return True, "ok"
        return False, f"Backend desconhecido: {entry.backend}"

    @staticmethod
    def _find_xml_in_dir(directory: Path) -> Path | None:
        if not directory.is_dir():
            return None
        matches = sorted(directory.glob("*.xml"))
        return matches[0] if matches else None

    def test_load(
        self,
        entry: ModelEntry,
        *,
        runner_factory: Callable[[str], Any] | None = None,
    ) -> tuple[bool, str]:
        """Tenta carregar o modelo de verdade (mesmo loader de producao,
        PediatricsDetectorMvpRunner) so quando chamado explicitamente --
        nunca automaticamente so para listar ou validar presenca de arquivo.
        """
        ok, reason = self.validate_entry(entry)
        if not ok:
            return False, reason
        path = self._resolve_entry_path(entry)
        load_path = path if entry.backend == "pt" else (
            path if path.suffix.lower() == ".xml" else self._find_xml_in_dir(path)
        )
        try:
            if runner_factory is not None:
                runner_factory(str(load_path))
            else:
                from .detector_mvp import PediatricsDetectorMvpRunner

                PediatricsDetectorMvpRunner(load_path)
            return True, "Modelo carregado com sucesso."
        except FileNotFoundError as exc:
            return False, f"Arquivo nao encontrado: {exc}"
        except PermissionError as exc:
            return False, f"Sem permissao para ler o arquivo: {exc}"
        except Exception as exc:  # modelo corrompido, formato invalido, etc.
            return False, f"Falha ao carregar modelo ({type(exc).__name__}): {exc}"

    # -- escrita ------------------------------------------------------

    def save(self, registry: ModelRegistry) -> None:
        self.registry_path.parent.mkdir(parents=True, exist_ok=True)
        self._backup_existing()
        registry.updated_at = _now_iso()
        text = json.dumps(registry.to_dict(), indent=2, ensure_ascii=False)
        self.registry_path.write_text(text, encoding="utf-8")

    def _backup_existing(self) -> None:
        if not self.registry_path.is_file():
            return
        backup_path = self.registry_path.with_suffix(self.registry_path.suffix + ".bak")
        try:
            shutil.copy2(self.registry_path, backup_path)
        except OSError:
            pass  # backup e best-effort; nao pode impedir salvar a mudanca real

    def set_active(self, role: str, entry_id: str) -> ModelEntry:
        registry = self.load()
        entry = registry.entry_by_id(entry_id)
        if entry is None:
            raise ModelImportError(f"Entrada '{entry_id}' nao existe no registry.")
        if entry.role != role:
            raise ModelImportError(
                f"Entrada '{entry_id}' e do papel '{entry.role}', nao '{role}'."
            )
        ok, reason = self.validate_entry(entry)
        if not ok:
            raise ModelImportError(f"Nao e possivel ativar: {reason}")
        registry.active_by_role[role] = entry_id
        self.save(registry)
        return entry

    def import_pt(self, source_path: Path, *, name: str, version: str, role: str) -> ModelEntry:
        source_path = Path(source_path)
        if source_path.suffix.lower() != ".pt":
            raise ModelImportError(f"Esperado arquivo .pt, recebido: {source_path.suffix}")
        if not source_path.is_file():
            raise ModelImportError(f"Arquivo nao encontrado: {source_path}")

        self.models_dir.mkdir(parents=True, exist_ok=True)
        entry_id = uuid.uuid4().hex[:12]
        dest_name = f"{_slugify(role)}_{_slugify(name)}_{_slugify(version)}_{entry_id}.pt"
        dest_path = self.models_dir / dest_name
        try:
            shutil.copy2(source_path, dest_path)
        except PermissionError as exc:
            raise ModelImportError(f"Sem permissao para copiar o modelo: {exc}") from exc
        except OSError as exc:
            raise ModelImportError(f"Falha ao copiar o modelo: {exc}") from exc

        entry = ModelEntry(
            id=entry_id,
            name=name,
            version=version,
            role=role,
            backend="pt",
            path=str(dest_path.relative_to(self.root)),
            created_at=_now_iso(),
            sha256=sha256_of_file(dest_path),
            enabled=True,
        )
        registry = self.load()
        registry.entries.append(entry)
        self.save(registry)
        return entry

    def import_openvino(
        self, xml_path: Path, *, name: str, version: str, role: str
    ) -> ModelEntry:
        xml_path = Path(xml_path)
        if xml_path.suffix.lower() != ".xml":
            raise ModelImportError(f"Esperado arquivo .xml, recebido: {xml_path.suffix}")
        if not xml_path.is_file():
            raise ModelImportError(f"Arquivo .xml nao encontrado: {xml_path}")
        bin_path = xml_path.with_suffix(".bin")
        if not bin_path.is_file():
            raise ModelImportError(
                f"Par OpenVINO incompleto: falta o .bin correspondente ({bin_path})."
            )

        self.models_dir.mkdir(parents=True, exist_ok=True)
        entry_id = uuid.uuid4().hex[:12]
        dest_dir = self.models_dir / f"{_slugify(role)}_{_slugify(name)}_{_slugify(version)}_{entry_id}"
        dest_dir.mkdir(parents=True, exist_ok=False)
        try:
            shutil.copy2(xml_path, dest_dir / xml_path.name)
            shutil.copy2(bin_path, dest_dir / bin_path.name)
            # metadata.yaml e opcional (guarda task/names do export do
            # ultralytics), mas copia se existir junto do .xml de origem.
            metadata_path = xml_path.with_name("metadata.yaml")
            if metadata_path.is_file():
                shutil.copy2(metadata_path, dest_dir / metadata_path.name)
        except PermissionError as exc:
            raise ModelImportError(f"Sem permissao para copiar o modelo: {exc}") from exc
        except OSError as exc:
            raise ModelImportError(f"Falha ao copiar o modelo: {exc}") from exc

        entry = ModelEntry(
            id=entry_id,
            name=name,
            version=version,
            role=role,
            backend="openvino",
            path=str(dest_dir.relative_to(self.root)),
            created_at=_now_iso(),
            sha256=sha256_of_file(dest_dir / bin_path.name),
            enabled=True,
        )
        registry = self.load()
        registry.entries.append(entry)
        self.save(registry)
        return entry
