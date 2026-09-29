from __future__ import annotations

from collections.abc import Iterator
from html import escape as html_escape
from pathlib import Path
from typing import Any

from fastapi import Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from src.jutta_ped.painel.db import Database
from src.jutta_ped.painel.models import Auditoria, Usuario
from src.jutta_ped.painel.security import novo_token, tokens_iguais

WEB_DIR = Path(__file__).resolve().parents[3] / "web"
templates = Jinja2Templates(directory=str(WEB_DIR / "templates"))

_METODOS_SEGUROS = {"GET", "HEAD", "OPTIONS"}


class NaoAutenticado(Exception):
    pass


class SemPermissao(Exception):
    pass


def get_db(request: Request) -> Iterator[Session]:
    database: Database = request.app.state.painel_db
    with database.session() as session:
        yield session


# scope="function": a transacao fecha (commit) logo apos o endpoint, antes da
# resposta sair -- o navegador segue o redirect ja vendo o dado gravado.
DbDep = Depends(get_db, scope="function")


def csrf_da_sessao(request: Request) -> str:
    token = request.session.get("csrf")
    if not token:
        token = novo_token()
        request.session["csrf"] = token
    return token


async def verifica_csrf(request: Request) -> None:
    """Exige o token da sessao em toda requisicao que altera estado.

    HTMX manda em `X-CSRF-Token` (hx-headers no <body>); formularios comuns
    mandam o campo oculto `csrf_token`.
    """
    if request.method in _METODOS_SEGUROS:
        return
    recebido = request.headers.get("x-csrf-token")
    if not recebido:
        form = await request.form()
        recebido = form.get("csrf_token")  # type: ignore[assignment]
    if not tokens_iguais(request.session.get("csrf"), recebido if isinstance(recebido, str) else None):
        raise HTTPException(status_code=403, detail="Token CSRF invalido ou ausente.")


def usuario_atual(request: Request, db: Session = DbDep) -> Usuario:
    uid = request.session.get("uid")
    usuario = db.get(Usuario, uid) if uid else None
    if usuario is None or not usuario.ativo:
        request.session.clear()
        raise NaoAutenticado()
    return usuario


def exige_admin(usuario: Usuario = Depends(usuario_atual)) -> Usuario:
    if not usuario.eh_admin:
        raise SemPermissao()
    return usuario


def registrar_auditoria(db: Session, usuario_login: str | None, acao: str, alvo: str | None = None) -> None:
    db.add(Auditoria(usuario_login=usuario_login, acao=acao, alvo=alvo))


def render(request: Request, nome: str, usuario: Usuario | None = None, status_code: int = 200, headers: dict[str, str] | None = None, **contexto: Any):
    contexto.setdefault("usuario_logado", usuario)
    contexto.setdefault("eh_admin_logado", bool(usuario and usuario.eh_admin))
    contexto.setdefault("csrf_token", csrf_da_sessao(request))
    contexto.setdefault("popups_pendentes", [])
    return templates.TemplateResponse(request, nome, contexto, status_code=status_code, headers=headers)


def erro_no_form(mensagem: str) -> HTMLResponse:
    """Mostra o erro no #form-area (alvo dos formularios), mesmo quando o alvo original era outro."""
    html = f'<div class="badge offline" style="display:block; margin-bottom:.6rem">{html_escape(mensagem)}</div>'
    return HTMLResponse(html, headers={"HX-Retarget": "#form-area", "HX-Reswap": "innerHTML"})
