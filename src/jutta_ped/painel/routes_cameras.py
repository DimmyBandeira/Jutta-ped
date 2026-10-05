from __future__ import annotations

import re
from html import escape
from typing import Any

import yaml
from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from src.jutta_ped.painel.config_service import ler_config
from src.jutta_ped.painel.deps import DbDep, erro_no_form, registrar_auditoria, render, usuario_atual, verifica_csrf, exige_admin
from src.jutta_ped.painel.simulater_dsa import ler_cenario
from src.jutta_ped.painel.go2rtc import mascarar_url
from src.jutta_ped.painel.models import Camera, CameraDestino, Destino, Fabricante, StreamTipo, Usuario

router = APIRouter(prefix="/painel/cameras", dependencies=[Depends(verifica_csrf)])

_HOST_RE = re.compile(r"^[A-Za-z0-9]([A-Za-z0-9.-]{0,251}[A-Za-z0-9])?$")
_NOME_STREAM_RE = re.compile(r"^[A-Za-z0-9_.-]{1,80}$")
_MAX_YAML_BYTES = 1_000_000


def _servicos(request: Request):
    return request.app.state.painel


def _cifra(request: Request):
    return _servicos(request).cifra


def _cameras(db: Session) -> list[Camera]:
    return list(db.scalars(select(Camera).order_by(Camera.nome)).unique().all())


def _lista_oob(request: Request, db: Session, usuario: Usuario):
    db.expire_all()
    return render(request, "cameras/_lista.html", usuario, cameras=_cameras(db), oob=True)


def _ctx_form(db: Session, camera: Camera | None, fabricante: str | None, streams_disponiveis: list[str]) -> dict[str, Any]:
    destinos = db.scalars(select(Destino).order_by(Destino.nome)).all()
    return {
        "camera": camera,
        "fabricante": fabricante or (camera.fabricante.value if camera else "intelbras"),
        "destinos": destinos,
        "destinos_vinculados_ids": [cd.destino_id for cd in camera.destinos] if camera else [],
        "streams_disponiveis": streams_disponiveis,
    }


def _mascarada(camera: Camera, cifra) -> str:
    if camera.fabricante != Fabricante.rtsp_generico or not camera.rtsp_url_enc:
        return ""
    try:
        return mascarar_url(cifra.decifrar(camera.rtsp_url_enc))
    except ValueError:
        return ""


@router.get("")
def listar(request: Request, usuario: Usuario = Depends(usuario_atual), db: Session = DbDep):
    return render(request, "cameras/list.html", usuario, cameras=_cameras(db), ativo="cameras")


@router.get("/novo")
def novo(request: Request, usuario: Usuario = Depends(usuario_atual), db: Session = DbDep):
    servicos = _servicos(request)
    return render(request, "cameras/_form.html", usuario, **_ctx_form(db, None, None, servicos.sync.nomes_vivos()))


@router.get("/cancelar")
def cancelar(usuario: Usuario = Depends(usuario_atual)):
    return HTMLResponse("")


@router.get("/campos-fonte")
def campos_fonte(request: Request, fabricante: str = "intelbras", camera_id: str = "", usuario: Usuario = Depends(usuario_atual), db: Session = DbDep):
    if fabricante not in {f.value for f in Fabricante}:
        fabricante = "intelbras"
    camera = db.get(Camera, int(camera_id)) if camera_id.isdigit() else None
    if camera is not None:
        camera.rtsp_url_mascarada = _mascarada(camera, _cifra(request))  # type: ignore[attr-defined]
    streams = _servicos(request).sync.nomes_vivos() if fabricante == "go2rtc_teste" else []
    return render(request, "cameras/_campos_fonte.html", usuario, camera=camera, fabricante=fabricante, streams_disponiveis=streams)


@router.post("/streams-do-yaml")
async def streams_do_yaml(arquivo_yaml: UploadFile = File(...), usuario: Usuario = Depends(usuario_atual)):
    conteudo = await arquivo_yaml.read(_MAX_YAML_BYTES + 1)
    opcoes = ['<option value="">-- selecione --</option>']
    try:
        if len(conteudo) > _MAX_YAML_BYTES:
            raise ValueError("arquivo grande demais")
        dados = yaml.safe_load(conteudo) or {}
        streams = dados.get("streams") if isinstance(dados, dict) else None
        nomes = sorted(str(n) for n in streams) if isinstance(streams, dict) else []
    except (yaml.YAMLError, ValueError):
        nomes = []
    if not nomes:
        return HTMLResponse('<option value="" disabled>(nenhum stream encontrado no arquivo)</option>')
    opcoes += [f'<option value="{escape(n)}">{escape(n)}</option>' for n in nomes]
    return HTMLResponse("".join(opcoes))


@router.get("/simulater-dsa")
def form_simulater(request: Request, usuario: Usuario = Depends(exige_admin)):
    return render(request, "cameras/_simulater_dsa.html", usuario)


@router.post("/simulater-dsa")
async def importar_simulater(request: Request, arquivo_yaml: UploadFile = File(...),
                            host: str = Form("127.0.0.1"),
                            usuario: Usuario = Depends(exige_admin), db: Session = DbDep):
    try:
        fontes = ler_cenario(await arquivo_yaml.read(_MAX_YAML_BYTES + 1), host)
        existentes = {c.nome.lower(): c for c in _cameras(db)}
        novas, repetidas = [], 0
        for nome, url in fontes:
            anterior = existentes.get(nome.lower())
            if anterior is not None:
                if anterior.fabricante != Fabricante.rtsp_generico or not anterior.rtsp_url_enc or _cifra(request).decifrar(anterior.rtsp_url_enc) != url:
                    raise ValueError(f"Já existe uma câmera diferente com o nome {nome}. Nenhuma foi importada.")
                repetidas += 1
                continue
            novas.append(Camera(nome=nome, fabricante=Fabricante.rtsp_generico,
                                rtsp_url_enc=_cifra(request).cifrar(url), ia_pediatria=False))
    except ValueError as exc:
        db.rollback()
        return erro_no_form(str(exc))
    db.add_all(novas)
    registrar_auditoria(db, usuario.login, "simulater_dsa_importado", f"novas={len(novas)}; existentes={repetidas}")
    db.commit()
    servicos = _servicos(request)
    servicos.sync.reconcile()
    servicos.acordar()
    lista = _lista_oob(request, db, usuario)
    mensagem = f"SimulaterDSA: {len(novas)} câmeras cadastradas; {repetidas} já existentes. IA desligada. Confira o estado de sincronização abaixo."
    return HTMLResponse(f'<p role="status">{mensagem}</p>' + lista.body.decode("utf-8"))


@router.get("/{camera_id}/editar")
def editar_form(camera_id: int, request: Request, usuario: Usuario = Depends(usuario_atual), db: Session = DbDep):
    camera = db.get(Camera, camera_id)
    if camera is None:
        return erro_no_form("Câmera não encontrada.")
    camera.rtsp_url_mascarada = _mascarada(camera, _cifra(request))  # type: ignore[attr-defined]
    streams = _servicos(request).sync.nomes_vivos() if camera.fabricante == Fabricante.go2rtc_teste else []
    return render(request, "cameras/_form.html", usuario, **_ctx_form(db, camera, None, streams))


def _validar_e_aplicar(
    request: Request,
    db: Session,
    camera: Camera,
    campos: dict[str, Any],
    destino_ids: list[int],
    criando: bool,
) -> str | None:
    """Valida o formulario e grava em `camera`. Devolve mensagem de erro ou None."""
    cifra = _cifra(request)
    nome = campos["nome"].strip()
    if not nome or len(nome) > 80:
        return "Informe o nome da câmera (até 80 caracteres)."
    duplicada = db.scalar(select(Camera.id).where(func.lower(Camera.nome) == nome.lower(), Camera.id != (camera.id or 0)))
    if duplicada is not None:
        return "Já existe uma câmera com esse nome."
    try:
        fabricante = Fabricante(campos["fabricante"])
    except ValueError:
        return "Fabricante inválido."

    camera.nome, camera.fabricante = nome, fabricante
    camera.identificador_externo = (campos["identificador_externo"] or "").strip()[:64] or None
    camera.ia_pediatria = campos["ia_pediatria"]

    if fabricante == Fabricante.go2rtc_teste:
        stream = (campos["go2rtc_stream_existente"] or "").strip()
        if not _NOME_STREAM_RE.match(stream):
            return "Selecione o stream do go2rtc a usar."
        camera.go2rtc_stream_name = stream
        camera.ip = camera.usuario = camera.senha_enc = camera.rtsp_url_enc = None
    elif fabricante == Fabricante.rtsp_generico:
        url = (campos["rtsp_url"] or "").strip()
        if url and "***" not in url:  # "***" = valor mascarado devolvido pelo formulario: manter o atual
            if not url.lower().startswith(("rtsp://", "rtsps://")) or len(url) > 1000:
                return "A URL deve começar com rtsp:// ou rtsps://."
            camera.rtsp_url_enc = cifra.cifrar(url)
        elif not camera.rtsp_url_enc:
            return "Informe a URL RTSP."
        camera.ip = camera.usuario = camera.senha_enc = camera.go2rtc_stream_name = None
    else:
        ip = (campos["ip"] or "").strip()
        usuario_cam = (campos["usuario"] or "").strip()
        if not _HOST_RE.match(ip):
            return "Informe um IP ou host válido."
        if not usuario_cam:
            return "Informe o usuário da câmera."
        for rotulo, valor, minimo, maximo in (("Porta", campos["porta"], 1, 65535), ("Canal", campos["canal"], 1, 64)):
            if not minimo <= valor <= maximo:
                return f"{rotulo} deve estar entre {minimo} e {maximo}."
        senha = campos["senha"]
        if senha:
            camera.senha_enc = cifra.cifrar(senha)
        elif not camera.senha_enc:
            return "Informe a senha da câmera."
        try:
            camera.stream_tipo = StreamTipo(campos["stream_tipo"])
        except ValueError:
            return "Tipo de stream inválido."
        camera.ip, camera.usuario, camera.porta, camera.canal = ip, usuario_cam, campos["porta"], campos["canal"]
        camera.rtsp_url_enc = camera.go2rtc_stream_name = None

    if camera.ia_pediatria:
        limite = ler_config(db)["max_cameras_ia"]
        outras = db.scalar(select(func.count()).select_from(Camera).where(Camera.ia_pediatria.is_(True), Camera.id != (camera.id or 0))) or 0
        if outras >= limite:
            return f"Limite de {limite} câmeras com IA atingido (ajustável em Configurações)."

    validos = set(db.scalars(select(Destino.id).where(Destino.id.in_(destino_ids))).all()) if destino_ids else set()
    camera.destinos = [CameraDestino(destino_id=d) for d in sorted(validos)]
    return None


def _campos_do_form(
    nome: str, fabricante: str, ip: str, porta: int, canal: int, stream_tipo: str, usuario: str, senha: str,
    rtsp_url: str, go2rtc_stream_existente: str, identificador_externo: str, ia_pediatria: str | None,
) -> dict[str, Any]:
    return {
        "nome": nome, "fabricante": fabricante, "ip": ip, "porta": porta, "canal": canal, "stream_tipo": stream_tipo,
        "usuario": usuario, "senha": senha, "rtsp_url": rtsp_url, "go2rtc_stream_existente": go2rtc_stream_existente,
        "identificador_externo": identificador_externo, "ia_pediatria": ia_pediatria is not None,
    }


def _depois_de_gravar(request: Request, db: Session, camera_id: int) -> None:
    """Commit + reconciliacao do go2rtc na hora; a IA e ajustada em segundo plano."""
    db.commit()
    servicos = _servicos(request)
    servicos.sync.reconcile()
    if servicos.ia is not None:
        servicos.ia.reiniciar(camera_id)
    servicos.acordar()          # depois do commit: invalida snapshots antigos da sombra mesmo sem IA


@router.post("")
def criar(
    request: Request,
    nome: str = Form(""), fabricante: str = Form("intelbras"), ip: str = Form(""), porta: int = Form(554), canal: int = Form(1),
    stream_tipo: str = Form("principal"), usuario: str = Form(""), senha: str = Form(""), rtsp_url: str = Form(""),
    go2rtc_stream_existente: str = Form(""), identificador_externo: str = Form(""), ia_pediatria: str | None = Form(None),
    destino_ids: list[int] = Form([]),
    operador: Usuario = Depends(usuario_atual), db: Session = DbDep,
):
    camera = Camera()
    campos = _campos_do_form(nome, fabricante, ip, porta, canal, stream_tipo, usuario, senha, rtsp_url, go2rtc_stream_existente, identificador_externo, ia_pediatria)
    erro = _validar_e_aplicar(request, db, camera, campos, destino_ids, criando=True)
    if erro:
        db.rollback()
        return erro_no_form(erro)
    db.add(camera)
    db.flush()
    registrar_auditoria(db, operador.login, "camera_criada", camera.nome)
    _depois_de_gravar(request, db, camera.id)
    return _lista_oob(request, db, operador)


@router.put("/{camera_id}")
def atualizar(
    camera_id: int, request: Request,
    nome: str = Form(""), fabricante: str = Form("intelbras"), ip: str = Form(""), porta: int = Form(554), canal: int = Form(1),
    stream_tipo: str = Form("principal"), usuario: str = Form(""), senha: str = Form(""), rtsp_url: str = Form(""),
    go2rtc_stream_existente: str = Form(""), identificador_externo: str = Form(""), ia_pediatria: str | None = Form(None),
    destino_ids: list[int] = Form([]),
    operador: Usuario = Depends(usuario_atual), db: Session = DbDep,
):
    camera = db.get(Camera, camera_id)
    if camera is None:
        return erro_no_form("Câmera não encontrada.")
    campos = _campos_do_form(nome, fabricante, ip, porta, canal, stream_tipo, usuario, senha, rtsp_url, go2rtc_stream_existente, identificador_externo, ia_pediatria)
    erro = _validar_e_aplicar(request, db, camera, campos, destino_ids, criando=False)
    if erro:
        db.rollback()
        return erro_no_form(erro)
    registrar_auditoria(db, operador.login, "camera_editada", camera.nome)
    _depois_de_gravar(request, db, camera.id)
    return _lista_oob(request, db, operador)


@router.delete("/{camera_id}")
def remover(camera_id: int, request: Request, operador: Usuario = Depends(usuario_atual), db: Session = DbDep):
    camera = db.get(Camera, camera_id)
    if camera is None:
        return HTMLResponse("")
    registrar_auditoria(db, operador.login, "camera_removida", camera.nome)
    db.delete(camera)
    _depois_de_gravar(request, db, camera_id)
    return HTMLResponse("")
