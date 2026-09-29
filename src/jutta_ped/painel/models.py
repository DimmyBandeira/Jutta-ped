from __future__ import annotations

import enum
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

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


class Fabricante(str, enum.Enum):
    intelbras = "intelbras"
    hikvision = "hikvision"
    rtsp_generico = "rtsp_generico"
    go2rtc_teste = "go2rtc_teste"


class StreamTipo(str, enum.Enum):
    principal = "principal"
    extra = "extra"


class TipoDestino(str, enum.Enum):
    gpsindoor = "gpsindoor"
    wifiptt = "wifiptt"
    webhook = "webhook"


class Destino(Base):
    __tablename__ = "destinos"

    id: Mapped[int] = mapped_column(primary_key=True)
    nome: Mapped[str] = mapped_column(String(80), unique=True)
    tipo: Mapped[TipoDestino] = mapped_column(Enum(TipoDestino, native_enum=False, length=16))
    ativo: Mapped[bool] = mapped_column(Boolean, default=False)  # nasce desabilitado (ver docs/painel_web.md §7)
    config_json_enc: Mapped[str | None] = mapped_column(Text, nullable=True)


class CameraDestino(Base):
    __tablename__ = "camera_destinos"

    camera_id: Mapped[int] = mapped_column(ForeignKey("cameras.id", ondelete="CASCADE"), primary_key=True)
    destino_id: Mapped[int] = mapped_column(ForeignKey("destinos.id", ondelete="CASCADE"), primary_key=True)
    destino: Mapped[Destino] = relationship(lazy="joined")


class Camera(Base):
    __tablename__ = "cameras"

    id: Mapped[int] = mapped_column(primary_key=True)
    nome: Mapped[str] = mapped_column(String(80), unique=True)
    fabricante: Mapped[Fabricante] = mapped_column(Enum(Fabricante, native_enum=False, length=16))
    ip: Mapped[str | None] = mapped_column(String(253), nullable=True)
    porta: Mapped[int] = mapped_column(Integer, default=554)
    canal: Mapped[int] = mapped_column(Integer, default=1)
    stream_tipo: Mapped[StreamTipo] = mapped_column(Enum(StreamTipo, native_enum=False, length=16), default=StreamTipo.principal)
    usuario: Mapped[str | None] = mapped_column(String(64), nullable=True)
    senha_enc: Mapped[str | None] = mapped_column(Text, nullable=True)
    rtsp_url_enc: Mapped[str | None] = mapped_column(Text, nullable=True)
    go2rtc_stream_name: Mapped[str | None] = mapped_column(String(80), nullable=True)
    identificador_externo: Mapped[str | None] = mapped_column(String(64), nullable=True)
    ia_pediatria: Mapped[bool] = mapped_column(Boolean, default=True)
    go2rtc_sync: Mapped[str] = mapped_column(String(16), default="pendente")  # ok | pendente | erro
    go2rtc_sync_erro: Mapped[str | None] = mapped_column(String(200), nullable=True)
    go2rtc_sync_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    criado_em: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    destinos: Mapped[list[CameraDestino]] = relationship(cascade="all, delete-orphan", lazy="selectin")

    @property
    def gerenciada_pelo_sync(self) -> bool:
        """Cameras cujo stream o painel cria no go2rtc (as de teste usam um stream que ja existe la)."""
        return self.fabricante != Fabricante.go2rtc_teste

    @property
    def status(self):  # placeholder: status online real chega com o live view (fase 3)
        return None
