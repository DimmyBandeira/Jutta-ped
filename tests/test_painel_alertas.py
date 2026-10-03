from __future__ import annotations

import asyncio
import threading
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select

from painel_helpers import FakeGerente, FakeGo2rtc, SENHA_ADMIN, _dados_intelbras, _hx, _login, _token
from src.jutta_ped.painel.eventos import PainelEventos, formatar_sse
from src.jutta_ped.painel.models import Auditoria, Camera, EventoPediatria, Papel, Usuario
from src.jutta_ped.painel.panel import mount_panel
from src.jutta_ped.painel.security import hash_senha
from src.jutta_ped.service.runtime import PediatriaHeadlessSession, PediatriaSessionManager


@pytest.fixture()
def amb(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("JUTTA_ADMIN_PASSWORD", SENHA_ADMIN)
    evid = tmp_path / "evid"
    evid.mkdir()
    gerente = FakeGerente()
    app = FastAPI()
    database = mount_panel(
        app, db_url=f"sqlite:///{(tmp_path / 'painel.db').as_posix()}", runtime_dir=tmp_path, manager=gerente,
        config_factory=lambda **kw: SimpleNamespace(**kw), background=False,
        go2rtc_transport=httpx.MockTransport(FakeGo2rtc()), evidence_roots=[evid],
    )
    app.state.painel.ia.dormir = lambda _s: None
    app.state.painel_sse_intervalo = 0.01
    app.state.painel_sse_max_pings = 0
    with TestClient(app, follow_redirects=False) as client:
        yield SimpleNamespace(client=client, database=database, eventos=app.state.painel.eventos, evid=evid, tmp=tmp_path, app=app)
    database.dispose()


def _foto(pasta: Path, nome: str, conteudo: bytes = b"\xff\xd8\xff\xe0fakejpeg") -> str:
    pasta.mkdir(parents=True, exist_ok=True)
    arquivo = pasta / nome
    arquivo.write_bytes(conteudo)
    return str(arquivo)


def _payload(amb, frame=1, tipo="popup_alert", camera_id="cam_1", sessao="sess_a", com_fotos=True, **extra):
    evidencias = {}
    if com_fotos:
        pasta = amb.evid / sessao
        evidencias = {"crop": _foto(pasta, f"{frame}_crop.jpg"), "annotated": _foto(pasta, f"{frame}_bbox.jpg"), "frame": _foto(pasta, f"{frame}_frame.jpg")}
    base = {
        "event_type": tipo, "session_id": sessao, "camera_id": camera_id, "frame_id": frame, "stable_state": "CHILD_ALONE",
        "reason": "child_without_adult", "child_confidence": 0.91, "evidence_paths": evidencias,
        "popup_emitido": tipo == "popup_alert",
    }
    base.update(extra)
    return base


def _com_camera(amb):
    _login(amb.client)
    assert _hx(amb.client, "POST", "/painel/cameras", data=_dados_intelbras(nome="Recepção")).status_code == 200


# ---------------------------------------------------------------- registro


def test_alerta_e_gravado_com_camera_confianca_e_evidencias(amb):
    _com_camera(amb)
    visao = amb.eventos.registrar(_payload(amb))
    assert visao.event_id == "sess_a_1" and visao.camera_nome == "Recepção" and visao.confianca == 0.91
    assert visao.crop_url == "/painel/evidence/sess_a_1/crop" and visao.annotated_url == "/painel/evidence/sess_a_1/annotated"
    with amb.database.session() as db:
        ev = db.scalar(select(EventoPediatria))
        assert ev.camera_id == 1 and ev.estado == "CHILD_ALONE" and ev.suprimido is False and ev.motivo == "child_without_adult"


def test_alerta_suprimido_e_gravado_mas_nao_vira_popup(amb):
    _com_camera(amb)
    amb.eventos.registrar(_payload(amb, frame=2, tipo="popup_suppressed"))
    assert amb.eventos.pendentes() == []
    assert [e.suprimido for e in amb.eventos.recentes()] == [True]


def test_registro_e_idempotente_por_event_id(amb):
    _com_camera(amb)
    assert amb.eventos.registrar(_payload(amb)) is not None
    assert amb.eventos.registrar(_payload(amb)) is None
    with amb.database.session() as db:
        assert len(db.scalars(select(EventoPediatria)).all()) == 1


def test_eventos_que_nao_sao_alerta_sao_ignorados_e_erros_nao_propagam(amb):
    _com_camera(amb)
    amb.eventos.on_runtime_event({"event_type": "frame_diagnostic", "session_id": "s", "frame_id": 1})
    amb.eventos.on_runtime_event({"event_type": "popup_alert", "evidence_paths": "isto-nao-e-um-dict"})   # payload corrompido
    assert amb.eventos.recentes() == []


def test_historico_sobrevive_a_remocao_da_camera(amb):
    _com_camera(amb)
    amb.eventos.registrar(_payload(amb))
    _hx(amb.client, "DELETE", "/painel/cameras/1")
    assert amb.eventos.recentes()[0].camera_nome == "Recepção"


# ---------------------------------------------------------------- runtime -> painel


def test_sessao_chama_o_ouvinte_a_cada_evento_e_sobrevive_a_ouvinte_quebrado():
    recebidos = []
    fake = SimpleNamespace(
        lock=threading.Lock(), state=SimpleNamespace(last_event_at=None), _record_summary=lambda p: None,
        _append_events_jsonl_line=lambda p: None, event_listener=recebidos.append,
    )
    PediatriaHeadlessSession._append_event(fake, {"event_type": "popup_alert", "timestamp": "t"})
    assert recebidos == [{"event_type": "popup_alert", "timestamp": "t"}]

    def quebrado(_p):
        raise RuntimeError("ouvinte ruim")

    fake.event_listener = quebrado
    PediatriaHeadlessSession._append_event(fake, {"event_type": "popup_alert"})   # nao levanta
    del fake.event_listener
    PediatriaHeadlessSession._append_event(fake, {"event_type": "popup_alert"})   # sem ouvinte tambem funciona


def test_gerente_distribui_eventos_isolando_ouvintes_com_falha():
    gerente, recebidos = PediatriaSessionManager(), []
    gerente.add_event_listener(lambda p: (_ for _ in ()).throw(RuntimeError("x")))
    gerente.add_event_listener(recebidos.append)
    gerente._dispatch_event({"event_type": "popup_alert"})
    assert recebidos == [{"event_type": "popup_alert"}]


def test_painel_se_inscreve_no_gerente_real(tmp_path, monkeypatch):
    monkeypatch.setenv("JUTTA_ADMIN_PASSWORD", SENHA_ADMIN)
    gerente = PediatriaSessionManager()
    app = FastAPI()
    database = mount_panel(app, db_url=f"sqlite:///{(tmp_path / 'p.db').as_posix()}", runtime_dir=tmp_path, manager=gerente,
                           config_factory=lambda **kw: SimpleNamespace(**kw), background=False, go2rtc_transport=httpx.MockTransport(FakeGo2rtc()))
    gerente._dispatch_event({"event_type": "popup_alert", "session_id": "s", "frame_id": 7, "camera_id": "cam_9", "stable_state": "CHILD_ALONE"})
    assert app.state.painel.eventos.recentes()[0].event_id == "s_7"
    database.dispose()


# ---------------------------------------------------------------- tempo real (barramento)


def test_popup_chega_ao_navegador_inscrito_vindo_de_outra_thread(amb):
    _com_camera(amb)

    async def cenario():
        inscricao = amb.eventos.inscrever(asyncio.get_running_loop())
        threading.Thread(target=amb.eventos.registrar, args=(_payload(amb),)).start()   # como a thread de inferencia
        mensagem = await asyncio.wait_for(inscricao.fila.get(), timeout=5)
        amb.eventos.cancelar(inscricao)
        return mensagem

    mensagem = asyncio.run(cenario())
    assert mensagem.nome == "popup" and mensagem.visao.event_id == "sess_a_1" and mensagem.id_evento >= 1
    assert amb.eventos.total_inscritos == 0


def test_alerta_suprimido_nao_e_publicado_aos_navegadores(amb):
    _com_camera(amb)

    async def cenario():
        inscricao = amb.eventos.inscrever(asyncio.get_running_loop())
        amb.eventos.registrar(_payload(amb, tipo="popup_suppressed"))
        await asyncio.sleep(0.1)
        return inscricao.fila.qsize()

    assert asyncio.run(cenario()) == 0


def test_ciencia_e_publicada_aos_outros_navegadores(amb):
    _com_camera(amb)
    amb.eventos.registrar(_payload(amb))

    async def cenario():
        inscricao = amb.eventos.inscrever(asyncio.get_running_loop())
        await asyncio.to_thread(amb.eventos.reconhecer, "sess_a_1", "maria")
        return await asyncio.wait_for(inscricao.fila.get(), timeout=5)

    mensagem = asyncio.run(cenario())
    assert mensagem.nome == "ack" and mensagem.visao == "sess_a_1"


def test_formato_sse_multilinha():
    assert formatar_sse("popup", "<div>\n  oi\n</div>", 42) == "id: 42\nevent: popup\ndata: <div>\ndata:   oi\ndata: </div>\n\n"
    assert formatar_sse("ack", "x") == "event: ack\ndata: x\n\n"


# ---------------------------------------------------------------- SSE via HTTP


def test_stream_reenvia_popups_perdidos_com_last_event_id(amb):
    _com_camera(amb)
    amb.eventos.registrar(_payload(amb, frame=1))
    amb.eventos.registrar(_payload(amb, frame=2))
    amb.eventos.registrar(_payload(amb, frame=3, tipo="popup_suppressed"))
    primeiro = amb.eventos.recentes()[-1]            # o mais antigo (frame 1)
    with amb.database.session() as db:
        id_primeiro = db.scalar(select(EventoPediatria.id).where(EventoPediatria.event_id == primeiro.event_id))
    resposta = amb.client.get("/painel/stream", headers={"Last-Event-ID": str(id_primeiro)})
    corpo = resposta.text
    assert resposta.headers["content-type"].startswith("text/event-stream")
    assert corpo.startswith("retry: 3000")
    assert "event: popup" in corpo and 'id="popup-sess_a_2"' in corpo
    assert 'id="popup-sess_a_1"' not in corpo          # o cliente ja tinha esse
    assert "sess_a_3" not in corpo                      # suprimido nunca vira popup
    assert corpo.count("event: popup") == 1


def test_stream_sem_last_event_id_nao_reenvia_historico(amb):
    _com_camera(amb)
    amb.eventos.registrar(_payload(amb))
    assert "event: popup" not in amb.client.get("/painel/stream").text


def test_stream_exige_login(amb):
    assert amb.client.get("/painel/stream").status_code == 303


# ---------------------------------------------------------------- ciencia (ack)


def test_ack_registra_quem_e_quando_e_audita(amb):
    _com_camera(amb)
    amb.eventos.registrar(_payload(amb))
    resposta = _hx(amb.client, "POST", "/painel/popups/sess_a_1/ack")
    assert resposta.status_code == 200 and resposta.text == ""
    with amb.database.session() as db:
        ev = db.scalar(select(EventoPediatria))
        assert ev.reconhecido_por == "admin" and ev.reconhecido_em is not None
        assert "alerta_reconhecido" in [a.acao for a in db.scalars(select(Auditoria)).all()]
    assert amb.eventos.pendentes() == []


def test_segundo_ack_nao_sobrescreve_o_primeiro(amb):
    _com_camera(amb)
    amb.eventos.registrar(_payload(amb))
    _hx(amb.client, "POST", "/painel/popups/sess_a_1/ack")
    with amb.database.session() as db:
        db.add(Usuario(login="operador", senha_hash=hash_senha("senha-comum-123"), papel=Papel.comum, ativo=True))
    amb.client.post("/logout", data={"csrf_token": _token(amb.client, "/painel/monitoramento")})
    _login(amb.client, "operador", "senha-comum-123")
    assert _hx(amb.client, "POST", "/painel/popups/sess_a_1/ack").status_code == 200
    with amb.database.session() as db:
        assert db.scalar(select(EventoPediatria)).reconhecido_por == "admin"
        assert [a.acao for a in db.scalars(select(Auditoria)).all()].count("alerta_reconhecido") == 1


def test_ack_de_alerta_inexistente_e_404_e_exige_csrf(amb):
    _login(amb.client)
    assert _hx(amb.client, "POST", "/painel/popups/nao_existe/ack").status_code == 404
    assert amb.client.post("/painel/popups/nao_existe/ack").status_code == 403


# ---------------------------------------------------------------- pagina: popups pendentes e recentes


def test_popup_pendente_reaparece_ao_recarregar_ate_dar_ciencia(amb):
    _com_camera(amb)
    amb.eventos.registrar(_payload(amb))
    html = amb.client.get("/painel/monitoramento").text
    assert 'id="popup-sess_a_1"' in html and "Criança desacompanhada" in html and "Recepção" in html
    assert '/painel/popups/sess_a_1/ack' in html and "91%" in html
    assert "/painel/evidence/sess_a_1/crop" in html
    assert 'id="popup-sess_a_1"' in amb.client.get("/painel/cameras").text          # tambem em outras paginas
    _hx(amb.client, "POST", "/painel/popups/sess_a_1/ack")
    assert 'id="popup-sess_a_1"' not in amb.client.get("/painel/monitoramento").text


def test_popup_antigo_sem_ciencia_nao_volta_a_aparecer(amb):
    _com_camera(amb)
    amb.eventos.registrar(_payload(amb))
    amb.eventos._relogio = lambda: datetime.now() + timedelta(hours=13)
    assert 'id="popup-sess_a_1"' not in amb.client.get("/painel/monitoramento").text


def test_fragmentos_htmx_nao_trazem_a_pilha_de_popups(amb):
    _com_camera(amb)
    amb.eventos.registrar(_payload(amb))
    assert "popup-sess_a_1" not in amb.client.get("/painel/cameras/1/ia-estado").text


def test_alertas_recentes_mostram_suprimidos_e_ciencia(amb):
    _com_camera(amb)
    amb.eventos.registrar(_payload(amb, frame=1))
    amb.eventos.registrar(_payload(amb, frame=2, tipo="popup_suppressed"))
    _hx(amb.client, "POST", "/painel/popups/sess_a_1/ack")
    html = amb.client.get("/painel/eventos/recentes").text
    assert "Recepção" in html and "suprimido" in html and "admin" in html and "Desacompanhada" in html


# ---------------------------------------------------------------- evidencias


def test_evidencia_e_servida_do_banco_e_so_dentro_das_pastas_permitidas(amb):
    _com_camera(amb)
    amb.eventos.registrar(_payload(amb))
    r = amb.client.get("/painel/evidence/sess_a_1/crop")
    assert r.status_code == 200 and r.headers["content-type"] == "image/jpeg" and r.content.startswith(b"\xff\xd8")
    assert "private" in r.headers["cache-control"]


def test_evidencia_fora_da_pasta_permitida_e_negada(amb):
    _com_camera(amb)
    segredo = Path(_foto(amb.tmp / "fora", "segredo.jpg"))
    amb.eventos.registrar(_payload(amb, com_fotos=False, evidence_paths={"crop": str(segredo)}))
    assert amb.client.get("/painel/evidence/sess_a_1/crop").status_code == 404


@pytest.mark.parametrize("caminho", ["/painel/evidence/nao_existe/crop", "/painel/evidence/sess_a_1/metadata", "/painel/evidence/sess_a_1/..%2F..%2Fx", "/painel/evidence/sess_a_1/crop.jpg"])
def test_evidencia_recusa_alvos_invalidos(amb, caminho):
    _com_camera(amb)
    amb.eventos.registrar(_payload(amb))
    assert amb.client.get(caminho).status_code == 404


def test_evidencia_com_extensao_nao_imagem_ou_traversal_no_banco_e_negada(amb):
    _com_camera(amb)
    texto = _foto(amb.evid / "sess_a", "notas.txt", b"x")
    amb.eventos.registrar(_payload(amb, com_fotos=False, evidence_paths={"crop": texto, "annotated": str(amb.evid / ".." / "painel.db")}))
    assert amb.client.get("/painel/evidence/sess_a_1/crop").status_code == 404
    assert amb.client.get("/painel/evidence/sess_a_1/annotated").status_code == 404


def test_evidencia_exige_login(amb):
    assert amb.client.get("/painel/evidence/x/crop").status_code == 303
