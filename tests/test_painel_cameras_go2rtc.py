from __future__ import annotations

import re
import sys
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
import yaml
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select

from src.jutta_ped.painel.crypto import Cifra
from src.jutta_ped.painel.go2rtc import (
    BASE_YAML_PADRAO,
    Go2rtcProcesso,
    mascarar_url,
    montar_url_rtsp,
    render_streams_yaml,
)
from src.jutta_ped.painel.models import Camera, Fabricante, Papel, StreamTipo, Usuario
from src.jutta_ped.painel.panel import mount_panel
from src.jutta_ped.painel.security import hash_senha
from painel_helpers import FakeGerente, FakeGo2rtc, SENHA_ADMIN, _dados_intelbras, _hx, _login, _token



@pytest.fixture()
def ambiente(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("JUTTA_ADMIN_PASSWORD", SENHA_ADMIN)
    go2rtc = FakeGo2rtc()
    gerente = FakeGerente()
    app = FastAPI()
    database = mount_panel(
        app,
        db_url=f"sqlite:///{(tmp_path / 'painel.db').as_posix()}",
        runtime_dir=tmp_path,
        manager=gerente,
        config_factory=lambda **kw: SimpleNamespace(**kw),
        background=False,
        go2rtc_transport=httpx.MockTransport(go2rtc),
    )
    app.state.painel.ia.dormir = lambda _s: None
    with TestClient(app, follow_redirects=False) as client:
        yield SimpleNamespace(client=client, database=database, go2rtc=go2rtc, gerente=gerente, app=app, runtime=tmp_path, servicos=app.state.painel)
    database.dispose()


def _camera(ambiente, nome="Recepção") -> Camera:
    with ambiente.database.session() as db:
        return db.scalar(select(Camera).where(Camera.nome == nome))


# ------------------------------------------------------------ URLs / yaml / cifra


def test_cifra_roundtrip_e_chave_persistida(tmp_path):
    arquivo = tmp_path / "k.key"
    c1 = Cifra.carregar_ou_criar(arquivo)
    token = c1.cifrar("segredo")
    assert "segredo" not in token
    assert Cifra.carregar_ou_criar(arquivo).decifrar(token) == "segredo"
    with pytest.raises(ValueError):
        Cifra.carregar_ou_criar(tmp_path / "outra.key").decifrar(token)


@pytest.mark.parametrize(
    "fabricante,tipo,esperado",
    [
        (Fabricante.intelbras, StreamTipo.principal, "rtsp://adm:p%40ss%231%24@10.0.0.5:554/cam/realmonitor?channel=2&subtype=0"),
        (Fabricante.intelbras, StreamTipo.extra, "rtsp://adm:p%40ss%231%24@10.0.0.5:554/cam/realmonitor?channel=2&subtype=1"),
        (Fabricante.hikvision, StreamTipo.principal, "rtsp://adm:p%40ss%231%24@10.0.0.5:554/Streaming/Channels/201"),
        (Fabricante.hikvision, StreamTipo.extra, "rtsp://adm:p%40ss%231%24@10.0.0.5:554/Streaming/Channels/202"),
    ],
)
def test_montar_url_rtsp_por_fabricante(tmp_path, fabricante, tipo, esperado):
    cifra = Cifra.carregar_ou_criar(tmp_path / "k")
    cam = Camera(fabricante=fabricante, stream_tipo=tipo, ip="10.0.0.5", porta=554, canal=2, usuario="adm", senha_enc=cifra.cifrar("p@ss#1$"))
    assert montar_url_rtsp(cam, cifra) == esperado


def test_yaml_gerado_e_valido_com_urls_dificeis():
    urls = {"cam_1": 'rtsp://u:p%40@h:554/cam/realmonitor?channel=1&subtype=0', "cam_2": 'rtsp://h/x#y"z'}
    assert yaml.safe_load(render_streams_yaml(urls))["streams"] == urls
    assert yaml.safe_load(render_streams_yaml({}))["streams"] == {}


def test_mascarar_url_esconde_credenciais():
    assert mascarar_url("rtsp://admin:segredo@10.0.0.1/x") == "rtsp://***:***@10.0.0.1/x"
    assert "segredo" not in mascarar_url("rtsps://a:segredo@h")


# ------------------------------------------------------------ CRUD + sync


def test_comum_cadastra_camera_e_go2rtc_recebe_stream(ambiente):
    a = ambiente
    with a.database.session() as db:
        db.add(Usuario(login="operador", senha_hash=hash_senha("senha-comum-123"), papel=Papel.comum, ativo=True))
    _login(a.client, "operador", "senha-comum-123")
    resposta = _hx(a.client, "POST", "/painel/cameras", data=_dados_intelbras())
    assert resposta.status_code == 200 and "Recepção" in resposta.text and 'hx-swap-oob="true"' in resposta.text
    assert "sincronizado" in resposta.text
    assert a.go2rtc.streams == {"cam_1": "rtsp://admin:p%40ss%231@10.0.0.5:554/cam/realmonitor?channel=1&subtype=0"}
    doc = yaml.safe_load((a.runtime / "go2rtc" / "streams.yaml").read_text(encoding="utf-8"))
    assert list(doc["streams"]) == ["cam_1"]


def test_senha_nunca_aparece_em_claro_no_banco_nem_no_html(ambiente):
    a = ambiente
    _login(a.client)
    _hx(a.client, "POST", "/painel/cameras", data=_dados_intelbras())
    cam = _camera(a)
    assert cam.senha_enc and "p@ss#1" not in cam.senha_enc
    for caminho in ("/painel/cameras", f"/painel/cameras/{cam.id}/editar"):
        html = a.client.get(caminho).text
        assert "p@ss#1" not in html and "p%40ss" not in html


def test_editar_sem_senha_mantem_a_atual_e_atualiza_stream(ambiente):
    a = ambiente
    _login(a.client)
    _hx(a.client, "POST", "/painel/cameras", data=_dados_intelbras())
    cam = _camera(a)
    r = _hx(a.client, "PUT", f"/painel/cameras/{cam.id}", data=_dados_intelbras(ip="10.0.0.9", senha=""))
    assert r.status_code == 200
    assert a.go2rtc.streams["cam_1"] == "rtsp://admin:p%40ss%231@10.0.0.9:554/cam/realmonitor?channel=1&subtype=0"
    assert ("DELETE", "cam_1") in a.go2rtc.chamadas  # troca de origem = remove + cria


def test_remover_camera_remove_stream_e_yaml(ambiente):
    a = ambiente
    _login(a.client)
    _hx(a.client, "POST", "/painel/cameras", data=_dados_intelbras())
    cam = _camera(a)
    assert _hx(a.client, "DELETE", f"/painel/cameras/{cam.id}").status_code == 200
    assert a.go2rtc.streams == {}
    assert yaml.safe_load((a.runtime / "go2rtc" / "streams.yaml").read_text(encoding="utf-8"))["streams"] == {}
    assert _camera(a) is None


def test_go2rtc_offline_nao_impede_salvar_e_recupera_depois(ambiente):
    a = ambiente
    _login(a.client)
    a.go2rtc.online = False
    r = _hx(a.client, "POST", "/painel/cameras", data=_dados_intelbras())
    assert r.status_code == 200 and "pendente" in r.text
    assert _camera(a).go2rtc_sync == "pendente"
    assert (a.runtime / "go2rtc" / "streams.yaml").exists()  # proximo boot do go2rtc ja le a camera
    a.go2rtc.online = True
    resumo = a.servicos.sync.reconcile()
    assert resumo.ok == 1 and _camera(a).go2rtc_sync == "ok"
    assert "cam_1" in a.go2rtc.streams


def test_erro_de_conexao_nao_vaza_credenciais(ambiente):
    a = ambiente
    _login(a.client)
    a.go2rtc.online = False
    _hx(a.client, "POST", "/painel/cameras", data=_dados_intelbras())
    detalhe = _camera(a).go2rtc_sync_erro or ""
    assert "p%40ss" not in detalhe and "p@ss" not in detalhe and "rtsp://" not in detalhe


def test_reconcile_reaplica_stream_perdido_e_remove_orfaos_sem_tocar_nos_de_fora(ambiente):
    a = ambiente
    _login(a.client)
    _hx(a.client, "POST", "/painel/cameras", data=_dados_intelbras())
    a.go2rtc.streams.clear()                                # go2rtc reiniciou e perdeu tudo
    a.go2rtc.streams.update({"cam_99": "rtsp://x", "demo": "ffmpeg:video.mp4"})  # orfao gerenciado + stream alheio
    a.servicos.sync.reconcile()
    assert set(a.go2rtc.streams) == {"cam_1", "demo"}


def test_camera_de_teste_usa_stream_existente_sem_ser_gerenciada(ambiente):
    a = ambiente
    _login(a.client)
    a.go2rtc.streams["demo"] = "ffmpeg:video.mp4"
    r = _hx(a.client, "POST", "/painel/cameras", data={"nome": "Teste", "fabricante": "go2rtc_teste", "go2rtc_stream_existente": "demo"})
    assert r.status_code == 200 and "stream: demo" in r.text
    a.servicos.sync.reconcile()
    assert a.go2rtc.streams == {"demo": "ffmpeg:video.mp4"}  # intocado
    assert _camera(a, "Teste").go2rtc_stream_name == "demo"


def test_rtsp_generico_e_mascarado_ao_editar_e_mantido_se_devolvido_mascarado(ambiente):
    a = ambiente
    _login(a.client)
    _hx(a.client, "POST", "/painel/cameras", data={"nome": "Livre", "fabricante": "rtsp_generico", "rtsp_url": "rtsp://joao:segredo@10.1.1.1/live"})
    cam = _camera(a, "Livre")
    assert a.go2rtc.streams[f"cam_{cam.id}"] == "rtsp://joao:segredo@10.1.1.1/live"
    html = a.client.get(f"/painel/cameras/campos-fonte?fabricante=rtsp_generico&camera_id={cam.id}").text
    assert "segredo" not in html and "***" in html
    _hx(a.client, "PUT", f"/painel/cameras/{cam.id}", data={"nome": "Livre", "fabricante": "rtsp_generico", "rtsp_url": "rtsp://***:***@10.1.1.1/live"})
    assert a.go2rtc.streams[f"cam_{cam.id}"] == "rtsp://joao:segredo@10.1.1.1/live"


@pytest.mark.parametrize(
    "dados,trecho",
    [
        (_dados_intelbras(nome=""), "nome"),
        (_dados_intelbras(ip="ip invalido!"), "IP ou host"),
        (_dados_intelbras(usuario=""), "usuário"),
        (_dados_intelbras(senha=""), "senha"),
        (_dados_intelbras(porta="70000"), "Porta"),
        (_dados_intelbras(canal="0"), "Canal"),
        ({"nome": "R", "fabricante": "rtsp_generico", "rtsp_url": "http://nao-rtsp"}, "rtsp://"),
        ({"nome": "T", "fabricante": "go2rtc_teste"}, "Selecione o stream"),
        (_dados_intelbras(fabricante="xpto"), "Fabricante"),
    ],
)
def test_validacoes_do_formulario(ambiente, dados, trecho):
    a = ambiente
    _login(a.client)
    r = _hx(a.client, "POST", "/painel/cameras", data=dados)
    assert trecho.lower() in r.text.lower()
    assert r.headers["HX-Retarget"] == "#form-area"
    assert a.go2rtc.streams == {}


def test_nome_duplicado_e_recusado(ambiente):
    a = ambiente
    _login(a.client)
    _hx(a.client, "POST", "/painel/cameras", data=_dados_intelbras())
    r = _hx(a.client, "POST", "/painel/cameras", data=_dados_intelbras(nome="recepção"))
    assert "Já existe" in r.text


def test_limite_de_cameras_com_ia(ambiente):
    a = ambiente
    _login(a.client)
    token = _token(a.client, "/painel/configuracoes")
    dados = {"go2rtc_api_url": "http://127.0.0.1:1984", "go2rtc_public_url": "http://127.0.0.1:1984", "go2rtc_rtsp_url": "rtsp://127.0.0.1:8554",
             "go2rtc_exe": "", "cooldown_seconds": "120", "evidence_retention_days": "30", "public_base_url": "", "max_cameras_ia": "1", "csrf_token": token}
    assert a.client.post("/painel/configuracoes", data=dados).status_code == 303
    assert _hx(a.client, "POST", "/painel/cameras", data=_dados_intelbras(nome="A")).status_code == 200
    r = _hx(a.client, "POST", "/painel/cameras", data=_dados_intelbras(nome="B"))
    assert "Limite de 1" in r.text
    ok = _hx(a.client, "POST", "/painel/cameras", data={k: v for k, v in _dados_intelbras(nome="C").items() if k != "ia_pediatria"})
    assert 'hx-swap-oob="true"' in ok.text  # sem IA nao conta no limite


def test_campos_fonte_e_upload_de_yaml(ambiente):
    a = ambiente
    _login(a.client)
    a.go2rtc.streams.update({"demo": "x", "cam_7": "y"})
    html = a.client.get("/painel/cameras/campos-fonte?fabricante=go2rtc_teste").text
    assert ">demo<" in html and "cam_7" not in html  # os gerenciados nao sao oferecidos
    assert 'name="ip"' in a.client.get("/painel/cameras/campos-fonte?fabricante=hikvision").text
    token = _token(a.client, "/painel/cameras")
    r = a.client.post("/painel/cameras/streams-do-yaml", headers={"X-CSRF-Token": token},
                      files={"arquivo_yaml": ("go2rtc.yaml", b"streams:\n  garagem: rtsp://x\n  hall: rtsp://y\n")})
    assert ">garagem<" in r.text and ">hall<" in r.text
    ruim = a.client.post("/painel/cameras/streams-do-yaml", headers={"X-CSRF-Token": token}, files={"arquivo_yaml": ("x.yaml", b": : [")})
    assert "nenhum stream" in ruim.text


def test_camera_exige_login_e_csrf(ambiente):
    a = ambiente
    assert a.client.get("/painel/cameras").status_code == 303
    _login(a.client)
    assert a.client.post("/painel/cameras", data=_dados_intelbras()).status_code == 403


# ------------------------------------------------------------ configuracoes


def test_configuracoes_so_admin_e_validam(ambiente):
    a = ambiente
    with a.database.session() as db:
        db.add(Usuario(login="operador", senha_hash=hash_senha("senha-comum-123"), papel=Papel.comum, ativo=True))
    _login(a.client)
    token = _token(a.client, "/painel/configuracoes")
    ruim = {"go2rtc_api_url": "ftp://x", "go2rtc_public_url": "http://a", "go2rtc_rtsp_url": "rtsp://a", "go2rtc_exe": "", "cooldown_seconds": "1",
            "evidence_retention_days": "1", "public_base_url": "", "max_cameras_ia": "1", "csrf_token": token}
    assert a.client.post("/painel/configuracoes", data=ruim).status_code == 422
    assert a.client.get("/painel/configuracoes").status_code == 200
    a.client.post("/logout", data={"csrf_token": token})
    _login(a.client, "operador", "senha-comum-123")
    assert a.client.get("/painel/configuracoes").status_code == 403


# ------------------------------------------------------------ orquestrador de IA


def _cfg_factory(**kw):
    return SimpleNamespace(**kw)


def test_ia_so_inicia_com_stream_sincronizado_e_le_o_restream(ambiente):
    a = ambiente
    _login(a.client)
    a.go2rtc.online = False
    _hx(a.client, "POST", "/painel/cameras", data=_dados_intelbras())
    a.servicos.ia.reconcile()
    assert a.gerente.ativas == {} and a.servicos.ia.estado(1)["estado"] == "aguardando_stream"
    a.go2rtc.online = True
    a.servicos.sync.reconcile()
    a.servicos.ia.reconcile()
    cfg = a.gerente.ativas["cam_1"]
    assert cfg.source == "rtsp://127.0.0.1:8554/cam_1" and cfg.stream_id == "cam_1" and cfg.cooldown_seconds == 120.0
    assert a.servicos.ia.estado(1)["estado"] == "rodando"


def test_ia_para_quando_camera_e_removida_ou_ia_desligada(ambiente):
    a = ambiente
    _login(a.client)
    _hx(a.client, "POST", "/painel/cameras", data=_dados_intelbras(nome="A"))
    _hx(a.client, "POST", "/painel/cameras", data=_dados_intelbras(nome="B", ip="10.0.0.6"))
    a.servicos.ia.reconcile()
    assert set(a.gerente.ativas) == {"cam_1", "cam_2"}
    _hx(a.client, "PUT", "/painel/cameras/1", data={k: v for k, v in _dados_intelbras(nome="A").items() if k != "ia_pediatria"})
    _hx(a.client, "DELETE", "/painel/cameras/2")
    a.servicos.ia.reconcile()
    assert a.gerente.ativas == {}


def test_ia_reinicia_quando_camera_e_editada(ambiente):
    a = ambiente
    _login(a.client)
    _hx(a.client, "POST", "/painel/cameras", data=_dados_intelbras())
    a.servicos.ia.reconcile()
    antiga = a.gerente.ativas["cam_1"]
    _hx(a.client, "PUT", "/painel/cameras/1", data=_dados_intelbras(ip="10.0.0.9", senha=""))
    a.servicos.ia.reconcile()
    assert a.gerente.ativas["cam_1"] is not antiga


def test_falha_de_partida_e_isolada_com_backoff(ambiente):
    a = ambiente
    _login(a.client)
    _hx(a.client, "POST", "/painel/cameras", data=_dados_intelbras(nome="A"))
    _hx(a.client, "POST", "/painel/cameras", data=_dados_intelbras(nome="B", ip="10.0.0.6"))
    ia = a.servicos.ia
    agora = [100.0]
    ia.relogio = lambda: agora[0]
    a.gerente.falhar = True
    ia.reconcile()
    assert ia.estado(1)["estado"] == "erro" and "modelo indisponivel" in ia.estado(1)["erro"]
    assert a.gerente.ativas == {}
    a.gerente.falhar = False
    ia.reconcile()                      # ainda em backoff: nao tenta de novo
    assert a.gerente.ativas == {}
    agora[0] += 10
    ia.reconcile()
    assert set(a.gerente.ativas) == {"cam_1", "cam_2"}


def test_partidas_sao_escalonadas(ambiente):
    a = ambiente
    _login(a.client)
    for i, nome in enumerate("ABC"):
        _hx(a.client, "POST", "/painel/cameras", data=_dados_intelbras(nome=nome, ip=f"10.0.0.{i + 10}"))
    esperas: list[float] = []
    a.servicos.ia.dormir = esperas.append
    a.servicos.ia.reconcile()
    assert len(a.gerente.ativas) == 3 and esperas == [3.0, 3.0]  # nenhuma espera antes da primeira


# ------------------------------------------------------------ processo go2rtc


class FakePopen:
    def __init__(self, cmd, **kw):
        self.cmd, self.kw, self.pid, self.returncode = cmd, kw, 4321, None

    def poll(self):
        return self.returncode

    def terminate(self):
        self.returncode = 0

    def wait(self, timeout=None):
        return 0

    def kill(self):
        self.returncode = -9


def _processo(tmp_path, api_responde: bool, exe: str, popen=FakePopen, relogio=lambda: 0.0):
    class _Cliente:
        def listar(self):
            if not api_responde:
                from src.jutta_ped.painel.go2rtc import Go2rtcIndisponivel
                raise Go2rtcIndisponivel("fora")
            return {}

        def close(self):
            pass

    return Go2rtcProcesso(runtime_dir=tmp_path, configurado_exe=lambda: exe, client_factory=_Cliente, popen=popen, relogio=relogio)


def test_processo_sem_executavel_informa_onde_colocar(tmp_path):
    p = _processo(tmp_path, False, "")
    assert p.garantir() == "sem_executavel" and "Configuracoes" in p.detalhe


def test_processo_externo_nao_e_reiniciado(tmp_path):
    p = _processo(tmp_path, True, "")
    assert p.garantir() == "externo"


def test_processo_sobe_com_os_dois_arquivos_e_nao_sobrescreve_config_do_usuario(tmp_path):
    exe = tmp_path / ("go2rtc.exe" if sys.platform == "win32" else "go2rtc")
    exe.write_bytes(b"")
    (tmp_path / "go2rtc").mkdir()
    (tmp_path / "go2rtc" / "go2rtc.yaml").write_text("api:\n  listen: ':9999'\n", encoding="utf-8")
    lancados: list[FakePopen] = []

    def popen(cmd, **kw):
        lancados.append(FakePopen(cmd, **kw))
        return lancados[-1]

    p = _processo(tmp_path, False, str(exe), popen=popen)
    assert p.garantir() == "rodando"
    assert lancados[0].cmd[0] == str(exe) and lancados[0].cmd.count("-config") == 2
    assert (tmp_path / "go2rtc" / "go2rtc.yaml").read_text(encoding="utf-8") == "api:\n  listen: ':9999'\n"
    assert yaml.safe_load((tmp_path / "go2rtc" / "streams.yaml").read_text(encoding="utf-8"))["streams"] == {}
    assert yaml.safe_load(BASE_YAML_PADRAO)["rtsp"]["listen"] == "127.0.0.1:8554"
    p.parar()
    assert lancados[0].returncode == 0


def test_processo_reinicia_se_morrer_respeitando_intervalo(tmp_path):
    exe = tmp_path / "go2rtc-bin"
    exe.write_bytes(b"")
    lancados: list[FakePopen] = []
    agora = [1000.0]

    def popen(cmd, **kw):
        lancados.append(FakePopen(cmd, **kw))
        return lancados[-1]

    p = _processo(tmp_path, False, str(exe), popen=popen, relogio=lambda: agora[0])
    p.garantir()
    lancados[0].returncode = 1                 # morreu
    p.garantir()                                # dentro do intervalo minimo: nao relanca
    assert len(lancados) == 1
    agora[0] += 11
    assert p.garantir() == "rodando" and len(lancados) == 2


# ------------------------------------------------------------ escrita do streams.yaml (Windows/OneDrive)


def test_escrita_atomica_tenta_de_novo_quando_destino_esta_travado(tmp_path, monkeypatch):
    import os

    from src.jutta_ped.painel import go2rtc as modulo

    real, falhas = os.replace, {"n": 0}

    def replace_travado(origem, destino):
        if falhas["n"] < 2:
            falhas["n"] += 1
            raise PermissionError(5, "Acesso negado")
        return real(origem, destino)

    monkeypatch.setattr(modulo.os, "replace", replace_travado)
    monkeypatch.setattr(modulo.time, "sleep", lambda _s: None)
    alvo = tmp_path / "streams.yaml"
    modulo.escrever_atomico(alvo, "streams: {}\n")
    assert alvo.read_text(encoding="utf-8") == "streams: {}\n" and falhas["n"] == 2
    assert not (tmp_path / "streams.yaml.tmp").exists()


def test_destino_sempre_travado_cai_para_gravacao_direta_sem_deixar_lixo(tmp_path, monkeypatch):
    from src.jutta_ped.painel import go2rtc as modulo

    def sempre_negado(origem, destino):
        raise PermissionError(5, "Acesso negado")

    monkeypatch.setattr(modulo.os, "replace", sempre_negado)
    monkeypatch.setattr(modulo.time, "sleep", lambda _s: None)
    alvo = tmp_path / "streams.yaml"
    modulo.escrever_atomico(alvo, "streams: {}\n")
    assert alvo.read_text(encoding="utf-8") == "streams: {}\n"
    assert not (tmp_path / "streams.yaml.tmp").exists()


def test_falha_ao_gravar_yaml_nao_impede_sincronizar_pela_api(ambiente, monkeypatch):
    from src.jutta_ped.painel import go2rtc as modulo

    a = ambiente
    _login(a.client)

    def quebrado(*_a, **_k):
        raise PermissionError(5, "Acesso negado")

    monkeypatch.setattr(modulo, "escrever_atomico", quebrado)
    r = _hx(a.client, "POST", "/painel/cameras", data=_dados_intelbras())
    assert r.status_code == 200
    assert "cam_1" in a.go2rtc.streams                 # a API a quente funcionou
    assert _hx(a.client, "DELETE", "/painel/cameras/1").status_code == 200
    assert a.go2rtc.streams == {}


def test_camera_de_teste_fica_sincronizada_e_a_ia_sobe(ambiente):
    a = ambiente
    _login(a.client)
    a.go2rtc.streams["demo"] = "ffmpeg:video.mp4"
    _hx(a.client, "POST", "/painel/cameras", data={"nome": "Teste", "fabricante": "go2rtc_teste", "go2rtc_stream_existente": "demo", "ia_pediatria": "on"})
    assert _camera(a, "Teste").go2rtc_sync == "ok"
    a.servicos.ia.reconcile()
    cfg = a.gerente.ativas["cam_1"]
    assert cfg.source == "rtsp://127.0.0.1:8554/demo" and cfg.camera_id == "cam_1"


def test_encerramento_para_todas_as_sessoes_de_ia_antes_de_sair(ambiente):
    a = ambiente
    _login(a.client)
    _hx(a.client, "POST", "/painel/cameras", data=_dados_intelbras(nome="A"))
    _hx(a.client, "POST", "/painel/cameras", data=_dados_intelbras(nome="B", ip="10.0.0.6"))
    a.servicos.ia.reconcile()
    a.gerente.ativas["api_externa"] = SimpleNamespace(camera_id="api_externa")      # sessao aberta pela API, nao pelo painel
    assert len(a.gerente.ativas) == 3
    a.servicos.parar()
    assert a.gerente.ativas == {}
    assert a.servicos.ia.estado(1)["estado"] == "desligada"
