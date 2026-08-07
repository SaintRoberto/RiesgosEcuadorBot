"""Recrea respuestas de barrido con columnas ordenadas."""

from collections.abc import Callable, Sequence

from alembic import op

revision: str = "20260807_0008"
down_revision: str | None = "20260807_0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _crear_tabla_respuestas() -> None:
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
            provincia character varying(150),
            canton character varying(150),
            parroquia character varying(150),
            descripcion text,
            personas_en_riesgo boolean NOT NULL DEFAULT false,
            cantidad_personas_riesgo integer NOT NULL DEFAULT 0,
            foto_file_id text,
            foto_file_unique_id text,
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


def _crear_tabla_respuestas_orden_anterior() -> None:
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


def _copiar_respuestas_desde_backup() -> None:
    op.execute(
        """
        INSERT INTO public.telegram_barrido_respuestas
            (
                id,
                barrido_id,
                contacto_id,
                alerta_encuesta_id,
                latitud,
                longitud,
                provincia,
                canton,
                parroquia,
                descripcion,
                personas_en_riesgo,
                cantidad_personas_riesgo,
                foto_file_id,
                foto_file_unique_id,
                fecha_respuesta,
                activo,
                fecha_creacion
            )
        OVERRIDING SYSTEM VALUE
        SELECT
            id,
            barrido_id,
            contacto_id,
            alerta_encuesta_id,
            latitud,
            longitud,
            provincia,
            canton,
            parroquia,
            descripcion,
            personas_en_riesgo,
            cantidad_personas_riesgo,
            foto_file_id,
            foto_file_unique_id,
            fecha_respuesta,
            activo,
            fecha_creacion
        FROM public.telegram_barrido_respuestas_backup_20260807_0008
        """
    )
    op.execute(
        """
        SELECT setval(
            pg_get_serial_sequence('public.telegram_barrido_respuestas', 'id'),
            COALESCE((SELECT max(id) FROM public.telegram_barrido_respuestas), 1),
            (SELECT count(*) > 0 FROM public.telegram_barrido_respuestas)
        )
        """
    )


def _crear_backup_temporal() -> None:
    op.execute("DROP TABLE IF EXISTS public.telegram_barrido_respuestas_backup_20260807_0008")
    op.execute(
        """
        DO $$
        BEGIN
            IF to_regclass('public.telegram_barrido_respuestas') IS NOT NULL THEN
                EXECUTE '
                    CREATE TABLE public.telegram_barrido_respuestas_backup_20260807_0008 AS
                    SELECT * FROM public.telegram_barrido_respuestas
                ';
            ELSIF to_regclass('public.telegram_barrido_respuestas_backup') IS NOT NULL THEN
                EXECUTE '
                    CREATE TABLE public.telegram_barrido_respuestas_backup_20260807_0008 AS
                    SELECT * FROM public.telegram_barrido_respuestas_backup
                ';
            ELSE
                EXECUTE '
                    CREATE TABLE public.telegram_barrido_respuestas_backup_20260807_0008 AS
                    SELECT
                        NULL::bigint AS id,
                        NULL::bigint AS barrido_id,
                        NULL::bigint AS contacto_id,
                        NULL::bigint AS alerta_encuesta_id,
                        NULL::numeric(10,7) AS latitud,
                        NULL::numeric(10,7) AS longitud,
                        NULL::character varying(150) AS provincia,
                        NULL::character varying(150) AS canton,
                        NULL::character varying(150) AS parroquia,
                        NULL::text AS descripcion,
                        NULL::boolean AS personas_en_riesgo,
                        NULL::integer AS cantidad_personas_riesgo,
                        NULL::text AS foto_file_id,
                        NULL::text AS foto_file_unique_id,
                        NULL::timestamp with time zone AS fecha_respuesta,
                        NULL::boolean AS activo,
                        NULL::timestamp with time zone AS fecha_creacion
                    WHERE false
                ';
            END IF;
        END $$;
        """
    )


def _recrear_preservando_datos(crear_tabla: Callable[[], None]) -> None:
    _crear_backup_temporal()
    op.execute("DROP TABLE IF EXISTS public.telegram_barrido_respuestas")
    crear_tabla()
    _copiar_respuestas_desde_backup()
    op.execute("DROP TABLE IF EXISTS public.telegram_barrido_respuestas_backup_20260807_0008")


def upgrade() -> None:
    _recrear_preservando_datos(_crear_tabla_respuestas)


def downgrade() -> None:
    _recrear_preservando_datos(_crear_tabla_respuestas_orden_anterior)
