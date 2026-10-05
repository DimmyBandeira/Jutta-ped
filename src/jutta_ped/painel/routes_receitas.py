"""Receitas (CRUD versionado), associacao receita -> camera e sombra por camera, mais os fragmentos de resumo e feed.

Mutacoes: somente administrador, CSRF e auditoria. Configuracao atomica: erro de validacao nao grava nada e a associacao
anterior da camera e preservada; o erro aparece ao usuario (formulario 422 ou flash). Nada aqui executa especialistas:
a validacao usa o contrato leve (manifesto) e a execucao e do motor.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse, Response
from sqlalchemy.orm import Session

from src.jutta_ped.painel import receitas_service as rs
from src.jutta_ped.painel.deps import DbDep, exige_admin, registrar_auditoria, render, usuario_atual, verifica_csrf
from src.jutta_ped.painel.models import Usuario
from src.jutta_ped.painel.sombra_service import estado_para_camera, visao_feed

router = APIRouter(prefix="/painel", dependencies=[Depends(verifica_csrf)])

DESTINO = "/painel/receitas"


def _servicos(request: Request):
    return request.app.state.painel


def _inst(request: Request):
    return request.app.state.especialistas


def _voltar(request: Request, tipo: str, texto: str, destino: str = DESTINO):
    request.session["flash"] = {"tipo": tipo, "texto": texto}
    if request.headers.get("hx-request"):
        return Response(status_code=200, headers={"HX-Redirect": destino})
    return RedirectResponse(destino, status_code=303)


def _auditar(db: Session, admin: Usuario, acao: str, alvo: str) -> None:
    registrar_auditoria(db, admin.login, acao, alvo[:128])
    db.commit()


def _acordar(request: Request, camera_id: int | None = None, *, desligar: bool = False) -> None:
    servicos = _servicos(request)
    if desligar and camera_id is not None and servicos.sombra is not None:
        servicos.sombra.desligar_agora(camera_id)
    servicos.acordar()
    if camera_id is not None and servicos.sombra is not None:
        servicos.sombra.aplicar_rapido(camera_id)         # ajustes/diagnostico da camera: barato e imediato (sem montar nada)


# ---------------------------------------------------------------------------------------------- paginas

def _pagina(request: Request, admin: Usuario, db: Session, *, flash: dict | None = None, erro: str | None = None, status_code: int = 200):
    inst = _inst(request)
    contratos = rs.contratos_ativos(inst)
    sombra = _servicos(request).sombra
    return render(
        request, "receitas.html", admin, status_code=status_code, ativo="receitas",
        receitas=rs.visao_receitas(db, inst, contratos),
        especialistas_ativos=rs.especialistas_ativos(inst, contratos),
        cameras_receita=rs.visao_cameras_receita(db, inst, lambda c: estado_para_camera(sombra, c), contratos),
        opcoes_versao=rs.opcoes_versao(db), flash=flash, erro=erro,
    )


def _pagina_form(request: Request, admin: Usuario, form: dict[str, Any], *, editando: bool, erro: str | None = None,
                 problemas: list[str] | None = None, status_code: int = 200):
    return render(request, "receitas_form.html", admin, status_code=status_code, ativo="receitas", form=form,
                  especialistas_ativos=rs.especialistas_ativos(_inst(request)), erro=erro, problemas=problemas or [], editando=editando)


@router.get("/receitas")
def listar(request: Request, admin: Usuario = Depends(exige_admin), db: Session = DbDep):
    return _pagina(request, admin, db, flash=request.session.pop("flash", None))


@router.get("/receitas/nova")
def nova(request: Request, admin: Usuario = Depends(exige_admin)):
    return _pagina_form(request, admin, rs.form_vazio(sorted(rs.contratos_ativos(_inst(request)))), editando=False)


@router.get("/receitas/{rid}/editar")
def editar(rid: int, request: Request, admin: Usuario = Depends(exige_admin), db: Session = DbDep):
    try:
        form = rs.form_da_ultima_versao(db, _inst(request), rid)
    except rs.ErroReceita as exc:
        return _voltar(request, "erro", exc.mensagem)
    if form is None:
        return _voltar(request, "erro", "Receita não encontrada.")
    return _pagina_form(request, admin, form, editando=True)


# ---------------------------------------------------------------------------------------------- mutacoes

@router.post("/receitas")
async def criar(request: Request, admin: Usuario = Depends(exige_admin), db: Session = DbDep):
    campos = await request.form()
    inst = _inst(request)
    form = rs.ler_form(campos, sorted(rs.contratos_ativos(inst)))
    try:
        versao = rs.criar_receita(db, inst, form, admin.login)
    except rs.ErroReceita as exc:
        db.rollback()
        _auditar(db, admin, "receita_recusada", form["nome"] or "-")
        return _pagina_form(request, admin, form, editando=False, erro="A receita não foi gravada.", problemas=exc.problemas, status_code=422)
    _auditar(db, admin, "receita_criada", f"{form['nome']} v{versao.versao}")
    return _voltar(request, "ok", f"Receita '{form['nome']}' criada (v1).")


@router.post("/receitas/importar")
async def importar(request: Request, admin: Usuario = Depends(exige_admin), db: Session = DbDep):
    """Cria uma receita (ou nova versao imutavel) a partir de JSON. E o caminho para o contrato 2 (relacoes e persistencia), que o
    formulario simples nao edita. Erro nao grava nada e volta com a mensagem."""
    campos = await request.form()
    nome = str(campos.get("nome") or "").strip()
    try:
        versao = rs.importar_definicao(db, _inst(request), nome, str(campos.get("descricao") or "").strip(), str(campos.get("definicao_json") or ""), admin.login)
    except rs.ErroReceita as exc:
        db.rollback()
        _auditar(db, admin, "receita_importacao_recusada", nome or "-")
        return _pagina(request, admin, db, erro="Importação recusada: " + exc.mensagem, status_code=422)
    rotulo = rs.rotulo_versao(db, versao.id)
    _auditar(db, admin, "receita_importada", rotulo or nome)
    _acordar(request)
    return _voltar(request, "ok", f"Definição importada como {rotulo}. As câmeras continuam na versão associada até você trocá-la.")


@router.post("/receitas/{rid}/versoes")
async def criar_versao(rid: int, request: Request, admin: Usuario = Depends(exige_admin), db: Session = DbDep):
    campos = await request.form()
    inst = _inst(request)
    form = rs.ler_form(campos, sorted(rs.contratos_ativos(inst)), rid)
    try:
        versao = rs.nova_versao(db, inst, rid, form, admin.login)
    except rs.ErroReceita as exc:
        db.rollback()
        _auditar(db, admin, "receita_versao_recusada", str(rid))
        if exc.problemas == ["Receita não encontrada."]:
            return _voltar(request, "erro", exc.mensagem)
        return _pagina_form(request, admin, form, editando=True, erro="A nova versão não foi gravada.", problemas=exc.problemas, status_code=422)
    rotulo = rs.rotulo_versao(db, versao.id)
    _auditar(db, admin, "receita_versao_criada", rotulo or str(rid))
    _acordar(request)           # cameras que usam versoes antigas continuam nelas; nada e trocado sem acao explicita
    return _voltar(request, "ok", f"Nova versão criada: {rotulo}. As câmeras continuam na versão associada até você trocá-la.")


@router.post("/cameras/{camera_id}/receita")
async def associar(camera_id: int, request: Request, admin: Usuario = Depends(exige_admin), db: Session = DbDep):
    campos = await request.form()
    bruto = str(campos.get("receita_versao_id") or "").strip()
    if bruto and not bruto.isdigit():
        return _voltar(request, "erro", "Versão de receita inválida.")
    versao_id = int(bruto) if bruto else None
    try:
        texto = rs.associar_camera(db, _inst(request), camera_id, versao_id, admin.login)
    except rs.ErroReceita as exc:
        db.rollback()
        _auditar(db, admin, "camera_receita_recusada", f"camera {camera_id}")
        return _voltar(request, "erro", f"Associação não alterada: {exc.mensagem}")
    _auditar(db, admin, "camera_receita_removida" if versao_id is None else "camera_receita_associada", f"camera {camera_id} -> {versao_id}")
    _acordar(request, camera_id, desligar=versao_id is None)
    return _voltar(request, "ok", texto)


@router.post("/cameras/{camera_id}/sombra")
async def alternar_sombra(camera_id: int, request: Request, admin: Usuario = Depends(exige_admin), db: Session = DbDep):
    campos = await request.form()
    acao = str(campos.get("acao") or "").strip().lower()
    if acao not in {"ligar", "desligar"}:
        return _voltar(request, "erro", "Ação inválida: use ligar ou desligar.")
    try:
        texto = rs.definir_sombra(db, _inst(request), camera_id, acao == "ligar")
    except rs.ErroReceita as exc:
        db.rollback()
        _auditar(db, admin, "sombra_recusada", f"camera {camera_id}")
        return _voltar(request, "erro", f"Sombra não alterada: {exc.mensagem}")
    _auditar(db, admin, "sombra_ligada" if acao == "ligar" else "sombra_desligada", f"camera {camera_id}")
    _acordar(request, camera_id, desligar=acao == "desligar")
    return _voltar(request, "ok", texto)


# ---------------------------------------------------------------------------------------------- fragmentos (logado)

@router.get("/receitas-resumo")
def resumo(request: Request, usuario: Usuario = Depends(usuario_atual), db: Session = DbDep):
    return render(request, "partials/receitas_resumo.html", usuario, receitas=rs.visao_receitas(db, _inst(request)))


@router.get("/sombra/feed")
def feed(request: Request, camera: str | None = None, usuario: Usuario = Depends(usuario_atual), db: Session = DbDep):
    camera_id = int(camera) if camera is not None and camera.strip().isdigit() else None
    return render(request, "partials/sombra_feed.html", usuario, sombra=visao_feed(db, _servicos(request).sombra, camera_id))
