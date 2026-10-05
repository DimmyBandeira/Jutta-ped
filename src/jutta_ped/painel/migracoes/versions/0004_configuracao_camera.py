"""configuracao de analise por camera (camera_config_versoes) e diagnostico do mestre (cameras.diagnostico_ativo)

Revision ID: 0004
Revises: 0003
"""
from alembic import op
import sqlalchemy as sa

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "camera_config_versoes",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("camera_id", sa.Integer(), sa.ForeignKey("cameras.id", ondelete="CASCADE"), nullable=False),
        sa.Column("versao", sa.Integer(), nullable=False),
        sa.Column("especialista_id", sa.String(64), nullable=True),
        sa.Column("receita_versao_id", sa.Integer(), sa.ForeignKey("receita_versoes.id", ondelete="SET NULL"), nullable=True),
        sa.Column("ajustes_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("config_hash", sa.String(80), nullable=False),
        sa.Column("criado_por", sa.String(32), nullable=True),
        sa.Column("criado_em", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("camera_id", "versao", name="uq_camera_config_versao"),
    )
    op.create_index("ix_camera_config_versoes_camera_id", "camera_config_versoes", ["camera_id"])
    if op.get_bind().dialect.name == "sqlite":
        # ALTER ... ADD COLUMN direto (como na 0003): o batch recriaria `cameras` e o DROP TABLE apagaria `camera_destinos` em cascata.
        op.execute("ALTER TABLE cameras ADD COLUMN diagnostico_ativo BOOLEAN NOT NULL DEFAULT 0")
    else:
        op.add_column("cameras", sa.Column("diagnostico_ativo", sa.Boolean(), nullable=False, server_default=sa.text("0")))


def downgrade() -> None:
    sqlite = op.get_bind().dialect.name == "sqlite"
    op.drop_index("ix_camera_config_versoes_camera_id", table_name="camera_config_versoes")
    op.drop_table("camera_config_versoes")
    if sqlite:   # o batch recria `cameras`: preserva os vinculos de destino que o DROP TABLE apagaria em cascata
        op.execute("CREATE TABLE _bkp_camera_destinos AS SELECT * FROM camera_destinos")
        with op.batch_alter_table("cameras") as b:
            b.drop_column("diagnostico_ativo")
        op.execute("DELETE FROM camera_destinos")
        op.execute("INSERT INTO camera_destinos SELECT * FROM _bkp_camera_destinos")
        op.execute("DROP TABLE _bkp_camera_destinos")
    else:
        op.drop_column("cameras", "diagnostico_ativo")
