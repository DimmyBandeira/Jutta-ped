"""Ambiente compartilhado pelos testes de receitas, sombra e dashboard do painel (pacotes sinteticos + BackendFalso)."""
from __future__ import annotations

import time
from pathlib import Path
from types import SimpleNamespace

import httpx
from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from fastapi.testclient import TestClient
from jinja2.exceptions import TemplateNotFound
from painel_helpers import FakeGerente, FakeGo2rtc, SENHA_ADMIN, _dados_intelbras, _hx, _login, _token
from test_especialistas_executor import BackendFalso, instalar_ativar, manifesto_bebe, manifesto_cor
from test_motor_sombra import BEBE, COR, SIM_AZUL, obs  # noqa: F401
from test_motor_sombra_runtime import quadro_com  # noqa: F401

from src.jutta_ped.painel import deps
from src.jutta_ped.painel.models import Papel, Usuario
from src.jutta_ped.painel.panel import mount_panel
from src.jutta_ped.painel.security import hash_senha

SENHA_COMUM = "senha-comum-123"


class GerenteContado(FakeGerente):
    """FakeGerente que conta as partidas (a sombra NUNCA pode causar uma nova)."""

    def __init__(self) -> None:
        super().__init__()
        self.partidas: list = []

    def enable_camera(self, config):
        self.partidas.append(config)
        return super().enable_camera(config)


def dados_receita(nome: str = "uniforme-bebe", **extra) -> dict:
    d = {"nome": nome, "descricao": "receita de teste", "combinacao": "todas", "vinc_usar": [BEBE, COR],
         f"vinc_orcamento__{BEBE}": "500", f"vinc_margem__{BEBE}": "0", f"vinc_orcamento__{COR}": "500", f"vinc_margem__{COR}": "",
         "cond_esp": [BEBE, COR, "", ""], "cond_attr": ["bebe_no_colo", "cor_uniforme", "", ""],
         "cond_igual": ["sim", "azul", "", ""], "cond_validade": ["", "", "", ""]}
    d.update(extra)
    return d


class Ambiente:
    def __init__(self, client, database, app, tmp, gerente, backend, contextos) -> None:
        self.client, self.database, self.app, self.tmp, self.gerente, self.backend = client, database, app, tmp, gerente, backend
        self._contextos = contextos
        self.servicos = app.state.painel
        self.inst = app.state.especialistas

    # ---- sessao
    def login_admin(self) -> None:
        _login(self.client)

    def criar_comum(self) -> None:
        with self.database.session() as db:
            db.add(Usuario(login="comum", senha_hash=hash_senha(SENHA_COMUM), papel=Papel.comum, ativo=True))

    def login_comum(self) -> None:
        self.criar_comum()
        _login(self.client, "comum", SENHA_COMUM)

    # ---- requisicoes
    def token(self) -> str:
        return _token(self.client, "/painel/cameras")

    def post(self, caminho: str, data: dict | None = None, token: bool = True, **kw):
        dados = dict(data or {})
        if token:
            dados["csrf_token"] = self.token()
        return self.client.post(caminho, data=dados, **kw)

    def contexto(self, template: str) -> dict:
        achados = [c for n, c in self._contextos if n == template]
        assert achados, f"template {template!r} nao foi renderizado ({[n for n, _ in self._contextos]})"
        return achados[-1]

    def limpar_contextos(self) -> None:
        self._contextos.clear()

    # ---- cenario
    def instalar_especialistas(self) -> None:
        instalar_ativar(self.inst, self.tmp, manifesto_bebe(), b"pesos-bebe")
        instalar_ativar(self.inst, self.tmp, manifesto_cor(), b"pesos-cor", "modelos/cor.pt")

    def criar_camera(self, **extra) -> int:
        r = _hx(self.client, "POST", "/painel/cameras", data=_dados_intelbras(**extra))
        assert r.status_code == 200, r.text[:300]
        with self.database.session() as db:
            from sqlalchemy import select

            from src.jutta_ped.painel.models import Camera
            return db.scalars(select(Camera.id).order_by(Camera.id.desc())).first()

    def criar_receita(self, **extra) -> int:
        """Devolve o id da VERSAO criada."""
        r = self.post("/painel/receitas", dados_receita(**extra))
        assert r.status_code == 303, r.text[:500]
        return self.ultima_versao_id()

    def ultima_versao_id(self) -> int:
        from sqlalchemy import select

        from src.jutta_ped.painel.models import ReceitaVersao
        with self.database.session() as db:
            return db.scalars(select(ReceitaVersao.id).order_by(ReceitaVersao.id.desc())).first()

    def associar_legado(self, camera_id: int, versao_id: int) -> None:
        """Associa direto no banco (como uma camera legada, anterior ao modo simples): a rota recusa receita multi-especialista."""
        from src.jutta_ped.painel.models import Camera
        with self.database.session() as db:
            db.get(Camera, camera_id).receita_versao_id = versao_id
        self.servicos.acordar()

    def camera(self, camera_id: int):
        from src.jutta_ped.painel.models import Camera
        with self.database.session() as db:
            return db.get(Camera, camera_id)

    def contar(self, modelo) -> int:
        from sqlalchemy import func, select
        with self.database.session() as db:
            return db.scalar(select(func.count()).select_from(modelo)) or 0

    def rodar_ia(self) -> None:
        self.servicos.sync.reconcile()
        self.servicos.ia.reconcile()

    def porta(self, camera_id: int):
        return self.gerente.ativas[f"cam_{camera_id}"].motor_sombra

    def alimentar(self, camera_id: int, quadro_id: int = 1, geracao: int = 1, trilha: int = 5):
        """Entrega um quadro com uma pessoa a PORTA da camera (como o laco da sessao faria)."""
        p = self.porta(camera_id)
        o = obs(trilha, 50, 60, cam=f"cam_{camera_id}", ger=geracao, quadro_id=quadro_id, t=time.monotonic())
        p.processar(f"cam_{camera_id}", geracao, quadro_com(50, 60, SIM_AZUL), [o])
        alvo = p.alvo
        if alvo is not None:
            assert alvo.aguardar_ociosa(10)


def montar(tmp_path: Path, monkeypatch, *, com_ia: bool = True, evidence_roots=None) -> Ambiente:
    monkeypatch.setenv("JUTTA_ADMIN_PASSWORD", SENHA_ADMIN)
    gerente = GerenteContado() if com_ia else None
    backend = BackendFalso(tipo_por_arquivo={"modelos/cor.pt": "classificacao"})
    go2rtc = FakeGo2rtc()
    app = FastAPI()
    database = mount_panel(
        app, db_url=f"sqlite:///{(tmp_path / 'painel.db').as_posix()}", runtime_dir=tmp_path / "rt", manager=gerente,
        config_factory=(lambda **kw: SimpleNamespace(**kw)) if com_ia else None, background=False,
        go2rtc_transport=httpx.MockTransport(go2rtc), evidence_roots=evidence_roots or [tmp_path / "evid"],
        especialistas_backends={"pt": backend, "openvino": backend},
    )
    if com_ia:
        app.state.painel.ia.dormir = lambda _s: None
    contextos: list = []
    original = deps.templates.TemplateResponse

    def capturar(request, nome, contexto=None, status_code=200, headers=None, **kw):
        contextos.append((nome, dict(contexto or {})))
        try:
            return original(request, nome, contexto, status_code=status_code, headers=headers, **kw)
        except TemplateNotFound:                                    # template do frontend ainda ausente: o contrato fica registrado
            return HTMLResponse(f"<!-- {nome} -->", status_code=status_code, headers=headers)

    monkeypatch.setattr(deps.templates, "TemplateResponse", capturar)
    client = TestClient(app, follow_redirects=False)
    client.__enter__()
    amb = Ambiente(client, database, app, tmp_path, gerente, backend, contextos)
    amb.go2rtc = go2rtc
    amb._fechar = lambda: (client.__exit__(None, None, None), app.state.painel.parar(), database.dispose())
    return amb


import pytest  # noqa: E402


@pytest.fixture()
def amb(tmp_path: Path, monkeypatch):
    """Painel com IA (gerente falso), sem login e SEM especialistas instalados."""
    a = montar(tmp_path, monkeypatch)
    yield a
    a._fechar()


@pytest.fixture()
def adm(amb):
    """Idem, logado como administrador e com dois especialistas sinteticos ativos."""
    amb.login_admin()
    amb.instalar_especialistas()
    return amb
