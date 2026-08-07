"""Reestructura barridos en cabecera y respuestas."""

from collections.abc import Sequence

from alembic import op

revision: str = "20260806_0006"
down_revision: str | None = "20260805_0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("DROP TABLE IF EXISTS public.telegram_barrido_respuestas")
    op.execute("DROP TABLE IF EXISTS public.telegram_barridos")
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS public.telegram_barridos
        (
            id bigint NOT NULL GENERATED ALWAYS AS IDENTITY,
            tipo_alerta_id bigint NOT NULL,
            codigo character varying(150),
            mensaje text,
            fecha_barrido timestamp with time zone NOT NULL DEFAULT now(),
            activo boolean NOT NULL DEFAULT true,
            fecha_creacion timestamp with time zone NOT NULL DEFAULT now(),
            CONSTRAINT telegram_barridos_pkey PRIMARY KEY (id),
            CONSTRAINT fk_telegram_barridos_tipo_alerta
                FOREIGN KEY (tipo_alerta_id)
                REFERENCES public.tipo_alertas (id)
                ON UPDATE CASCADE
                ON DELETE RESTRICT
        )
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS public.telegram_barrido_respuestas
        (
            id bigint NOT NULL GENERATED ALWAYS AS IDENTITY,
            barrido_id bigint NOT NULL,
            contacto_id bigint NOT NULL,
            alerta_encuesta_id bigint NOT NULL,
            latitud numeric(10,7) NOT NULL,
            longitud numeric(10,7) NOT NULL,
            descripcion text,
            personas_en_riesgo boolean NOT NULL DEFAULT false,
            cantidad_personas_riesgo integer NOT NULL DEFAULT 0,
            foto_file_id text,
            foto_file_unique_id text,
            provincia character varying(150),
            canton character varying(150),
            parroquia character varying(150),
            fecha_respuesta timestamp with time zone NOT NULL DEFAULT now(),
            activo boolean NOT NULL DEFAULT true,
            fecha_creacion timestamp with time zone NOT NULL DEFAULT now(),
            CONSTRAINT telegram_barrido_respuestas_pkey PRIMARY KEY (id),
            CONSTRAINT fk_telegram_barrido_respuestas_barrido
                FOREIGN KEY (barrido_id)
                REFERENCES public.telegram_barridos (id)
                ON UPDATE CASCADE
                ON DELETE RESTRICT,
            CONSTRAINT fk_telegram_barrido_respuestas_contacto
                FOREIGN KEY (contacto_id)
                REFERENCES public.telegram_contactos (id)
                ON UPDATE CASCADE
                ON DELETE RESTRICT,
            CONSTRAINT fk_telegram_barrido_respuestas_alerta_encuesta
                FOREIGN KEY (alerta_encuesta_id)
                REFERENCES public.alerta_encuesta (id)
                ON UPDATE CASCADE
                ON DELETE RESTRICT,
            CONSTRAINT chk_telegram_barrido_respuestas_latitud
                CHECK (latitud >= -90 AND latitud <= 90),
            CONSTRAINT chk_telegram_barrido_respuestas_longitud
                CHECK (longitud >= -180 AND longitud <= 180),
            CONSTRAINT uq_telegram_barrido_respuesta_contacto
                UNIQUE (barrido_id, contacto_id)
        )
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS idx_telegram_barridos_tipo_alerta ON public.telegram_barridos (tipo_alerta_id)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_telegram_barridos_fecha ON public.telegram_barridos (fecha_barrido DESC)")
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_telegram_barrido_respuestas_barrido "
        "ON public.telegram_barrido_respuestas (barrido_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_telegram_barrido_respuestas_alerta_encuesta "
        "ON public.telegram_barrido_respuestas (alerta_encuesta_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_telegram_barrido_respuestas_contacto "
        "ON public.telegram_barrido_respuestas (contacto_id)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS public.telegram_barrido_respuestas")
    op.execute("DROP TABLE IF EXISTS public.telegram_barridos")
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS public.telegram_barridos
        (
            id bigint NOT NULL GENERATED ALWAYS AS IDENTITY,
            contacto_id bigint NOT NULL,
            nivel_id bigint NOT NULL,
            latitud numeric(10,7) NOT NULL,
            longitud numeric(10,7) NOT NULL,
            fecha_barrido timestamp with time zone NOT NULL DEFAULT now(),
            activo boolean NOT NULL DEFAULT true,
            fecha_creacion timestamp with time zone NOT NULL DEFAULT now(),
            CONSTRAINT telegram_barridos_pkey PRIMARY KEY (id),
            CONSTRAINT fk_telegram_barridos_contacto
                FOREIGN KEY (contacto_id)
                REFERENCES public.telegram_contactos (id)
                ON UPDATE CASCADE
                ON DELETE RESTRICT,
            CONSTRAINT fk_telegram_barridos_nivel
                FOREIGN KEY (nivel_id)
                REFERENCES public.catalogo_niveles_evento (id)
                ON UPDATE CASCADE
                ON DELETE RESTRICT,
            CONSTRAINT chk_telegram_barridos_latitud
                CHECK (latitud >= -90 AND latitud <= 90),
            CONSTRAINT chk_telegram_barridos_longitud
                CHECK (longitud >= -180 AND longitud <= 180)
        )
        """
    )
