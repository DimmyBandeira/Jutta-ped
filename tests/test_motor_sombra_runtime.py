"""Ponto de captura do runtime -> motor em sombra. Servico de IA e captura sao dubles; motor, executor, instalador e pacotes sao reais."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import numpy as np
from test_especialistas_executor import BackendFalso, fabrica, instalar_ativar, manifesto_bebe, manifesto_cor
from test_motor_sombra import BEBE, COR, SIM_AZUL, receita
from test_pediatria_service_runtime_reconnect import ScriptedCapture, make_bare_session

from src.jutta_ped.especialistas.instalador import Instalador
from src.jutta_ped.motor.adaptador import observacoes_de_deteccoes, proxima_geracao
from src.jutta_ped.motor.motor import MotorSombra, RastroJsonl
from src.jutta_ped.service.runtime import SessionStartConfig


def det(track, x, y, h=200, w=80, conf=0.9):
    return SimpleNamespace(bbox_xyxy=(x, y, x + w, y + h), track_id=track, confidence=conf, role="adult")


class ServicoFalso:
    def __init__(self, deteccoes_por_quadro):
        self.deteccoes, self.n = deteccoes_por_quadro, 0

    def process_frame(self, frame, *, frame_id):
        d = self.deteccoes[min(self.n, len(self.deteccoes) - 1)]
        self.n += 1
        return SimpleNamespace(latency_ms=1.0, state=SimpleNamespace(stable_state="NO_CHILD", children=[]), resolved_detections=[], person_detections=d)


def motor_real(tmp_path: Path, **kw) -> MotorSombra:
    inst = Instalador(tmp_path / "raiz")
    instalar_ativar(inst, tmp_path, manifesto_bebe(), b"pesos-bebe")
    instalar_ativar(inst, tmp_path, manifesto_cor(), b"pesos-cor", "modelos/cor.pt")
    m = MotorSombra(fabrica(inst, BackendFalso(tipo_por_arquivo={"modelos/cor.pt": "classificacao"})), habilitado=True, **kw)
    m.configurar([receita()], {"test_camera": {"receita": "r.teste", "versao": "1"}})
    return m


def quadro_com(x, y, bgr):
    q = np.zeros((480, 640, 3), np.uint8)
    q[y, x] = bgr
    return q


def test_adaptador_converte_e_descarta_deteccoes_sem_trilha_ou_malformadas() -> None:
    ds = [det(1, 10, 10), SimpleNamespace(bbox_xyxy=(0, 0, 5, 5), track_id=None), SimpleNamespace(bbox_xyxy=None, track_id=2),
          SimpleNamespace(bbox_xyxy=("a", 0, 5, 5), track_id=3), SimpleNamespace(bbox_xyxy=(0, 0, 5, 5), track_id=True)]
    o = observacoes_de_deteccoes("c", 7, 42, 1.5, ds)
    assert len(o) == 1 and (o[0].camera_ref, o[0].geracao, o[0].quadro_id, o[0].trilha_id, o[0].classe, o[0].capturado_em) == ("c", 7, 42, 1, "person", 1.5)
    assert proxima_geracao() < proxima_geracao()


def test_laco_real_da_sessao_alimenta_o_motor_sem_tocar_nos_eventos_operacionais(tmp_path: Path) -> None:
    rastro = tmp_path / "sombra.jsonl"
    motor = motor_real(tmp_path, destino=RastroJsonl(rastro))
    cfg = SessionStartConfig(source=str(tmp_path / "v.mp4"), report_dir=tmp_path, motor_sombra=motor)
    s = make_bare_session(cfg, tmp_path)
    s._geracao_sombra = proxima_geracao()
    s.service = ServicoFalso([[det(5, 50, 60)], [det(5, 50, 60), det(6, 300, 100)]])
    s.capture = ScriptedCapture([(True, quadro_com(50, 60, SIM_AZUL)), (True, quadro_com(50, 60, SIM_AZUL)), (False, None)])
    s._run_loop()
    linhas = [__import__("json").loads(x) for x in rastro.read_text(encoding="utf-8").splitlines()]
    assert [(x["quadro_id"], x["trilha_id"]) for x in linhas] == [(1, 5), (2, 5), (2, 6)]
    assert linhas[0]["veredito"] == "sim" and linhas[0]["camera_ref"] == "test_camera" and linhas[0]["geracao"] == s._geracao_sombra
    assert all(x["alerta_real"] is False for x in linhas)
    # idade medida desde a captura no laco (relogio monotonico real): pequena e nao negativa
    assert 0 <= linhas[0]["condicoes"][0]["idade_ms"] < 2000
    assert not (tmp_path / "evidence" / "events.jsonl").exists() or "sombra" not in (tmp_path / "evidence" / "events.jsonl").read_text(encoding="utf-8")
    assert s.state.popup_count == 0


def test_falha_do_motor_nao_afeta_a_sessao(tmp_path: Path) -> None:
    class Quebrado:
        def processar(self, *a, **k):
            raise RuntimeError("boom")

    cfg = SessionStartConfig(source=str(tmp_path / "v.mp4"), report_dir=tmp_path, motor_sombra=Quebrado())
    s = make_bare_session(cfg, tmp_path)
    s.service = ServicoFalso([[det(1, 50, 60)]])
    s.capture = ScriptedCapture([(True, quadro_com(50, 60, SIM_AZUL)), (True, quadro_com(50, 60, SIM_AZUL)), (False, None)])
    s._run_loop()
    assert s.state.frames_processed == 2 and s.evidence_error_counts["motor_sombra"] == 2 and s.state.stop_reason == "eof"


def test_sem_motor_configurado_o_laco_segue_como_antes(tmp_path: Path) -> None:
    cfg = SessionStartConfig(source=str(tmp_path / "v.mp4"), report_dir=tmp_path)
    assert cfg.motor_sombra is None
    s = make_bare_session(cfg, tmp_path)
    s.service = ServicoFalso([[det(1, 50, 60)]])
    s.capture = ScriptedCapture([(True, quadro_com(50, 60, SIM_AZUL)), (False, None)])
    s._run_loop()
    assert s.state.frames_processed == 1 and s.evidence_error_counts["motor_sombra"] == 0
