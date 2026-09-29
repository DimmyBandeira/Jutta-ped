from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from src.jutta_ped.painel.config_service import CAMPOS, ler_config, salvar_config, validar_config
from src.jutta_ped.painel.deps import DbDep, exige_admin, registrar_auditoria, render, verifica_csrf
from src.jutta_ped.painel.models import Usuario

router = APIRouter(prefix="/painel/configuracoes", dependencies=[Depends(verifica_csrf)])


def _sync_status(request: Request) -> str:
    servicos = request.app.state.painel
    resumo = servicos.sync.ultimo_resumo.texto()
    return f"{resumo} · processo: {servicos.go2rtc.estado}" + (f" ({servicos.go2rtc.detalhe})" if servicos.go2rtc.detalhe else "")


@router.get("")
def formulario(request: Request, salvo: int = 0, admin: Usuario = Depends(exige_admin), db: Session = DbDep):
    return render(request, "configuracoes.html", admin, config=ler_config(db), salvo=bool(salvo), sync_status=_sync_status(request), ativo="configuracoes")


@router.post("")
async def salvar(request: Request, admin: Usuario = Depends(exige_admin), db: Session = DbDep):
    form = await request.form()
    dados = {chave: str(form.get(chave) or "") for chave in CAMPOS}
    valores, erro = validar_config(dados)
    if erro:
        return render(request, "configuracoes.html", admin, status_code=422, config=dados, erro=erro, sync_status=_sync_status(request), ativo="configuracoes")
    salvar_config(db, valores)
    registrar_auditoria(db, admin.login, "configuracoes_salvas")
    db.commit()
    servicos = request.app.state.painel
    servicos.sync.reconcile()
    servicos.acordar()
    return RedirectResponse("/painel/configuracoes?salvo=1", status_code=303)
