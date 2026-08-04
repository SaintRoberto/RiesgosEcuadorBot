"""Agrega campos del formulario de alerta a telegram_eventos."""

from collections.abc import Sequence

from alembic import op

revision: str = "20260804_0004"
down_revision: str | None = "20260804_0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE public.telegram_eventos
            ADD COLUMN IF NOT EXISTS tipo_alerta_id bigint,
            ADD COLUMN IF NOT EXISTS alerta_encuesta_id bigint,
            ADD COLUMN IF NOT EXISTS personas_en_riesgo boolean NOT NULL DEFAULT false,
            ADD COLUMN IF NOT EXISTS cantidad_personas_riesgo integer NOT NULL DEFAULT 0
        """
    )
    op.execute(
        """
        UPDATE public.telegram_eventos
        SET personas_en_riesgo = COALESCE(personas_en_riesgo, false),
            cantidad_personas_riesgo = COALESCE(cantidad_personas_riesgo, 0)
        """
    )
    op.execute(
        """
        ALTER TABLE public.telegram_eventos
            ALTER COLUMN personas_en_riesgo SET DEFAULT false,
            ALTER COLUMN personas_en_riesgo SET NOT NULL,
            ALTER COLUMN cantidad_personas_riesgo SET DEFAULT 0,
            ALTER COLUMN cantidad_personas_riesgo SET NOT NULL
        """
    )
    op.execute(
        """
        DO $$
        BEGIN
            IF to_regclass('public.tipo_alertas') IS NOT NULL
               AND NOT EXISTS (
                SELECT 1
                FROM pg_constraint
                WHERE conname = 'fk_telegram_eventos_tipo_alerta'
                  AND conrelid = 'public.telegram_eventos'::regclass
            ) THEN
                ALTER TABLE public.telegram_eventos
                    ADD CONSTRAINT fk_telegram_eventos_tipo_alerta
                    FOREIGN KEY (tipo_alerta_id)
                    REFERENCES public.tipo_alertas (id);
            END IF;

            IF to_regclass('public.alerta_encuesta') IS NOT NULL
               AND NOT EXISTS (
                SELECT 1
                FROM pg_constraint
                WHERE conname = 'fk_telegram_eventos_alerta_encuesta'
                  AND conrelid = 'public.telegram_eventos'::regclass
            ) THEN
                ALTER TABLE public.telegram_eventos
                    ADD CONSTRAINT fk_telegram_eventos_alerta_encuesta
                    FOREIGN KEY (alerta_encuesta_id)
                    REFERENCES public.alerta_encuesta (id);
            END IF;

            IF NOT EXISTS (
                SELECT 1
                FROM pg_constraint
                WHERE conname = 'chk_telegram_eventos_cantidad_personas_riesgo'
                  AND conrelid = 'public.telegram_eventos'::regclass
            ) THEN
                ALTER TABLE public.telegram_eventos
                    ADD CONSTRAINT chk_telegram_eventos_cantidad_personas_riesgo
                    CHECK (cantidad_personas_riesgo >= 0 AND cantidad_personas_riesgo <= 999999);
            END IF;
        END
        $$;
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_telegram_eventos_tipo_alerta
            ON public.telegram_eventos (tipo_alerta_id)
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_telegram_eventos_alerta_encuesta
            ON public.telegram_eventos (alerta_encuesta_id)
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS public.idx_telegram_eventos_alerta_encuesta")
    op.execute("DROP INDEX IF EXISTS public.idx_telegram_eventos_tipo_alerta")
    op.execute(
        """
        ALTER TABLE public.telegram_eventos
            DROP CONSTRAINT IF EXISTS chk_telegram_eventos_cantidad_personas_riesgo,
            DROP CONSTRAINT IF EXISTS fk_telegram_eventos_alerta_encuesta,
            DROP CONSTRAINT IF EXISTS fk_telegram_eventos_tipo_alerta,
            DROP COLUMN IF EXISTS cantidad_personas_riesgo,
            DROP COLUMN IF EXISTS personas_en_riesgo,
            DROP COLUMN IF EXISTS alerta_encuesta_id,
            DROP COLUMN IF EXISTS tipo_alerta_id
        """
    )
