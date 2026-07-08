"""Tipos de dominio do registry de modelos (runtime/model_registry.json).

Puro dado, sem I/O e sem dependencia de Qt/ultralytics -- fica em
modulo/pediatria (camada "core", compartilhada por service e ui) de
proposito, seguindo a mesma regra de dependencia ja usada no resto do
projeto (ver plugin/README.md): ui -> service -> core, nunca o contrario.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

ModelBackend = Literal["pt", "openvino"]
ModelRole = Literal["detector", "specialist", "master", "crop"]

# Papeis realmente usados hoje pelo pipeline (ver PediatriaServiceConfig):
# "detector" = detector generico de pessoa (DEFAULT_PERSON_MODEL_PATH),
# "specialist" = classificador adult/child (DEFAULT_MODEL_PATH). "master" e
# "crop" ficam reservados no tipo para papeis futuros, sem uso hoje.


@dataclass
class ModelEntry:
    id: str
    name: str
    version: str
    role: str
    backend: str
    path: str
    created_at: str
    sha256: str | None = None
    enabled: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "version": self.version,
            "role": self.role,
            "backend": self.backend,
            "path": self.path,
            "created_at": self.created_at,
            "sha256": self.sha256,
            "enabled": self.enabled,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ModelEntry":
        return cls(
            id=str(data["id"]),
            name=str(data.get("name", "")),
            version=str(data.get("version", "")),
            role=str(data.get("role", "")),
            backend=str(data.get("backend", "")),
            path=str(data["path"]),
            created_at=str(data.get("created_at", "")),
            sha256=data.get("sha256"),
            enabled=bool(data.get("enabled", True)),
        )


@dataclass
class ModelRegistry:
    version: int = 1
    updated_at: str = ""
    active_by_role: dict[str, str] = field(default_factory=dict)
    entries: list[ModelEntry] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "updated_at": self.updated_at,
            "active_by_role": dict(self.active_by_role),
            "entries": [entry.to_dict() for entry in self.entries],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ModelRegistry":
        return cls(
            version=int(data.get("version", 1)),
            updated_at=str(data.get("updated_at", "")),
            active_by_role=dict(data.get("active_by_role", {})),
            entries=[ModelEntry.from_dict(item) for item in data.get("entries", [])],
        )

    def entry_by_id(self, entry_id: str) -> ModelEntry | None:
        return next((entry for entry in self.entries if entry.id == entry_id), None)

    def entries_by_role(self, role: str) -> list[ModelEntry]:
        return [entry for entry in self.entries if entry.role == role]


@dataclass
class ModelProfile:
    """Snapshot resolvido: entrada ativa (se houver) por papel, no momento
    da consulta. E o que a tela tecnica mostra como "modelo ativo atual" e
    o que o resolvedor de path usa para decidir o que carregar.
    """

    active_by_role: dict[str, ModelEntry | None] = field(default_factory=dict)
