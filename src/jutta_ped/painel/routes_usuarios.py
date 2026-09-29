from __future__ import annotations

import re

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from src.jutta_ped.painel.deps import DbDep, exige_admin, registrar_auditoria, render, verifica_csrf
from src.jutta_ped.painel.models import Papel, Usuario
from src.jutta_ped.painel.security import hash_senha

router = APIRouter(prefix="/painel/usuarios", dependencies=[Depends(verifica_csrf)])

_LOGIN_RE = re.compile(r"^[a-z0-9._-]{3,32}$")
_SENHA_MIN = 8


def _lista(request: Request, db: Session, admin: Usuario, **extra):
    usuarios = db.scalars(select(Usuario).order_by(Usuario.login)).all()
    return render(request, "usuarios/_lista.html", admin, usuarios=usuarios, oob=True, **extra)


def _erro_no_form(mensagem: str) -> HTMLResponse:
    """Mostra o erro no #form-area (alvo dos formularios), mesmo quando o alvo original era outro."""
    html = f'<div class="badge offline" style="display:block; margin-bottom:.6rem">{mensagem}</div>'
    return HTMLResponse(html, headers={"HX-Retarget": "#form-area", "HX-Reswap": "innerHTML"})


def _admins_ativos(db: Session) -> int:
    return db.scalar(select(func.count()).select_from(Usuario).where(Usuario.papel == Papel.admin, Usuario.ativo.is_(True))) or 0


@router.get("")
def listar(request: Request, admin: Usuario = Depends(exige_admin), db: Session = DbDep):
    usuarios = db.scalars(select(Usuario).order_by(Usuario.login)).all()
    return render(request, "usuarios/list.html", admin, usuarios=usuarios, ativo="usuarios")


@router.get("/novo")
def novo(request: Request, admin: Usuario = Depends(exige_admin)):
    return render(request, "usuarios/_form.html", admin, usuario=None)


@router.get("/cancelar")
def cancelar(admin: Usuario = Depends(exige_admin)):
    return HTMLResponse("")


@router.get("/{usuario_id}/editar")
def editar_form(usuario_id: int, request: Request, admin: Usuario = Depends(exige_admin), db: Session = DbDep):
    usuario = db.get(Usuario, usuario_id)
    if usuario is None:
        return _erro_no_form("Usuário não encontrado.")
    return render(request, "usuarios/_form.html", admin, usuario=usuario)


@router.post("")
def criar(
    request: Request,
    login: str = Form(""),
    papel: str = Form("comum"),
    senha: str = Form(""),
    admin: Usuario = Depends(exige_admin),
    db: Session = DbDep,
):
    login = login.strip().lower()
    if not _LOGIN_RE.match(login):
        return _erro_no_form("Login deve ter 3–32 caracteres: letras minúsculas, números, ponto, hífen ou sublinhado.")
    if len(senha) < _SENHA_MIN:
        return _erro_no_form(f"A senha precisa ter pelo menos {_SENHA_MIN} caracteres.")
    if papel not in {p.value for p in Papel}:
        return _erro_no_form("Papel inválido.")
    if db.scalar(select(Usuario.id).where(Usuario.login == login)) is not None:
        return _erro_no_form("Já existe um usuário com esse login.")
    db.add(Usuario(login=login, senha_hash=hash_senha(senha), papel=Papel(papel), ativo=True))
    registrar_auditoria(db, admin.login, "usuario_criado", login)
    db.flush()
    return _lista(request, db, admin)


@router.put("/{usuario_id}")
def atualizar(
    usuario_id: int,
    request: Request,
    papel: str = Form("comum"),
    senha: str = Form(""),
    ativo: str | None = Form(None),
    admin: Usuario = Depends(exige_admin),
    db: Session = DbDep,
):
    usuario = db.get(Usuario, usuario_id)
    if usuario is None:
        return _erro_no_form("Usuário não encontrado.")
    if papel not in {p.value for p in Papel}:
        return _erro_no_form("Papel inválido.")
    novo_papel, novo_ativo = Papel(papel), ativo is not None
    if senha and len(senha) < _SENHA_MIN:
        return _erro_no_form(f"A senha precisa ter pelo menos {_SENHA_MIN} caracteres.")
    era_admin_ativo = usuario.eh_admin and usuario.ativo
    if era_admin_ativo and not (novo_papel == Papel.admin and novo_ativo) and _admins_ativos(db) <= 1:
        return _erro_no_form("Não é possível rebaixar ou desativar o último administrador ativo.")
    usuario.papel, usuario.ativo = novo_papel, novo_ativo
    if senha:
        usuario.senha_hash = hash_senha(senha)
    registrar_auditoria(db, admin.login, "usuario_editado", usuario.login)
    db.flush()
    return _lista(request, db, admin)


@router.delete("/{usuario_id}")
def remover(usuario_id: int, admin: Usuario = Depends(exige_admin), db: Session = DbDep):
    usuario = db.get(Usuario, usuario_id)
    if usuario is None:
        return HTMLResponse("")
    if usuario.id == admin.id:
        return _erro_no_form("Você não pode remover o próprio usuário.")
    if usuario.eh_admin and usuario.ativo and _admins_ativos(db) <= 1:
        return _erro_no_form("Não é possível remover o último administrador ativo.")
    registrar_auditoria(db, admin.login, "usuario_removido", usuario.login)
    db.delete(usuario)
    return HTMLResponse("")
