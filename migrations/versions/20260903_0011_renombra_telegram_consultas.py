"""Renombra telegram_consultas a telegram_interacciones sin perder datos.

Revision ID: 20260903_0011
Revises: 20260825_0010
Create Date: 2026-09-03
"""
from collections.abc import Sequence

from alembic import op

revision: str = "20260903_0011"
down_revision: str | None = "20260825_0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.rename_table("telegram_consultas", "telegram_interacciones")
    op.execute(
        "ALTER TABLE telegram_interacciones "
        "RENAME CONSTRAINT telegram_consultas_pkey TO telegram_interacciones_pkey"
    )
    op.execute(
        "ALTER TABLE telegram_interacciones "
        "RENAME CONSTRAINT chk_telegram_consultas_estado TO chk_telegram_interacciones_estado"
    )
    op.execute(
        "ALTER TABLE telegram_interacciones "
        "RENAME CONSTRAINT fk_telegram_consultas_contacto TO fk_telegram_interacciones_contacto"
    )
    op.execute(
        "ALTER INDEX idx_telegram_consultas_contacto RENAME TO idx_telegram_interacciones_contacto"
    )
    op.execute(
        "ALTER INDEX idx_telegram_consultas_codigo RENAME TO idx_telegram_interacciones_codigo"
    )
    op.execute(
        "ALTER INDEX idx_telegram_consultas_fecha RENAME TO idx_telegram_interacciones_fecha"
    )
    op.execute(
        "ALTER INDEX idx_telegram_consultas_parametros RENAME TO idx_telegram_interacciones_parametros"
    )


def downgrade() -> None:
    op.rename_table("telegram_interacciones", "telegram_consultas")
    op.execute(
        "ALTER TABLE telegram_consultas "
        "RENAME CONSTRAINT telegram_interacciones_pkey TO telegram_consultas_pkey"
    )
    op.execute(
        "ALTER TABLE telegram_consultas "
        "RENAME CONSTRAINT chk_telegram_interacciones_estado TO chk_telegram_consultas_estado"
    )
    op.execute(
        "ALTER TABLE telegram_consultas "
        "RENAME CONSTRAINT fk_telegram_interacciones_contacto TO fk_telegram_consultas_contacto"
    )
    op.execute(
        "ALTER INDEX idx_telegram_interacciones_contacto RENAME TO idx_telegram_consultas_contacto"
    )
    op.execute(
        "ALTER INDEX idx_telegram_interacciones_codigo RENAME TO idx_telegram_consultas_codigo"
    )
    op.execute(
        "ALTER INDEX idx_telegram_interacciones_fecha RENAME TO idx_telegram_consultas_fecha"
    )
    op.execute(
        "ALTER INDEX idx_telegram_interacciones_parametros RENAME TO idx_telegram_consultas_parametros"
    )
