from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse

from src.jutta_ped.painel import encerramento
from src.jutta_ped.painel.deps import registrar_auditoria, render, templates, usuario_atual, verifica_csrf, DbDep
from src.jutta_ped.painel.eventos import formatar_sse
from src.jutta_ped.painel.models import Usuario

router = APIRouter(prefix="/painel", dependencies=[Depends(verifica_csrf)])

SSE_INTERVALO_PADRAO = 15.0
_EVIDENCIA_CACHE = "private, max-age=3600"


def _eventos(request: Request):
    return request.app.state.painel.eventos


def _html_do_popup(visao: SimpleNamespace) -> str:
    return templates.get_template("partials/popup.html").render(ev=visao)


@router.get("/stream")
async def stream(request: Request, usuario: Usuario = Depends(usuario_atual)):
    """SSE do painel: `event: popup` (HTML pronto do alerta) e `event: ack` (event_id que outro
    operador reconheceu). Reconexao com `Last-Event-ID` reenvia os popups sem ciencia perdidos.
    A cada `intervalo` segundos envia um comentario (heartbeat) para manter a conexao viva."""
    intervalo = float(getattr(request.app.state, "painel_sse_intervalo", SSE_INTERVALO_PADRAO))
    max_pings = getattr(request.app.state, "painel_sse_max_pings", None)  # so os testes limitam (TestClient nao le stream infinito)
    eventos = _eventos(request)
    ultimo = request.headers.get("last-event-id", "")
    inscricao = eventos.inscrever(asyncio.get_running_loop())   # antes do replay: nada se perde no intervalo

    async def gerar():
        try:
            yield "retry: 3000\n\n"
            if ultimo.isdigit():
                for visao in await asyncio.to_thread(eventos.pendentes, int(ultimo)):
                    yield formatar_sse("popup", _html_do_popup(visao), visao.id_db)
            enviados, ultimo_ping = 0, time.monotonic()
            espera = min(intervalo, 1.0)       # acorda ao menos 1x/s: percebe o servico encerrando sem esperar o heartbeat
            while not await request.is_disconnected() and not encerramento.deve_encerrar():
                try:
                    mensagem = await asyncio.wait_for(inscricao.fila.get(), timeout=espera)
                except asyncio.TimeoutError:
                    if max_pings is not None and enviados >= max_pings:
                        return
                    if time.monotonic() - ultimo_ping >= intervalo:
                        yield ": ping\n\n"
                        enviados, ultimo_ping = enviados + 1, time.monotonic()
                    continue
                if mensagem.nome == "popup":
                    yield formatar_sse("popup", _html_do_popup(mensagem.visao), mensagem.id_evento)
                else:
                    yield formatar_sse("ack", str(mensagem.visao), mensagem.id_evento)
        finally:
            eventos.cancelar(inscricao)

    return StreamingResponse(gerar(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.post("/popups/{event_id}/ack")
def reconhecer(event_id: str, request: Request, operador: Usuario = Depends(usuario_atual), db=DbDep):
    """Ciencia do alerta. Resposta vazia: o HTMX troca o popup por nada. Os demais navegadores
    abertos recebem `event: ack` e tambem removem o popup."""
    resultado = _eventos(request).reconhecer(event_id, operador.login)
    if resultado is None:
        raise HTTPException(status_code=404, detail="Alerta nao encontrado.")
    if resultado:
        registrar_auditoria(db, operador.login, "alerta_reconhecido", event_id)
    return HTMLResponse("")


@router.get("/evidence/{event_id}/{tipo}")
def evidencia(event_id: str, tipo: str, request: Request, operador: Usuario = Depends(usuario_atual)):
    arquivo = _eventos(request).caminho_evidencia(event_id, tipo)
    if arquivo is None:
        raise HTTPException(status_code=404, detail="Evidencia nao encontrada.")
    tipo_midia = "image/png" if arquivo.suffix.lower() == ".png" else "image/jpeg"
    return FileResponse(arquivo, media_type=tipo_midia, headers={"Cache-Control": _EVIDENCIA_CACHE})


@router.get("/eventos/recentes")
def eventos_recentes(request: Request, operador: Usuario = Depends(usuario_atual)):
    return render(request, "partials/eventos_recentes.html", operador, alertas=_eventos(request).recentes())
