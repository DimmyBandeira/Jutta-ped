from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any
from urllib.parse import quote, urlsplit, urlunsplit

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from src.jutta_ped.painel.config_service import ler_config
from src.jutta_ped.painel.deps import DbDep, render, usuario_atual, verifica_csrf
from src.jutta_ped.painel.go2rtc import PREFIXO_STREAM, nome_stream
from src.jutta_ped.painel.models import Camera, Fabricante, Usuario

router = APIRouter(prefix="/painel", dependencies=[Depends(verifica_csrf)])

_LOOPBACK = {"127.0.0.1", "localhost", "::1"}
SSE_INTERVALO_PADRAO = 15.0

_OVERLAY_VAZIO: dict[str, Any] = {
    "type": "overlay_bboxes",
    "objects": [],
    "frame_w": None,
    "frame_h": None,
    "frame_age_ms": None,
}


def ajustar_host(url_publica: str, request: Request) -> str:
    """Troca host de loopback pelo host com que o operador abriu o painel.

    A URL do go2rtc costuma ficar como 127.0.0.1 (correta no servidor); num navegador
    de outra maquina ela nao funciona. Mantem esquema e porta configurados.
    """
    partes = urlsplit(url_publica)
    host_painel = request.url.hostname or ""
    if partes.hostname in _LOOPBACK and host_painel and host_painel not in _LOOPBACK:
        host = f"[{host_painel}]" if ":" in host_painel else host_painel
        netloc = f"{host}:{partes.port}" if partes.port else host
        partes = partes._replace(netloc=netloc)
    return urlunsplit(partes).rstrip("/")


def estado_ia(camera: Camera, servicos) -> tuple[bool, str | None, str | None]:
    """(ia_ativa, estado, erro) para exibir no card. Estados extras alem do status operacional:
    iniciando, aguardando_stream, erro, limite."""
    if not camera.ia_pediatria or servicos.ia is None:
        return False, None, None
    info = servicos.ia.estado(camera.id)
    estado = info["estado"]
    if estado == "desligada":
        return True, "iniciando", None
    if estado != "rodando":
        return True, estado, info.get("erro")
    status = servicos.ia.gerente.camera_status(f"{PREFIXO_STREAM}{camera.id}")
    if not status.get("active"):
        return True, "ERROR" if status.get("operational_status") == "ERROR" else "iniciando", status.get("last_error")
    return True, status.get("operational_status") or "ANALISANDO", None


def visao_camera(camera: Camera, cfg: dict[str, Any], servicos, request: Request) -> SimpleNamespace:
    nome = nome_stream(camera)
    pronta = bool(nome) and (not camera.gerenciada_pelo_sync or camera.go2rtc_sync == "ok")
    ia_ativa, ia_estado, ia_erro = estado_ia(camera, servicos)
    if camera.fabricante == Fabricante.go2rtc_teste:
        sync_texto = "stream de teste"
    else:
        sync_texto = {"ok": "stream sincronizado", "erro": "erro ao sincronizar go2rtc"}.get(camera.go2rtc_sync, "sincronizando go2rtc…")
    return SimpleNamespace(
        id=camera.id,
        nome=camera.nome,
        player_url=f"{ajustar_host(cfg['go2rtc_public_url'], request)}/stream.html?src={quote(nome)}" if pronta else None,
        ia_ativa=ia_ativa,
        ia_estado=ia_estado,
        ia_erro=ia_erro,
        go2rtc_sync=camera.go2rtc_sync,
        sync_texto=sync_texto,
    )


def _cameras(db: Session) -> list[Camera]:
    return list(db.scalars(select(Camera).order_by(Camera.nome)).unique().all())


@router.get("/monitoramento")
def monitoramento(request: Request, usuario: Usuario = Depends(usuario_atual), db: Session = DbDep):
    servicos = request.app.state.painel
    cfg = ler_config(db)
    cameras = [visao_camera(c, cfg, servicos, request) for c in _cameras(db)]
    return render(request, "monitoramento.html", usuario, cameras=cameras, ativo="monitoramento")


@router.get("/cameras/{camera_id}/ia-estado")
def ia_estado(camera_id: int, request: Request, usuario: Usuario = Depends(usuario_atual), db: Session = DbDep):
    camera = db.get(Camera, camera_id)
    if camera is None:
        return render(request, "partials/ia_estado.html", usuario, c=SimpleNamespace(ia_ativa=False, ia_estado=None, ia_erro=None))
    visao = visao_camera(camera, ler_config(db), request.app.state.painel, request)
    return render(request, "partials/ia_estado.html", usuario, c=visao)


@router.get("/cameras/{camera_id}/overlay")
def overlay(camera_id: int, request: Request, usuario: Usuario = Depends(usuario_atual)):
    """Ultimo overlay (bboxes) da camera, no contrato plugin/schemas/overlay.schema.json.
    Sem IA rodando devolve overlay vazio (o cliente so limpa o canvas)."""
    ia = request.app.state.painel.ia
    if ia is None:
        return _OVERLAY_VAZIO
    gerente = ia.gerente
    status = gerente.camera_status(f"{PREFIXO_STREAM}{camera_id}")
    sessao = status.get("session_id")
    if not sessao or not status.get("active"):
        return _OVERLAY_VAZIO
    try:
        return gerente.overlay_latest(sessao)
    except KeyError:
        return _OVERLAY_VAZIO


@router.get("/eventos/recentes")
def eventos_recentes(request: Request, usuario: Usuario = Depends(usuario_atual)):
    # Alimentado pelo banco de alertas na fase 4.
    return render(request, "partials/eventos_recentes.html", usuario, alertas=[])


@router.get("/stream")
async def stream(request: Request, usuario: Usuario = Depends(usuario_atual)):
    """SSE do painel. Nesta fase so mantem a conexao viva (heartbeat); os popups de alerta
    passam a sair por aqui na fase 4."""
    intervalo = float(getattr(request.app.state, "painel_sse_intervalo", SSE_INTERVALO_PADRAO))

    max_pings = getattr(request.app.state, "painel_sse_max_pings", None)  # so os testes limitam (TestClient nao le stream infinito)

    async def gerar():
        yield "retry: 3000\n\n"
        enviados = 0
        while not await request.is_disconnected():
            if max_pings is not None and enviados >= max_pings:
                return
            await asyncio.sleep(intervalo)
            yield ": ping\n\n"
            enviados += 1

    return StreamingResponse(gerar(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
