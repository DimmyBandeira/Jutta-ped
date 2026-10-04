"""alertas genericos (plugin/tipo/severidade/status/receita) e segunda fonte (termica) da camera

Revision ID: 0002
Revises: 0001
"""
from alembic import op
import sqlalchemy as sa

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("eventos_pediatria") as b:
        b.add_column(sa.Column("plugin", sa.String(32), nullable=False, server_default="pediatria"))
        b.add_column(sa.Column("tipo", sa.String(48), nullable=False, server_default="crianca_desacompanhada"))
        b.add_column(sa.Column("severidade", sa.String(16), nullable=False, server_default="alta"))
        b.add_column(sa.Column("status", sa.String(16), nullable=False, server_default="novo"))
        b.add_column(sa.Column("receita_id", sa.Integer(), nullable=True))
        b.add_column(sa.Column("receita_versao", sa.Integer(), nullable=True))
        b.create_index("ix_eventos_pediatria_status", ["status"])
    # Alertas ja reconhecidos antes desta revisao entram como 'reconhecido'.
    op.execute("UPDATE eventos_pediatria SET status = 'reconhecido' WHERE reconhecido_em IS NOT NULL")
    with op.batch_alter_table("cameras") as b:
        b.add_column(sa.Column("termica_canal", sa.Integer(), nullable=True))
        b.add_column(sa.Column("termica_rtsp_url_enc", sa.Text(), nullable=True))
        b.add_column(sa.Column("termica_stream_name", sa.String(80), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("cameras") as b:
        b.drop_column("termica_stream_name")
        b.drop_column("termica_rtsp_url_enc")
        b.drop_column("termica_canal")
    with op.batch_alter_table("eventos_pediatria") as b:
        b.drop_index("ix_eventos_pediatria_status")
        for c in ("receita_versao", "receita_id", "status", "severidade", "tipo", "plugin"):
            b.drop_column(c)
