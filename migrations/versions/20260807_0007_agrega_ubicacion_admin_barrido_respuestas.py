"""Agrega ubicacion administrativa a respuestas de barrido."""

from collections.abc import Sequence

from alembic import op

revision: str = "20260807_0007"
down_revision: str | None = "20260806_0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE public.telegram_barrido_respuestas
            ADD COLUMN IF NOT EXISTS provincia character varying(150),
            ADD COLUMN IF NOT EXISTS canton character varying(150),
            ADD COLUMN IF NOT EXISTS parroquia character varying(150)
        """
    )


def downgrade() -> None:
    op.execute(
        """
        ALTER TABLE public.telegram_barrido_respuestas
            DROP COLUMN IF EXISTS parroquia,
            DROP COLUMN IF EXISTS canton,
            DROP COLUMN IF EXISTS provincia
        """
    )
