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
        assert con.execute(text("select version_num from alembic_version")).scalar() == "0004"
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


def _cfg():
    from alembic.config import Config

    cfg = Config()
    cfg.set_main_option("script_location", str(Path(__file__).parents[1] / "src/jutta_ped/painel/migracoes"))
    return cfg


def test_migracao_0004_cria_tabela_e_coluna_preservando_dados_e_destinos(tmp_path: Path) -> None:
    """Banco em 0003 com camera + destino + vinculo: a 0004 nao pode apagar os vinculos; downgrade tambem preserva."""
    from alembic import command

    eng = create_engine(f"sqlite:///{tmp_path / 'v3.db'}")
    cfg = _cfg()

    def passo(alvo: str, subir: bool = True) -> None:
        with eng.begin() as con:
            cfg.attributes["connection"] = con
            con.exec_driver_sql("PRAGMA foreign_keys=ON")
            (command.upgrade if subir else command.downgrade)(cfg, alvo)

    passo("0003")
    with eng.begin() as con:
        con.execute(text("insert into destinos (id, nome, tipo, ativo) values (1, 'D', 'webhook', 0)"))
        con.execute(text("insert into cameras (id, nome, fabricante, porta, canal, stream_tipo, ia_pediatria, go2rtc_sync, sombra_ativa, criado_em) "
                         "values (1, 'Cam', 'intelbras', 554, 1, 'principal', 1, 'ok', 1, '2026-01-01 10:00:00')"))
        con.execute(text("insert into camera_destinos (camera_id, destino_id) values (1, 1)"))
    passo("0004")
    with eng.begin() as con:
        con.exec_driver_sql("PRAGMA foreign_keys=ON")
        assert con.execute(text("select count(*) from camera_destinos")).scalar() == 1
        assert con.execute(text("select nome, sombra_ativa, diagnostico_ativo from cameras")).one() == ("Cam", 1, 0)
        con.execute(text("insert into camera_config_versoes (camera_id, versao, ajustes_json, config_hash, criado_em) "
                         "values (1, 1, '{}', 'sha256:x', '2026-01-01 10:00:00')"))
        assert {"camera_id", "versao", "especialista_id", "receita_versao_id", "ajustes_json", "config_hash", "criado_por", "criado_em"} <= {
            c["name"] for c in inspect(con).get_columns("camera_config_versoes")}
    passo("0003", subir=False)
    with eng.connect() as con:
        assert con.execute(text("select count(*) from camera_destinos")).scalar() == 1
        assert con.execute(text("select nome, sombra_ativa from cameras")).one() == ("Cam", 1)
        assert "camera_config_versoes" not in inspect(con).get_table_names()
        assert "diagnostico_ativo" not in {c["name"] for c in inspect(con).get_columns("cameras")}
    passo("head")                                           # sobe de novo sem erro
    eng.dispose()
