from __future__ import annotations

from pathlib import Path

from modulo.pediatria.config import CompanionshipConfig, CompanionshipTiming
from src.jutta_ped.service.runtime import PediatriaHeadlessSession, SessionStartConfig


def test_from_seconds_converte_pela_taxa_de_analise() -> None:
    c5 = CompanionshipConfig.from_seconds(5.0)
    assert (c5.relationship_confirmation_frames, c5.state_persistence_frames) == (5, 5)
    assert (c5.adult_occlusion_grace_frames, c5.unbound_child_alone_grace_frames) == (5, 15)
    c15 = CompanionshipConfig.from_seconds(15.0)
    assert c15.unbound_child_alone_grace_frames == 45          # referencia dos defaults antigos


def test_from_seconds_nunca_passa_de_um_frame_minimo() -> None:
    c = CompanionshipConfig.from_seconds(0.5, CompanionshipTiming(relationship_confirmation_seconds=0.1))
    assert c.relationship_confirmation_frames == 1


def _sessao(tmp_path: Path, source, **kw) -> PediatriaHeadlessSession:
    return PediatriaHeadlessSession(SessionStartConfig(source=source, report_dir=tmp_path, **kw))


def test_fonte_ao_vivo_usa_taxa_alvo_e_analisador_em_segundos(tmp_path: Path) -> None:
    s = _sessao(tmp_path, "rtsp://x/cam")
    assert s._analysis_fps == 5.0
    assert s.service.analyzer.config.unbound_child_alone_grace_frames == 15
    s.set_analysis_fps(3.0)
    assert s._analysis_fps == 3.0


def test_fonte_de_arquivo_nao_e_limitada_nem_muda_defaults(tmp_path: Path) -> None:
    s = _sessao(tmp_path, str(tmp_path / "v.mp4"))
    assert s._analysis_fps == 0.0
    assert s.service.analyzer.config.unbound_child_alone_grace_frames == 45
    s.set_analysis_fps(3.0)
    assert s._analysis_fps == 0.0
