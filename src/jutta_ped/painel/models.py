from __future__ import annotations

import enum
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Enum, String
from sqlalchemy.orm import Mapped, mapped_column

from src.jutta_ped.painel.db import Base


class Papel(str, enum.Enum):
    admin = "admin"
    comum = "comum"


class Usuario(Base):
    __tablename__ = "usuarios"

    id: Mapped[int] = mapped_column(primary_key=True)
    login: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    senha_hash: Mapped[str] = mapped_column(String(256))
    papel: Mapped[Papel] = mapped_column(Enum(Papel, native_enum=False, length=16), default=Papel.comum)
    ativo: Mapped[bool] = mapped_column(Boolean, default=True)
    criado_em: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)

    @property
    def eh_admin(self) -> bool:
        return self.papel == Papel.admin


class Auditoria(Base):
    __tablename__ = "auditoria"

    id: Mapped[int] = mapped_column(primary_key=True)
    usuario_login: Mapped[str | None] = mapped_column(String(32), nullable=True)
    acao: Mapped[str] = mapped_column(String(64), index=True)
    alvo: Mapped[str | None] = mapped_column(String(128), nullable=True)
    criado_em: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, index=True)


class Configuracao(Base):
    __tablename__ = "configuracoes"

    chave: Mapped[str] = mapped_column(String(64), primary_key=True)
    valor: Mapped[str] = mapped_column(String(1024), default="")
