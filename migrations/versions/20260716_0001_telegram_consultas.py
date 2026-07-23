"""Crea el historial de consultas de Telegram.

Revision ID: 20260716_0001
Revises:
Create Date: 2026-07-16
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "20260716_0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "telegram_consultas",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("contacto_id", sa.BigInteger(), nullable=False),
        sa.Column("usuario_id", sa.BigInteger(), nullable=True),
        sa.Column("tipo_consulta", sa.String(length=50), nullable=False),
        sa.Column("codigo", sa.String(length=150), nullable=True),
        sa.Column("consulta", sa.Text(), nullable=True),
        sa.Column(
            "parametros",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("respuesta", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "estado",
            sa.String(length=20),
            server_default=sa.text("'PENDIENTE'"),
            nullable=False,
        ),
        sa.Column("mensaje_error", sa.Text(), nullable=True),
        sa.Column(
            "fecha_consulta",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("fecha_respuesta", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "estado IN ('PENDIENTE', 'PROCESANDO', 'COMPLETADA', 'ERROR')",
            name="chk_telegram_consultas_estado",
        ),
        sa.ForeignKeyConstraint(
            ["contacto_id"],
            ["telegram_contactos.id"],
            name="fk_telegram_consultas_contacto",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_telegram_consultas_contacto", "telegram_consultas", ["contacto_id"])
    op.create_index("idx_telegram_consultas_codigo", "telegram_consultas", ["codigo"])
    op.create_index("idx_telegram_consultas_fecha", "telegram_consultas", ["fecha_consulta"])
    op.create_index(
        "idx_telegram_consultas_parametros",
        "telegram_consultas",
        ["parametros"],
        postgresql_using="gin",
    )


def downgrade() -> None:
    op.drop_index("idx_telegram_consultas_parametros", table_name="telegram_consultas")
    op.drop_index("idx_telegram_consultas_fecha", table_name="telegram_consultas")
    op.drop_index("idx_telegram_consultas_codigo", table_name="telegram_consultas")
    op.drop_index("idx_telegram_consultas_contacto", table_name="telegram_consultas")
    op.drop_table("telegram_consultas")

