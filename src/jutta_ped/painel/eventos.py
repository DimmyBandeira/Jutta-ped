from __future__ import annotations

import asyncio
import json
import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from sqlalchemy import select

from src.jutta_ped.painel.db import Database
from src.jutta_ped.painel.go2rtc import PREFIXO_STREAM
from src.jutta_ped.painel.models import Camera, EventoPediatria

logger = logging.getLogger("jutta_ped.painel.eventos")

TIPOS_DE_ALERTA = {"popup_alert", "popup_suppressed"}
JANELA_POPUPS_PENDENTES = timedelta(hours=12)   # popup sem ciencia mais velho que isso nao volta a aparecer
MAX_POPUPS_PENDENTES = 10
TIPOS_DE_EVIDENCIA = {"crop", "annotated", "frame"}
_EXTENSOES_DE_IMAGEM = {".jpg", ".jpeg", ".png"}
_TAMANHO_FILA = 200


def formatar_sse(evento: str, dado: str, id_evento: int | None = None) -> str:
    """Quadro Server-Sent Events. `dado` multilinha vira varias linhas `data:`."""
    linhas = [f"id: {id_evento}"] if id_evento is not None else []
    linhas.append(f"event: {evento}")
    linhas.extend(f"data: {linha}" for linha in (dado.splitlines() or [""]))
    return "\n".join(linhas) + "\n\n"


@dataclass
class _Inscricao:
    loop: asyncio.AbstractEventLoop
    fila: asyncio.Queue


@dataclass
class Mensagem:
    """O que circula pelo barramento. `visao` e o alerta (popup) ou o event_id (ack)."""

    nome: str                # "popup" | "ack"
    id_evento: int
    visao: Any


def _camera_id_do_runtime(valor: Any) -> int | None:
    texto = str(valor or "")
    if texto.startswith(PREFIXO_STREAM) and texto[len(PREFIXO_STREAM):].isdigit():
        return int(texto[len(PREFIXO_STREAM):])
    return None


def _quando(valor: datetime) -> str:
    return valor.strftime("%H:%M:%S")


def evidencia_url(evento: EventoPediatria, tipo: str) -> str | None:
    try:
        existe = tipo in json.loads(evento.evidencias_json or "{}")
    except ValueError:
        existe = False
    return f"/painel/evidence/{evento.event_id}/{tipo}" if existe else None


def para_visao(evento: EventoPediatria) -> SimpleNamespace:
    """Dados de um alerta para os templates (popup e tabela de alertas)."""
    return SimpleNamespace(
        event_id=evento.event_id,
        estado=evento.estado,
        camera_nome=evento.camera_nome,
        quando_fmt=_quando(evento.criado_em),
        criado_em=evento.criado_em,
        confianca=evento.confianca,
        motivo=evento.motivo,
        suprimido=evento.suprimido,
        reconhecido_em=evento.reconhecido_em,
        reconhecido_por=evento.reconhecido_por,
        entregas=list(evento.entregas),
        crop_url=evidencia_url(evento, "crop"),
        annotated_url=evidencia_url(evento, "annotated"),
    )


class PainelEventos:
    """Alertas ao vivo: recebe eventos das threads de IA, grava no banco e empurra para os navegadores.

    - `on_runtime_event` e chamado NA THREAD DE INFERENCIA: faz so um insert rapido e notifica.
    - Cada navegador conectado ao SSE e uma `_Inscricao` (fila asyncio do seu loop); a entrega a
      partir de outra thread usa `loop.call_soon_threadsafe`.
    - Fila cheia (navegador lento) descarta; o replay por `Last-Event-ID` cobre o que faltar.
    """

    def __init__(self, database: Database, evidence_roots: list[Path], *, relogio: Callable[[], datetime] = datetime.now) -> None:
        self._database = database
        self._roots = [Path(r).resolve() for r in evidence_roots]
        self._relogio = relogio
        self._inscricoes: list[_Inscricao] = []
        self._lock = threading.Lock()
        self.ouvintes_pos_registro: list[Callable[[int], None]] = []   # ex.: dispatcher de destinos (fase 5)

    # -- entrada ------------------------------------------------------------------------------

    def on_runtime_event(self, payload: dict[str, Any]) -> None:
        if payload.get("event_type") in TIPOS_DE_ALERTA:
            try:
                self.registrar(payload)
            except Exception:  # noqa: BLE001 -- nunca derruba a analise
                logger.exception("Falha ao registrar alerta")

    def registrar(self, payload: dict[str, Any]) -> SimpleNamespace | None:
        """Grava o alerta (idempotente por event_id) e, se nao foi suprimido, publica o popup."""
        suprimido = payload.get("event_type") == "popup_suppressed"
        event_id = f"{payload.get('session_id')}_{payload.get('frame_id')}"
        camera_id = _camera_id_do_runtime(payload.get("camera_id"))
        filho = payload.get("selected_child") or {}
        confianca = payload.get("child_confidence")
        if confianca is None:
            confianca = filho.get("confidence")
        with self._database.session() as db:
            if db.scalar(select(EventoPediatria.id).where(EventoPediatria.event_id == event_id)) is not None:
                return None
            camera = db.get(Camera, camera_id) if camera_id is not None else None
            evento = EventoPediatria(
                event_id=event_id,
                camera_id=camera_id,
                camera_nome=camera.nome if camera else str(payload.get("camera_id") or "?"),
                session_id=str(payload.get("session_id") or ""),
                estado=str(payload.get("stable_state") or ""),
                confianca=float(confianca) if confianca is not None else None,
                motivo=(str(payload.get("reason"))[:200] if payload.get("reason") else None),
                suprimido=suprimido,
                evidencias_json=json.dumps({k: v for k, v in (payload.get("evidence_paths") or {}).items() if v}),
                criado_em=self._relogio(),
            )
            db.add(evento)
            db.flush()
            visao, id_db = para_visao(evento), evento.id
        if not suprimido:
            self._publicar(Mensagem("popup", id_db, visao))
        for ouvinte in list(self.ouvintes_pos_registro):
            try:
                ouvinte(id_db)
            except Exception:  # noqa: BLE001
                logger.exception("Ouvinte pos-registro falhou")
        return visao

    # -- ciencia -------------------------------------------------------------------------------

    def reconhecer(self, event_id: str, login: str) -> bool | None:
        """Da ciencia. `None` = alerta inexistente; `True` = registrou agora; `False` = ja tinha ciencia."""
        with self._database.session() as db:
            evento = db.scalar(select(EventoPediatria).where(EventoPediatria.event_id == event_id))
            if evento is None:
                return None
            novo = evento.reconhecido_em is None
            if novo:
                evento.reconhecido_em, evento.reconhecido_por = self._relogio(), login
            id_db = evento.id
        if novo:
            self._publicar(Mensagem("ack", id_db, event_id))
        return novo

    # -- consultas -----------------------------------------------------------------------------

    def _consulta_pendentes(self):
        limite = self._relogio() - JANELA_POPUPS_PENDENTES
        return select(EventoPediatria).where(
            EventoPediatria.suprimido.is_(False),
            EventoPediatria.reconhecido_em.is_(None),
            EventoPediatria.criado_em >= limite,
        )

    def pendentes(self, apos_id: int | None = None) -> list[SimpleNamespace]:
        """Popups sem ciencia (mais antigos primeiro). Com `apos_id`, so os posteriores (replay do SSE)."""
        consulta = self._consulta_pendentes()
        if apos_id is not None:
            consulta = consulta.where(EventoPediatria.id > apos_id)
        with self._database.session() as db:
            eventos = list(db.scalars(consulta.order_by(EventoPediatria.id.desc()).limit(MAX_POPUPS_PENDENTES)).unique())
            visoes = [(e.id, para_visao(e)) for e in reversed(eventos)]
        return [SimpleNamespace(id_db=i, **vars(v)) for i, v in visoes]

    def recentes(self, limite: int = 15) -> list[SimpleNamespace]:
        with self._database.session() as db:
            eventos = db.scalars(select(EventoPediatria).order_by(EventoPediatria.id.desc()).limit(limite)).unique().all()
            return [para_visao(e) for e in eventos]

    def caminho_evidencia(self, event_id: str, tipo: str) -> Path | None:
        """Arquivo de evidencia do alerta. O caminho vem do BANCO (nunca do cliente) e so e servido
        se estiver dentro de uma das pastas de evidencia configuradas."""
        if tipo not in TIPOS_DE_EVIDENCIA:
            return None
        with self._database.session() as db:
            evento = db.scalar(select(EventoPediatria).where(EventoPediatria.event_id == event_id))
            bruto = json.loads(evento.evidencias_json or "{}").get(tipo) if evento else None
        if not bruto:
            return None
        try:
            arquivo = Path(bruto).resolve()
        except (OSError, ValueError):
            return None
        if arquivo.suffix.lower() not in _EXTENSOES_DE_IMAGEM or not arquivo.is_file():
            return None
        return arquivo if any(arquivo.is_relative_to(raiz) for raiz in self._roots) else None

    # -- assinantes (SSE) ----------------------------------------------------------------------

    def inscrever(self, loop: asyncio.AbstractEventLoop) -> _Inscricao:
        inscricao = _Inscricao(loop=loop, fila=asyncio.Queue(maxsize=_TAMANHO_FILA))
        with self._lock:
            self._inscricoes.append(inscricao)
        return inscricao

    def cancelar(self, inscricao: _Inscricao) -> None:
        with self._lock:
            if inscricao in self._inscricoes:
                self._inscricoes.remove(inscricao)

    @property
    def total_inscritos(self) -> int:
        with self._lock:
            return len(self._inscricoes)

    def _publicar(self, mensagem: Mensagem) -> None:
        with self._lock:
            inscricoes = list(self._inscricoes)
        for inscricao in inscricoes:
            try:
                inscricao.loop.call_soon_threadsafe(self._entregar, inscricao, mensagem)
            except RuntimeError:  # loop ja fechado (navegador saiu durante a publicacao)
                self.cancelar(inscricao)

    @staticmethod
    def _entregar(inscricao: _Inscricao, mensagem: Mensagem) -> None:
        try:
            inscricao.fila.put_nowait(mensagem)
        except asyncio.QueueFull:
            logger.warning("Fila SSE cheia: mensagem descartada (o navegador recupera por Last-Event-ID)")
