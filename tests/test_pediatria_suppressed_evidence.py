"""Frames suprimidos pelo cooldown nao podem gravar evidencia/evento a cada frame.

Achado do teste operacional (docs/testes_operacionais): 192 eventos e ~97 MB de imagens num unico
episodio. O contador `suppressed_count` continua por frame; evidencia e evento saem 1x por intervalo.
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from src.jutta_ped.service import runtime as runtime_module
from src.jutta_ped.service.runtime import SessionStartConfig, SimpleAlertLatch
from test_pediatria_service_runtime_reconnect import make_bare_session


class _ServicoComCrianca:
    def process_frame(self, frame, *, frame_id):
        return SimpleNamespace(
            latency_ms=1.0,
            state=SimpleNamespace(stable_state="CHILD_ALONE", children=[]),
            resolved_detections=[],
            person_detections=[],
        )


def _sessao(tmp_path: Path, monkeypatch, intervalo: float, cooldown: float = 3600.0):
    config = SessionStartConfig(source="rtsp://x/y", report_dir=tmp_path, cooldown_seconds=cooldown, suppressed_evidence_interval_seconds=intervalo)
    sessao = make_bare_session(config, tmp_path)
    sessao.service = _ServicoComCrianca()
    sessao.latch = SimpleAlertLatch(cooldown)
    sessao.gravados = []
    monkeypatch.setattr(runtime_module, "select_alert_child", lambda state, resolved: SimpleNamespace(track_id=7, confidence=0.9))
    monkeypatch.setattr(sessao, "_save_event_evidence", lambda tipo, *a, **k: (sessao.gravados.append(tipo) or ({}, [])))
    monkeypatch.setattr(sessao, "_event_payload", lambda tipo, *a, **k: {"event_type": tipo})
    return sessao


def _popups(sessao) -> list[str]:
    """Eventos de popup gravados (ignora o frame_diagnostic periodico, que e outro mecanismo)."""
    return [g for g in sessao.gravados if g.startswith("popup")]


def _frames(sessao, n: int) -> None:
    for _ in range(n):
        sessao._process_frame(np.zeros((8, 8, 3), dtype=np.uint8))


def test_suprimidos_gravam_no_maximo_um_evento_por_intervalo(tmp_path, monkeypatch):
    sessao = _sessao(tmp_path, monkeypatch, intervalo=10.0)
    relogio = [1000.0]
    monkeypatch.setattr(runtime_module.time, "monotonic", lambda: relogio[0])
    _frames(sessao, 100)                                   # 100 frames no mesmo instante
    assert _popups(sessao) == ["popup_alert", "popup_suppressed"]
    assert sessao.state.popup_count == 1 and sessao.state.suppressed_count == 99     # o contador segue por frame
    relogio[0] += 9.9
    _frames(sessao, 50)
    assert _popups(sessao).count("popup_suppressed") == 1  # ainda dentro do intervalo
    relogio[0] += 0.2
    _frames(sessao, 50)
    assert _popups(sessao).count("popup_suppressed") == 2  # passou 10 s: registra de novo
    assert sessao.state.suppressed_count == 99 + 100


def test_intervalo_zero_mantem_o_comportamento_antigo(tmp_path, monkeypatch):
    sessao = _sessao(tmp_path, monkeypatch, intervalo=0.0)
    _frames(sessao, 5)
    assert _popups(sessao) == ["popup_alert"] + ["popup_suppressed"] * 4


def test_novo_alerta_apos_o_cooldown_continua_gravando_evidencia(tmp_path, monkeypatch):
    sessao = _sessao(tmp_path, monkeypatch, intervalo=10.0, cooldown=60.0)
    relogio = [500.0]
    monkeypatch.setattr(runtime_module.time, "monotonic", lambda: relogio[0])
    _frames(sessao, 3)
    relogio[0] += 61
    _frames(sessao, 1)
    assert _popups(sessao).count("popup_alert") == 2        # o limite so vale para suprimidos; alertas reais nunca sao cortados
    assert sessao.state.popup_count == 2


def test_padrao_do_config_limita_a_10_segundos():
    assert SessionStartConfig(source="x").suppressed_evidence_interval_seconds == 10.0
