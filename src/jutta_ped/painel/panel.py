from __future__ import annotations

import logging
import os
import secrets
from collections.abc import Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import func, select
from starlette.middleware.sessions import SessionMiddleware

import httpx

from src.jutta_ped.painel import routes_auth, routes_cameras, routes_config, routes_usuarios
from src.jutta_ped.painel.config_service import ler_config
from src.jutta_ped.painel.crypto import Cifra
from src.jutta_ped.painel.db import Database
from src.jutta_ped.painel.go2rtc import Go2rtcClient, Go2rtcProcesso, Go2rtcSync
from src.jutta_ped.painel.servicos import GerenteIa, IaOrquestrador, PainelServicos, config_loader_para
from src.jutta_ped.painel.deps import NaoAutenticado, SemPermissao, WEB_DIR, exige_admin, render, usuario_atual
from src.jutta_ped.painel.models import Papel, Usuario
from src.jutta_ped.painel.security import LimitadorLogin, carregar_ou_criar_segredo, hash_senha

logger = logging.getLogger("jutta_ped.painel")

ROOT = Path(__file__).resolve().parents[3]
DEFAULT_RUNTIME_DIR = ROOT / "runtime"
SESSAO_MAX_IDADE_SEGUNDOS = 12 * 3600


def garantir_admin_inicial(database: Database) -> str | None:
    """Cria o primeiro administrador se nao houver usuario algum.

    Senha vem de JUTTA_ADMIN_PASSWORD; sem ela, gera uma aleatoria e a exibe
    uma unica vez no log do servico. Devolve a senha gerada (ou None).
    """
    with database.session() as db:
        if (db.scalar(select(func.count()).select_from(Usuario)) or 0) > 0:
            return None
        login = os.environ.get("JUTTA_ADMIN_LOGIN", "admin").strip().lower() or "admin"
        senha = os.environ.get("JUTTA_ADMIN_PASSWORD") or ""
        gerada = None
        if not senha:
            senha = gerada = secrets.token_urlsafe(12)
        db.add(Usuario(login=login, senha_hash=hash_senha(senha), papel=Papel.admin, ativo=True))
    if gerada:
        logger.warning("Administrador inicial criado: login=%s senha=%s (troque-a em Usuarios)", login, gerada)
    return gerada


_placeholders = APIRouter(prefix="/painel")


def _secao_em_construcao(caminho: str, titulo: str, chave: str, *, admin: bool = False) -> None:
    dependencia = exige_admin if admin else usuario_atual

    @_placeholders.get(caminho, name=f"placeholder_{chave}")
    def _pagina(request: Request, usuario: Usuario = Depends(dependencia)):
        return render(request, "em_construcao.html", usuario, titulo=titulo, ativo=chave)


_secao_em_construcao("/monitoramento", "Monitoramento", "monitoramento")
_secao_em_construcao("/relatorio", "Relatório", "relatorio")
_secao_em_construcao("/destinos", "Destinos", "destinos", admin=True)


def montar_servicos(
    database: Database,
    runtime_dir: Path,
    *,
    manager: GerenteIa | None,
    config_factory: Callable[..., Any] | None,
    go2rtc_transport: httpx.BaseTransport | None,
    popen: Callable[..., Any] | None,
) -> PainelServicos:
    cifra = Cifra.carregar_ou_criar(runtime_dir / "painel_cifra.key")
    config_loader = config_loader_para(database)

    def client_factory() -> Go2rtcClient:
        return Go2rtcClient(config_loader()["go2rtc_api_url"], transport=go2rtc_transport)

    processo_kwargs: dict[str, Any] = {"popen": popen} if popen else {}
    go2rtc_dir = runtime_dir / "go2rtc"
    processo = Go2rtcProcesso(
        runtime_dir=runtime_dir,
        configurado_exe=lambda: config_loader()["go2rtc_exe"],
        client_factory=client_factory,
        **processo_kwargs,
    )
    sync = Go2rtcSync(database, cifra, go2rtc_dir / "streams.yaml", client_factory=client_factory)
    ia = None
    if manager is not None and config_factory is not None:
        ia = IaOrquestrador(database=database, gerente=manager, config_loader=config_loader, config_factory=config_factory)
    servicos = PainelServicos(database=database, sync=sync, go2rtc=processo, ia=ia, runtime_dir=runtime_dir)
    servicos.cifra = cifra
    return servicos


def mount_panel(
    app: FastAPI,
    *,
    db_url: str | None = None,
    runtime_dir: Path | None = None,
    manager: GerenteIa | None = None,
    config_factory: Callable[..., Any] | None = None,
    background: bool = True,
    go2rtc_transport: httpx.BaseTransport | None = None,
    popen: Callable[..., Any] | None = None,
) -> Database:
    """Acopla o painel web (login, sessao, RBAC, cameras, go2rtc) a um app FastAPI existente.

    `manager`/`config_factory`: gerente de sessoes de IA (PediatriaSessionManager e
    SessionStartConfig); sem eles o painel funciona mas nao liga a IA.
    `background=False` nao inicia as threads de go2rtc/IA (testes).
    """
    runtime_dir = runtime_dir or DEFAULT_RUNTIME_DIR
    db_url = db_url or os.environ.get("JUTTA_PANEL_DB") or f"sqlite:///{(runtime_dir / 'painel.db').as_posix()}"
    database = Database(db_url)
    database.create_all()
    garantir_admin_inicial(database)

    servicos = montar_servicos(database, runtime_dir, manager=manager, config_factory=config_factory, go2rtc_transport=go2rtc_transport, popen=popen)
    app.state.painel = servicos
    app.state.painel_db = database
    app.state.painel_limitador = LimitadorLogin()
    app.add_middleware(
        SessionMiddleware,
        secret_key=carregar_ou_criar_segredo(runtime_dir / "painel_secret.key"),
        session_cookie="jutta_sess",
        max_age=SESSAO_MAX_IDADE_SEGUNDOS,
        same_site="lax",
        https_only=False,  # painel hoje roda em HTTP interno; ligar quando houver TLS/proxy
    )

    @app.exception_handler(NaoAutenticado)
    async def _nao_autenticado(request: Request, _exc: NaoAutenticado):
        if request.headers.get("hx-request"):
            return HTMLResponse("", status_code=401, headers={"HX-Redirect": "/login"})
        return RedirectResponse("/login", status_code=303)

    @app.exception_handler(SemPermissao)
    async def _sem_permissao(request: Request, _exc: SemPermissao):
        return render(request, "erro.html", None, status_code=403, titulo="Acesso negado", mensagem="Seu perfil não tem permissão para esta área.")

    @app.get("/", include_in_schema=False)
    def _raiz():
        return RedirectResponse("/painel/monitoramento", status_code=303)

    if background:
        original = app.router.lifespan_context

        @asynccontextmanager
        async def _lifespan(a: FastAPI):
            servicos.iniciar()
            try:
                async with original(a) as estado:
                    yield estado
            finally:
                servicos.parar()

        app.router.lifespan_context = _lifespan

    app.mount("/static", StaticFiles(directory=str(WEB_DIR / "static")), name="painel_static")
    app.include_router(routes_auth.router)
    app.include_router(routes_usuarios.router)
    app.include_router(routes_cameras.router)
    app.include_router(routes_config.router)
    app.include_router(_placeholders)
    return database
