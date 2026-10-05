"""Configuracao de analise por camera (05f): pagina e salvamento atomico, e diagnostico do mestre (pagina, resumo, JPEG).

Tudo e ADMIN no servidor; mutacoes com CSRF e auditoria. Salvar NAO reinicia a analise operacional: so troca ajustes e
(se for o caso) a montagem da sombra, no laco proprio. O diagnostico vive so em memoria (nunca em disco).
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse, Response
from sqlalchemy.orm import Session

from src.jutta_ped.motor.diagnostico import DiagnosticoMestre
from src.jutta_ped.motor.ajustes import efetivos
from src.jutta_ped.painel import camera_config_service as ccs
from src.jutta_ped.painel.deps import DbDep, exige_admin, registrar_auditoria, render, verifica_csrf
from src.jutta_ped.painel.models import Camera, Usuario

router = APIRouter(prefix="/painel/cameras", dependencies=[Depends(verifica_csrf)])

DESTINOS_DIAGNOSTICO = {"analise", "diagnostico"}


def _servicos(request: Request):
    return request.app.state.painel


def _sombra(request: Request):
    return _servicos(request).sombra


def _voltar(request: Request, tipo: str, texto: str, destino: str):
    request.session["flash"] = {"tipo": tipo, "texto": texto}
    if request.headers.get("hx-request"):
        return Response(status_code=200, headers={"HX-Redirect": destino})
    return RedirectResponse(destino, status_code=303)


def _auditar(db: Session, admin: Usuario, acao: str, alvo: str) -> None:
    registrar_auditoria(db, admin.login, acao, alvo[:128])
    db.commit()


def _nao_encontrada(request: Request, admin: Usuario):
    return render(request, "erro.html", admin, status_code=404, titulo="Câmera não encontrada", mensagem="Essa câmera não existe (ou foi removida).")


def _acordar(request: Request, camera_id: int, *, desligar_sombra: bool = False) -> None:
    """Depois do COMMIT: invalida snapshots antigos, aplica ajustes/diagnostico na hora e acorda os lacos (a IA nao e reiniciada)."""
    servicos = _servicos(request)
    if desligar_sombra and servicos.sombra is not None:
        servicos.sombra.desligar_agora(camera_id)
    servicos.acordar()
    if servicos.sombra is not None:
        servicos.sombra.aplicar_rapido(camera_id)


# ---------------------------------------------------------------------------------------------- configuracao

def _pagina(request: Request, admin: Usuario, db: Session, camera_id: int, *, flash: dict | None = None, erro: str | None = None,
            problemas: list[str] | None = None, cfg_digitado: dict[str, str] | None = None, status_code: int = 200):
    ctx = ccs.contexto_pagina(db, request.app.state.especialistas, _sombra(request), camera_id, cfg_digitado)
    if ctx is None:
        return _nao_encontrada(request, admin)
    ctx.update(flash=flash, erro=erro, problemas=problemas or [])
    return render(request, "cameras/analise.html", admin, status_code=status_code, ativo="cameras", **ctx)


@router.get("/{camera_id}/analise")
def pagina_analise(camera_id: int, request: Request, admin: Usuario = Depends(exige_admin), db: Session = DbDep):
    return _pagina(request, admin, db, camera_id, flash=request.session.pop("flash", None))


@router.post("/{camera_id}/analise")
async def salvar(camera_id: int, request: Request, admin: Usuario = Depends(exige_admin), db: Session = DbDep):
    campos = await request.form()
    cfg = ccs.ler_form(campos)
    destino = f"/painel/cameras/{camera_id}/analise"
    try:
        resultado = ccs.salvar_config(db, request.app.state.especialistas, camera_id, cfg, admin.login)
    except ccs.ErroConfig as exc:
        db.rollback()
        _auditar(db, admin, "camera_config_recusada", f"camera {camera_id}")
        if exc.problemas == ["Câmera não encontrada."]:
            return _nao_encontrada(request, admin)
        return _pagina(request, admin, db, camera_id, erro="A configuração não foi gravada.", problemas=exc.problemas, cfg_digitado=cfg, status_code=422)
    _auditar(db, admin, "camera_config_salva", f"camera {camera_id} v{resultado.versao}" if resultado.versao else f"camera {camera_id}")
    if resultado.mudou or resultado.sombra_desligada:
        _acordar(request, camera_id, desligar_sombra=resultado.sombra_desligada)
    return _voltar(request, "ok", resultado.texto, destino)


# ---------------------------------------------------------------------------------------------- diagnostico do mestre

def _diag(request: Request, db: Session, camera: Camera) -> dict[str, Any]:
    """`diag` do contrato: `resumo()` + `estado` (desligado | aguardando | ativo). Sem objeto ainda = dict valido com contadores zero."""
    gerente = _sombra(request)
    ligado = bool(camera.diagnostico_ativo)
    obj = gerente.diagnostico(camera.id) if (ligado and gerente is not None) else None
    if obj is None:
        obj = DiagnosticoMestre()                       # vazio, so para ter o formato; nunca e guardado nem recebe cenas
        _cfg, _esp, _rvid, ajustes = ccs._config_atual_efetiva(db, camera)  # noqa: SLF001
        obj.definir_ajustes(efetivos(ajustes, ccs.vinculo_de(ccs.definicao_da_camera(db, camera), _esp)) if _cfg is not None else None)
    resumo = obj.resumo()
    if not ligado:
        estado = "desligado"
    else:
        ia_ok = gerente.ia_rodando(camera.id) if gerente is not None else False
        estado = "ativo" if (resumo["ativo"] and ia_ok) else "aguardando"
    return {**resumo, "estado": estado}


@router.post("/{camera_id}/diagnostico")
async def alternar_diagnostico(camera_id: int, request: Request, admin: Usuario = Depends(exige_admin), db: Session = DbDep):
    campos = await request.form()
    acao = str(campos.get("acao") or "").strip().lower()
    voltar = str(campos.get("destino") or "").strip().lower()
    destino = f"/painel/cameras/{camera_id}/" + (voltar if voltar in DESTINOS_DIAGNOSTICO else "diagnostico")
    camera = db.get(Camera, camera_id)
    if camera is None:
        return _voltar(request, "erro", "Câmera não encontrada.", "/painel/cameras")
    if acao not in {"ligar", "desligar"}:
        return _voltar(request, "erro", "Ação inválida: use ligar ou desligar.", destino)
    camera.diagnostico_ativo = acao == "ligar"
    nome = camera.nome
    _auditar(db, admin, "camera_diagnostico_ligado" if acao == "ligar" else "camera_diagnostico_desligado", f"camera {camera_id}")
    _acordar(request, camera_id)
    texto = (f"Diagnóstico do mestre ligado na câmera '{nome}': só memória, sem alertas e sem gravar nada." if acao == "ligar"
             else f"Diagnóstico do mestre desligado na câmera '{nome}' (memória limpa).")
    return _voltar(request, "ok", texto, destino)


@router.get("/{camera_id}/diagnostico")
def pagina_diagnostico(camera_id: int, request: Request, admin: Usuario = Depends(exige_admin), db: Session = DbDep):
    camera = db.get(Camera, camera_id)
    if camera is None:
        return _nao_encontrada(request, admin)
    ctx = ccs.contexto_pagina(db, request.app.state.especialistas, _sombra(request), camera_id)
    return render(request, "cameras/diagnostico.html", admin, ativo="cameras", cam=ctx["cam"] if ctx else {"id": camera.id, "nome": camera.nome},
                  diag=_diag(request, db, camera), efetivo=ctx["efetivo"] if ctx else None, flash=request.session.pop("flash", None))


@router.get("/{camera_id}/diagnostico/resumo")
def resumo_diagnostico(camera_id: int, request: Request, admin: Usuario = Depends(exige_admin), db: Session = DbDep):
    camera = db.get(Camera, camera_id)
    if camera is None:
        return Response(status_code=404)
    return render(request, "partials/diagnostico_mestre.html", admin, diag=_diag(request, db, camera))


def _jpeg(request: Request, db: Session, camera_id: int, gerar) -> Response:
    camera = db.get(Camera, camera_id)
    gerente = _sombra(request)
    obj = gerente.diagnostico(camera_id) if (camera is not None and camera.diagnostico_ativo and gerente is not None) else None
    if obj is None:
        return Response(status_code=404)
    try:
        dados = gerar(obj)
    except Exception:  # noqa: BLE001 -- imagem e conveniencia: nunca 500
        dados = None
    if not dados:
        return Response(status_code=404)
    return Response(content=dados, media_type="image/jpeg", headers={"Cache-Control": "no-store"})


@router.get("/{camera_id}/diagnostico/quadro.jpg")
def quadro_jpg(camera_id: int, request: Request, q: int | None = None, admin: Usuario = Depends(exige_admin), db: Session = DbDep):
    return _jpeg(request, db, camera_id, lambda d: d.jpeg_quadro(q))


@router.get("/{camera_id}/diagnostico/recorte/{trilha}.jpg")
def recorte_jpg(camera_id: int, trilha: str, request: Request, q: int | None = None, admin: Usuario = Depends(exige_admin), db: Session = DbDep):
    """`?q=<quadro_id>` pede o recorte do MESMO quadro que o resumo mostrou (as ultimas cenas ficam retidas em memoria)."""
    if not trilha.isdigit() or len(trilha) > 12:
        return Response(status_code=404)
    return _jpeg(request, db, camera_id, lambda d: d.jpeg_recorte(int(trilha), q))
