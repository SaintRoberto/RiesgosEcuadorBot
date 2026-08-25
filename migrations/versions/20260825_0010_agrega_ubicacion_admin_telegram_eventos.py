"""Recrea telegram_eventos con ubicacion administrativa."""

from collections.abc import Sequence

from alembic import op

revision: str = "20260825_0010"
down_revision: str | None = "20260807_0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE public.telegram_eventos
            ADD COLUMN IF NOT EXISTS provincia character varying(150),
            ADD COLUMN IF NOT EXISTS canton character varying(150),
            ADD COLUMN IF NOT EXISTS parroquia character varying(150)
        """
    )
    op.execute("DROP TABLE IF EXISTS public.telegram_eventos_nueva")
    op.execute(
        """
        CREATE TABLE public.telegram_eventos_nueva
        (
            id bigint GENERATED ALWAYS AS IDENTITY,
            contacto_id bigint NOT NULL,
            tipo_alerta_id bigint,
            alerta_encuesta_id bigint,
            descripcion text NOT NULL,
            personas_en_riesgo boolean NOT NULL DEFAULT false,
            cantidad_personas_riesgo integer NOT NULL DEFAULT 0,
            latitud numeric(10,7) NOT NULL,
            longitud numeric(10,7) NOT NULL,
            provincia character varying(150),
            canton character varying(150),
            parroquia character varying(150),
            fecha_reporte timestamp with time zone NOT NULL DEFAULT now(),
            activo boolean NOT NULL DEFAULT true,
            fecha_creacion timestamp with time zone NOT NULL DEFAULT now(),
            foto_file_id text NOT NULL,
            foto_file_unique_id text
        )
        """
    )
    op.execute(
        """
        INSERT INTO public.telegram_eventos_nueva
            (
                id,
                contacto_id,
                tipo_alerta_id,
                alerta_encuesta_id,
                descripcion,
                personas_en_riesgo,
                cantidad_personas_riesgo,
                latitud,
                longitud,
                provincia,
                canton,
                parroquia,
                fecha_reporte,
                activo,
                fecha_creacion,
                foto_file_id,
                foto_file_unique_id
            )
        OVERRIDING SYSTEM VALUE
        SELECT
            id,
            contacto_id,
            tipo_alerta_id,
            alerta_encuesta_id,
            descripcion,
            COALESCE(personas_en_riesgo, false),
            COALESCE(cantidad_personas_riesgo, 0),
            latitud,
            longitud,
            provincia,
            canton,
            parroquia,
            fecha_reporte,
            COALESCE(activo, true),
            fecha_creacion,
            foto_file_id,
            foto_file_unique_id
        FROM public.telegram_eventos
        ORDER BY id
        """
    )
    op.execute("DROP TABLE public.telegram_eventos")
    op.execute("ALTER TABLE public.telegram_eventos_nueva RENAME TO telegram_eventos")
    op.execute(
        """
        ALTER TABLE public.telegram_eventos
            ADD CONSTRAINT telegram_eventos_pkey PRIMARY KEY (id),
            ADD CONSTRAINT fk_telegram_eventos_contacto
                FOREIGN KEY (contacto_id)
                REFERENCES public.telegram_contactos (id)
                ON UPDATE NO ACTION
                ON DELETE NO ACTION,
            ADD CONSTRAINT fk_telegram_eventos_tipo_alerta
                FOREIGN KEY (tipo_alerta_id)
                REFERENCES public.tipo_alertas (id),
            ADD CONSTRAINT fk_telegram_eventos_alerta_encuesta
                FOREIGN KEY (alerta_encuesta_id)
                REFERENCES public.alerta_encuesta (id),
            ADD CONSTRAINT chk_telegram_eventos_latitud
                CHECK (latitud >= -90 AND latitud <= 90),
            ADD CONSTRAINT chk_telegram_eventos_longitud
                CHECK (longitud >= -180 AND longitud <= 180),
            ADD CONSTRAINT chk_telegram_eventos_cantidad_personas_riesgo
                CHECK (cantidad_personas_riesgo >= 0 AND cantidad_personas_riesgo <= 999999)
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
    op.execute(
        """
        SELECT setval(
            pg_get_serial_sequence('public.telegram_eventos', 'id'),
            GREATEST((SELECT COALESCE(max(id), 1) FROM public.telegram_eventos), 1),
            (SELECT count(*) > 0 FROM public.telegram_eventos)
        )
        """
    )


def downgrade() -> None:
    op.execute(
        """
        ALTER TABLE public.telegram_eventos
            DROP COLUMN IF EXISTS parroquia,
            DROP COLUMN IF EXISTS canton,
            DROP COLUMN IF EXISTS provincia
        """
    )
