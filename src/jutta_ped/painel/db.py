from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker


class Base(DeclarativeBase):
    pass


class Database:
    """Engine + fabrica de sessoes do painel.

    Uma sessao por unidade de trabalho (nunca compartilhada entre threads): o
    painel tem threads de camera/dispatcher alem das requisicoes HTTP.
    """

    def __init__(self, url: str) -> None:
        is_sqlite = url.startswith("sqlite")
        if is_sqlite and ":memory:" not in url:
            Path(url.split("///", 1)[-1]).parent.mkdir(parents=True, exist_ok=True)
        self.engine: Engine = create_engine(
            url,
            connect_args={"check_same_thread": False} if is_sqlite else {},
        )
        if is_sqlite:
            @event.listens_for(self.engine, "connect")
            def _pragmas(dbapi_connection, _record) -> None:  # noqa: ANN001
                cursor = dbapi_connection.cursor()
                cursor.execute("PRAGMA foreign_keys=ON")
                cursor.execute("PRAGMA journal_mode=WAL")
                cursor.close()

        self.session_factory = sessionmaker(self.engine, expire_on_commit=False)

    def migrar(self) -> None:
        """Leva o banco ao esquema atual com o Alembic.

        Banco vazio: aplica todas as revisoes. Banco legado criado por `create_all` (tabelas sem
        `alembic_version`): marca a revisao base como aplicada e segue dali, sem recriar nada.
        """
        from alembic import command
        from alembic.config import Config
        from sqlalchemy import inspect

        cfg = Config()
        cfg.set_main_option("script_location", str(Path(__file__).parent / "migracoes"))
        with self.engine.begin() as con:
            cfg.attributes["connection"] = con
            tabelas = set(inspect(con).get_table_names())
            if "alembic_version" not in tabelas and "usuarios" in tabelas:
                command.stamp(cfg, "0001")
            command.upgrade(cfg, "head")

    def create_all(self) -> None:
        """Compatibilidade: antes criava as tabelas direto do metadata; agora aplica as migracoes."""
        self.migrar()

    @contextmanager
    def session(self) -> Iterator[Session]:
        with self.session_factory() as session:
            try:
                yield session
                session.commit()
            except Exception:
                session.rollback()
                raise

    def dispose(self) -> None:
        self.engine.dispose()
