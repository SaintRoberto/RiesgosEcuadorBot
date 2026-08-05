"""Elimina campos redundantes de telegram_eventos."""

from collections.abc import Sequence

from alembic import op

revision: str = "20260805_0005"
down_revision: str | None = "20260804_0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE public.telegram_eventos
            DROP COLUMN IF EXISTS nivel_alerta_nombre,
            DROP COLUMN IF EXISTS nivel_alerta_descripcion,
            DROP COLUMN IF EXISTS comunidad
        """
    )


def downgrade() -> None:
    op.execute(
        """
        ALTER TABLE public.telegram_eventos
            ADD COLUMN IF NOT EXISTS nivel_alerta_nombre varchar(150),
            ADD COLUMN IF NOT EXISTS nivel_alerta_descripcion varchar(150),
            ADD COLUMN IF NOT EXISTS comunidad varchar(200)
        """
    )
