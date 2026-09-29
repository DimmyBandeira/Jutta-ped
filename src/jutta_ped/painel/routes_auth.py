from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from src.jutta_ped.painel.deps import DbDep, registrar_auditoria, render, verifica_csrf
from src.jutta_ped.painel.models import Usuario
from src.jutta_ped.painel.security import novo_token, verificar_senha

router = APIRouter(dependencies=[Depends(verifica_csrf)])

_HASH_FALSO = "scrypt$16384$8$1$" + "00" * 16 + "$" + "00" * 32  # iguala o tempo de resposta p/ login inexistente


@router.get("/login")
def login_form(request: Request):
    if request.session.get("uid"):
        return RedirectResponse("/painel/monitoramento", status_code=303)
    return render(request, "login.html")


@router.post("/login")
def login(request: Request, login: str = Form(""), senha: str = Form(""), db: Session = DbDep):
    login = login.strip().lower()
    ip = request.client.host if request.client else "?"
    limitador = request.app.state.painel_limitador
    chave = (login, ip)
    if limitador.bloqueado(chave):
        return render(request, "login.html", status_code=429, erro="Muitas tentativas. Aguarde um minuto e tente de novo.")
    usuario = db.scalar(select(Usuario).where(Usuario.login == login))
    senha_ok = verificar_senha(senha, usuario.senha_hash if usuario else _HASH_FALSO)
    if usuario is None or not usuario.ativo or not senha_ok:
        limitador.falhou(chave)
        registrar_auditoria(db, login or None, "login_falhou", ip)
        return render(request, "login.html", status_code=401, erro="Usuário ou senha inválidos.")
    limitador.sucesso(chave)
    request.session.clear()  # nova sessao: evita fixacao
    request.session["uid"] = usuario.id
    request.session["csrf"] = novo_token()
    registrar_auditoria(db, usuario.login, "login", ip)
    return RedirectResponse("/painel/monitoramento", status_code=303)


@router.post("/logout")
def logout(request: Request, db: Session = DbDep):
    uid = request.session.get("uid")
    usuario = db.get(Usuario, uid) if uid else None
    if usuario:
        registrar_auditoria(db, usuario.login, "logout")
    request.session.clear()
    return RedirectResponse("/login", status_code=303)
