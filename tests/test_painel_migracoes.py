from __future__ import annotations

from pathlib import Path

from sqlalchemy import create_engine, inspect, text

from src.jutta_ped.painel.db import Database


def _colunas(db: Database, tabela: str) -> set[str]:
    return {c["name"] for c in inspect(db.engine).get_columns(tabela)}


def test_banco_novo_chega_ao_esquema_atual(tmp_path: Path) -> None:
    db = Database(f"sqlite:///{tmp_path / 'novo.db'}")
    db.migrar()
    assert {"plugin", "tipo", "severidade", "status", "receita_id", "receita_versao"} <= _colunas(db, "eventos_pediatria")
    assert {"termica_canal", "termica_rtsp_url_enc", "termica_stream_name"} <= _colunas(db, "cameras")
    with db.engine.connect() as con:
        assert con.execute(text("select version_num from alembic_version")).scalar() == "0002"
    db.dispose()


def test_migrar_e_idempotente(tmp_path: Path) -> None:
    db = Database(f"sqlite:///{tmp_path / 'x.db'}")
    db.migrar()
    db.migrar()
    db.dispose()


def test_banco_legado_sem_alembic_e_preservado_e_migrado(tmp_path: Path) -> None:
    """Banco criado antes das migracoes (so a revisao base, sem alembic_version) mantem os dados."""
    url = f"sqlite:///{tmp_path / 'legado.db'}"
    from alembic import command
    from alembic.config import Config

    cfg = Config()
    cfg.set_main_option("script_location", str(Path(__file__).parents[1] / "src/jutta_ped/painel/migracoes"))
    eng = create_engine(url)
    with eng.begin() as con:
        cfg.attributes["connection"] = con
        command.upgrade(cfg, "0001")
        con.execute(text(
            "insert into eventos_pediatria (event_id, camera_nome, session_id, estado, suprimido, evidencias_json, criado_em, reconhecido_em)"
            " values ('e1','Cam','s','CHILD_ALONE',0,'{}','2026-01-01 10:00:00','2026-01-01 10:01:00'),"
            " ('e2','Cam','s','CHILD_ALONE',0,'{}','2026-01-01 10:02:00',null)"
        ))
        con.execute(text("drop table alembic_version"))
    eng.dispose()

    db = Database(url)
    db.migrar()
    with db.engine.connect() as con:
        linhas = dict(con.execute(text("select event_id, status from eventos_pediatria")).all())
        plugin = con.execute(text("select plugin from eventos_pediatria where event_id='e1'")).scalar()
    assert linhas == {"e1": "reconhecido", "e2": "novo"}
    assert plugin == "pediatria"
    db.dispose()
