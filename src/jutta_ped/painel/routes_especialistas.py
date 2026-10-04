"""Administracao de especialistas (pacotes declarativos): instalar, ativar, reverter, remover, recuperar.

Somente administrador. Toda operacao passa pelo servico `Instalador` (validacao, extracao isolada, bloqueio,
registro atomico): este modulo NUNCA le nem escreve o registro de instalacao diretamente.

Ativar uma versao aqui apenas a torna a versao ativa do especialista no registro; o motor de analise ainda nao a
consome (etapa futura). A tela diz isso ao operador.
"""
from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, File, Request, UploadFile
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from src.jutta_ped.especialistas.instalador import (
    COD_BLOQUEIO,
    COD_CONFLITO,
    COD_EM_USO,
    COD_REPROVADO,
    ErroInstalacao,
    Instalador,
)
from src.jutta_ped.especialistas.validador import NOMES_CATEGORIA
from src.jutta_ped.painel.deps import DbDep, exige_admin, registrar_auditoria, render, verifica_csrf
from src.jutta_ped.painel.models import Usuario

MIB = 1024 * 1024
UPLOAD_MAX_PADRAO = 600 * MIB
CAMINHO_UPLOAD = "/painel/especialistas/instalar"   # limitado por painel.limite_corpo (antes do parser multipart)


router = APIRouter(prefix="/painel/especialistas", dependencies=[Depends(verifica_csrf)])

_STATUS = {COD_REPROVADO: 422, COD_CONFLITO: 409, COD_BLOQUEIO: 409, COD_EM_USO: 409}


def _inst(request: Request) -> Instalador:
    return request.app.state.especialistas


def _chave_versao(v: str) -> tuple:
    base = v.split("-", 1)[0]
    try:
        return tuple(int(p) for p in base.split(".")), v
    except ValueError:
        return (0,), v


def _visao(inst: Instalador) -> list[dict[str, Any]]:
    itens = []
    for pid, esp in sorted(inst.listar().items()):
        versoes = [{"versao": ver, **dados} for ver, dados in sorted(esp["versoes"].items(), key=lambda kv: _chave_versao(kv[0]), reverse=True)]
        nome = next((v.get("nome") for v in versoes if v.get("nome")), pid)
        itens.append({"id": pid, "nome": nome, "ativa": esp.get("ativa"), "tem_anterior": bool(esp.get("historico")), "versoes": versoes})
    return itens


def _pagina(request: Request, admin: Usuario, *, status_code: int = 200, erro: str | None = None, relatorio: Any = None, flash: dict | None = None):
    inst = _inst(request)
    registro_erro = None
    itens: list[dict[str, Any]] = []
    try:
        itens = _visao(inst)
    except ErroInstalacao as exc:
        registro_erro = exc.mensagem
    problemas = []
    if relatorio is not None:
        problemas = [{"categoria": NOMES_CATEGORIA[p.categoria], "codigo": p.codigo, "caminho": p.caminho, "mensagem": p.mensagem} for p in relatorio.ordenado()]
    return render(
        request, "especialistas.html", admin, status_code=status_code, ativo="especialistas",
        itens=itens, erro=erro, problemas=problemas, flash=flash, registro_erro=registro_erro,
        operacoes=inst.operacoes_recentes(15), upload_max_mib=getattr(request.app.state, "especialistas_upload_max", UPLOAD_MAX_PADRAO) // MIB,
    )


def _flash(request: Request, tipo: str, texto: str) -> RedirectResponse:
    request.session["flash"] = {"tipo": tipo, "texto": texto}
    return RedirectResponse("/painel/especialistas", status_code=303)


def _auditar(db: Session, admin: Usuario, acao: str, alvo: str) -> None:
    registrar_auditoria(db, admin.login, acao, alvo[:128])
    db.commit()


@router.get("")
def listar(request: Request, admin: Usuario = Depends(exige_admin)):
    return _pagina(request, admin, flash=request.session.pop("flash", None))


@router.post("/instalar")
def instalar(request: Request, pacote: UploadFile = File(...), admin: Usuario = Depends(exige_admin), db: Session = DbDep):
    inst = _inst(request)
    nome = (pacote.filename or "").strip()
    if not nome.lower().endswith(".zip"):
        return _pagina(request, admin, status_code=422, erro="Envie um arquivo .zip com o pacote do especialista.")
    teto = getattr(request.app.state, "especialistas_upload_max", UPLOAD_MAX_PADRAO)
    uploads = inst.raiz / "uploads"
    uploads.mkdir(parents=True, exist_ok=True)
    tmp = uploads / f"{uuid.uuid4().hex}.zip"      # nome do cliente NUNCA vira caminho no disco
    try:
        copiado = 0
        with open(tmp, "wb") as saida:
            while True:
                bloco = pacote.file.read(1 * MIB)
                if not bloco:
                    break
                copiado += len(bloco)
                if copiado > teto:
                    return _pagina(request, admin, status_code=413, erro=f"Arquivo maior que o limite de {teto // MIB} MiB.")
                saida.write(bloco)
        try:
            r = inst.instalar(tmp, rotulo=Path(nome.replace(chr(92), '/')).name)
        except ErroInstalacao as exc:
            _auditar(db, admin, "especialista_instalacao_recusada", f"{nome}: {exc.slug}")
            return _pagina(request, admin, status_code=_STATUS.get(exc.codigo, 500), erro=exc.mensagem, relatorio=exc.relatorio)
    finally:
        tmp.unlink(missing_ok=True)
        pacote.file.close()
    _auditar(db, admin, "especialista_instalado", f"{r.id} {r.versao}")
    texto = (f"{r.id} {r.versao} já estava instalado (nada mudou)." if r.ja_instalado
             else f"{r.id} {r.versao} instalado. Ele ainda está inativo: ative a versão para torná-la a vigente.")
    return _flash(request, "ok", texto)


def _executar(request: Request, admin: Usuario, db: Session, acao: str, alvo: str, fn, ok: str):
    try:
        fn()
    except ErroInstalacao as exc:
        _auditar(db, admin, f"{acao}_falhou", f"{alvo}: {exc.slug}")
        return _flash(request, "erro", exc.mensagem)
    _auditar(db, admin, acao, alvo)
    return _flash(request, "ok", ok)


@router.post("/recuperar")
def recuperar(request: Request, admin: Usuario = Depends(exige_admin), db: Session = DbDep):
    inst = _inst(request)
    resultado: list[str] = []

    def _fn() -> None:
        resultado.extend(inst.recuperar())

    resp = _executar(request, admin, db, "especialistas_recuperados", "-", _fn, "")
    if request.session.get("flash", {}).get("tipo") == "ok":
        request.session["flash"] = {"tipo": "ok", "texto": ("Recuperação: " + "; ".join(resultado)) if resultado else "Nada a recuperar."}
    return resp


@router.post("/{pid}/reverter")
def reverter(pid: str, request: Request, admin: Usuario = Depends(exige_admin), db: Session = DbDep):
    destino: list[str] = []
    return _executar(request, admin, db, "especialista_revertido", pid, lambda: destino.append(_inst(request).reverter(pid)),
                     f"{pid} voltou para a versão anterior.")


@router.post("/{pid}/{versao}/ativar")
def ativar(pid: str, versao: str, request: Request, admin: Usuario = Depends(exige_admin), db: Session = DbDep):
    return _executar(request, admin, db, "especialista_ativado", f"{pid} {versao}", lambda: _inst(request).ativar(pid, versao),
                     f"{pid} {versao} é agora a versão ativa.")


@router.post("/{pid}/{versao}/remover")
def remover(pid: str, versao: str, request: Request, admin: Usuario = Depends(exige_admin), db: Session = DbDep):
    return _executar(request, admin, db, "especialista_removido", f"{pid} {versao}", lambda: _inst(request).remover(pid, versao),
                     f"{pid} {versao} removido.")
