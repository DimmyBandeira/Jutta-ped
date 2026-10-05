"""receitas versionadas (receitas, receita_versoes) e sombra por camera (receita_versao_id, sombra_ativa)

Revision ID: 0003
Revises: 0002
"""
from alembic import op
import sqlalchemy as sa

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "receitas",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("nome", sa.String(48), nullable=False),
        sa.Column("descricao", sa.String(300), nullable=False, server_default=""),
        sa.Column("criada_em", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_receitas_nome", "receitas", ["nome"], unique=True)
    op.create_table(
        "receita_versoes",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("receita_id", sa.Integer(), sa.ForeignKey("receitas.id", ondelete="CASCADE"), nullable=False),
        sa.Column("versao", sa.Integer(), nullable=False),
        sa.Column("definicao_json", sa.Text(), nullable=False),
        sa.Column("config_hash", sa.String(80), nullable=False),
        sa.Column("criado_por", sa.String(32), nullable=True),
        sa.Column("criado_em", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("receita_id", "versao", name="uq_receita_versao"),
    )
    op.create_index("ix_receita_versoes_receita_id", "receita_versoes", ["receita_id"])
    if op.get_bind().dialect.name == "sqlite":
        # ALTER ... ADD COLUMN direto: o batch recriaria `cameras` e, com foreign_keys=ON, o DROP TABLE apagaria
        # em cascata os `camera_destinos`. Coluna nova com default NULL/constante e permitida pelo SQLite.
        op.execute("ALTER TABLE cameras ADD COLUMN receita_versao_id INTEGER REFERENCES receita_versoes (id) ON DELETE SET NULL")
        op.execute("ALTER TABLE cameras ADD COLUMN sombra_ativa BOOLEAN NOT NULL DEFAULT 0")
    else:
        op.add_column("cameras", sa.Column("receita_versao_id", sa.Integer(), nullable=True))
        op.add_column("cameras", sa.Column("sombra_ativa", sa.Boolean(), nullable=False, server_default=sa.text("0")))
        op.create_foreign_key("fk_cameras_receita_versao", "cameras", "receita_versoes", ["receita_versao_id"], ["id"], ondelete="SET NULL")


def downgrade() -> None:
    sqlite = op.get_bind().dialect.name == "sqlite"
    if sqlite:   # o batch recria `cameras`: preserva os vinculos de destino que o DROP TABLE apagaria em cascata
        op.execute("CREATE TABLE _bkp_camera_destinos AS SELECT * FROM camera_destinos")
        with op.batch_alter_table("cameras") as b:
            b.drop_column("sombra_ativa")
            b.drop_column("receita_versao_id")
        op.execute("DELETE FROM camera_destinos")
        op.execute("INSERT INTO camera_destinos SELECT * FROM _bkp_camera_destinos")
        op.execute("DROP TABLE _bkp_camera_destinos")
    else:
        op.drop_constraint("fk_cameras_receita_versao", "cameras", type_="foreignkey")
        op.drop_column("cameras", "sombra_ativa")
        op.drop_column("cameras", "receita_versao_id")
    op.drop_index("ix_receita_versoes_receita_id", table_name="receita_versoes")
    op.drop_table("receita_versoes")
    op.drop_index("ix_receitas_nome", table_name="receitas")
    op.drop_table("receitas")
