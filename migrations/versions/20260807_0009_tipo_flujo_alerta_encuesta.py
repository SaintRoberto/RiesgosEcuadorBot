"""Agrega tipos de flujo para opciones de encuesta."""

from collections.abc import Sequence

from alembic import op

revision: str = "20260807_0009"
down_revision: str | None = "20260807_0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS public.tipo_flujo
        (
            id bigint NOT NULL GENERATED ALWAYS AS IDENTITY,
            codigo character varying(30) NOT NULL,
            descripcion character varying(100) NOT NULL,
            activo boolean NOT NULL DEFAULT true,
            fecha_creacion timestamp with time zone NOT NULL DEFAULT now(),
            CONSTRAINT tipo_flujo_pkey PRIMARY KEY (id),
            CONSTRAINT uq_tipo_flujo_codigo UNIQUE (codigo)
        )
        """
    )
    op.execute(
        """
        INSERT INTO public.tipo_flujo (codigo, descripcion)
        VALUES
            ('ALERTA', 'Reporte de alerta'),
            ('BARRIDO', 'Reporte de barrido'),
            ('AMBOS', 'Reporte de alerta y barrido')
        ON CONFLICT (codigo) DO UPDATE
        SET descripcion = EXCLUDED.descripcion,
            activo = true
        """
    )
    op.execute(
        """
        SELECT setval(
            pg_get_serial_sequence('public.tipo_flujo', 'id'),
            (SELECT max(id) FROM public.tipo_flujo),
            true
        )
        """
    )
    op.execute(
        """
        ALTER TABLE public.alerta_encuesta
            ADD COLUMN IF NOT EXISTS tipo_flujo_id bigint
        """
    )
    op.execute(
        """
        UPDATE public.alerta_encuesta ae
        SET tipo_flujo_id = tf.id
        FROM public.tipo_flujo tf
        WHERE tf.codigo = 'AMBOS'
          AND ae.tipo_flujo_id IS NULL
        """
    )
    op.execute(
        """
        ALTER TABLE public.alerta_encuesta
            ALTER COLUMN tipo_flujo_id SET NOT NULL
        """
    )
    op.execute("ALTER TABLE public.alerta_encuesta DROP CONSTRAINT IF EXISTS uq_alerta_encuesta_tipo_orden")
    op.execute("DROP INDEX IF EXISTS public.uq_alerta_encuesta_tipo_orden")
    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1
                FROM pg_constraint
                WHERE conname = 'fk_alerta_encuesta_tipo_flujo'
                  AND conrelid = 'public.alerta_encuesta'::regclass
            ) THEN
                ALTER TABLE public.alerta_encuesta
                    ADD CONSTRAINT fk_alerta_encuesta_tipo_flujo
                    FOREIGN KEY (tipo_flujo_id)
                    REFERENCES public.tipo_flujo (id)
                    ON UPDATE CASCADE
                    ON DELETE RESTRICT;
            END IF;
        END $$;
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_alerta_encuesta_tipo_flujo
            ON public.alerta_encuesta (tipo_flujo_id)
        """
    )
    op.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS uq_alerta_encuesta_tipo_flujo_orden
            ON public.alerta_encuesta (tipo_alerta_id, tipo_flujo_id, orden)
        """
    )
    op.execute(
        """
        INSERT INTO public.alerta_encuesta
            (tipo_alerta_id, tipo_flujo_id, orden, nombre, descripcion, color)
        SELECT
            ta.id,
            tf.id,
            4,
            'LLUVIA DEBIL',
            'Precipitacion ligera sin acumulacion relevante.',
            '🟢'
        FROM public.tipo_alertas ta
        CROSS JOIN public.tipo_flujo tf
        WHERE ta.descripcion = 'LLUVIAS'
          AND tf.codigo = 'BARRIDO'
        ON CONFLICT (tipo_alerta_id, tipo_flujo_id, orden) DO UPDATE
        SET nombre = EXCLUDED.nombre,
            descripcion = EXCLUDED.descripcion,
            color = EXCLUDED.color,
            activo = true
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS public.uq_alerta_encuesta_tipo_flujo_orden")
    op.execute("DROP INDEX IF EXISTS public.idx_alerta_encuesta_tipo_flujo")
    op.execute(
        """
        ALTER TABLE public.alerta_encuesta
            DROP CONSTRAINT IF EXISTS fk_alerta_encuesta_tipo_flujo,
            DROP COLUMN IF EXISTS tipo_flujo_id
        """
    )
    op.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS uq_alerta_encuesta_tipo_orden
            ON public.alerta_encuesta (tipo_alerta_id, orden)
        """
    )
    op.execute("DROP TABLE IF EXISTS public.tipo_flujo")
