"""Ambiente do Alembic. A conexao vem de `Database.migrar()` (config.attributes["connection"])."""
from __future__ import annotations

from alembic import context

from src.jutta_ped.painel import models  # noqa: F401  (registra as tabelas no metadata)
from src.jutta_ped.painel.db import Base

config = context.config
target_metadata = Base.metadata


def run_migrations_online() -> None:
    connection = config.attributes["connection"]
    context.configure(connection=connection, target_metadata=target_metadata, render_as_batch=True, compare_type=True)
    with context.begin_transaction():
        context.run_migrations()


run_migrations_online()
