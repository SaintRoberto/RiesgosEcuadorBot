"""Recrea tablas de monitoreo con el orden definitivo de columnas.

Revision ID: 20260903_0013
Revises: 20260903_0012
Create Date: 2026-09-03
"""
from collections.abc import Sequence

from alembic import op

revision: str = "20260903_0013"
down_revision: str | None = "20260903_0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("DROP TABLE IF EXISTS public.tipo_monitoreo_alertas_nueva")
    op.execute("DROP TABLE IF EXISTS public.tipo_monitoreo_flujo_nueva")
    op.execute("DROP TABLE IF EXISTS public.monitoreo_opciones_nueva")
    op.execute("DROP TABLE IF EXISTS public.monitoreo_recomendaciones_nueva")
    op.execute("DROP TABLE IF EXISTS public.telegram_interacciones_nueva")

    op.execute(
        """
        CREATE TABLE public.tipo_monitoreo_alertas_nueva
        (
            id bigint GENERATED ALWAYS AS IDENTITY,
            descripcion character varying(150) NOT NULL,
            activo boolean NOT NULL DEFAULT true,
            fecha_creacion timestamp with time zone NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        """
        INSERT INTO public.tipo_monitoreo_alertas_nueva
            (id, descripcion, activo, fecha_creacion)
        OVERRIDING SYSTEM VALUE
        SELECT id, descripcion, activo, fecha_creacion
        FROM public.tipo_monitoreo_alertas
        ORDER BY id
        """
    )

    op.execute(
        """
        CREATE TABLE public.tipo_monitoreo_flujo_nueva
        (
            id bigint GENERATED ALWAYS AS IDENTITY,
            codigo character varying(30) NOT NULL,
            descripcion character varying(100) NOT NULL,
            activo boolean NOT NULL DEFAULT true,
            fecha_creacion timestamp with time zone NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        """
        INSERT INTO public.tipo_monitoreo_flujo_nueva
            (id, codigo, descripcion, activo, fecha_creacion)
        OVERRIDING SYSTEM VALUE
        SELECT id, codigo, descripcion, activo, fecha_creacion
        FROM public.tipo_monitoreo_flujo
        ORDER BY id
        """
    )

    op.execute(
        """
        CREATE TABLE public.monitoreo_opciones_nueva
        (
            id bigint GENERATED ALWAYS AS IDENTITY,
            tipo_monitoreo_alerta_id bigint NOT NULL,
            tipo_monitoreo_flujo_id bigint NOT NULL,
            nombre character varying(150) NOT NULL,
            descripcion text,
            color character varying(20),
            orden integer NOT NULL,
            activo boolean NOT NULL DEFAULT true,
            fecha_creacion timestamp with time zone NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        """
        INSERT INTO public.monitoreo_opciones_nueva
            (
                id,
                tipo_monitoreo_alerta_id,
                tipo_monitoreo_flujo_id,
                nombre,
                descripcion,
                color,
                orden,
                activo,
                fecha_creacion
            )
        OVERRIDING SYSTEM VALUE
        SELECT
            id,
            tipo_monitoreo_alerta_id,
            tipo_monitoreo_flujo_id,
            nombre,
            descripcion,
            color,
            orden,
            activo,
            fecha_creacion
        FROM public.monitoreo_opciones
        ORDER BY id
        """
    )

    op.execute(
        """
        CREATE TABLE public.monitoreo_recomendaciones_nueva
        (
            id bigint GENERATED ALWAYS AS IDENTITY,
            tipo_monitoreo_alerta_id bigint NOT NULL,
            recomendacion text NOT NULL,
            orden integer NOT NULL,
            activo boolean NOT NULL DEFAULT true,
            fecha_creacion timestamp with time zone NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        """
        INSERT INTO public.monitoreo_recomendaciones_nueva
            (id, tipo_monitoreo_alerta_id, recomendacion, orden, activo, fecha_creacion)
        OVERRIDING SYSTEM VALUE
        SELECT id, tipo_monitoreo_alerta_id, recomendacion, orden, activo, fecha_creacion
        FROM public.monitoreo_recomendaciones
        ORDER BY id
        """
    )

    op.execute(
        """
        CREATE TABLE public.telegram_interacciones_nueva
        (
            id bigint GENERATED ALWAYS AS IDENTITY,
            contacto_id bigint NOT NULL,
            usuario_id bigint,
            tipo_interaccion character varying(50) NOT NULL,
            codigo character varying(150),
            mensaje text,
            parametros jsonb NOT NULL DEFAULT '{}'::jsonb,
            respuesta jsonb,
            estado character varying(20) NOT NULL DEFAULT 'PENDIENTE',
            mensaje_error text,
            fecha_interaccion timestamp with time zone NOT NULL DEFAULT now(),
            fecha_respuesta timestamp with time zone
        )
        """
    )
    op.execute(
        """
        INSERT INTO public.telegram_interacciones_nueva
            (
                id,
                contacto_id,
                usuario_id,
                tipo_interaccion,
                codigo,
                mensaje,
                parametros,
                respuesta,
                estado,
                mensaje_error,
                fecha_interaccion,
                fecha_respuesta
            )
        OVERRIDING SYSTEM VALUE
        SELECT
            id,
            contacto_id,
            usuario_id,
            tipo_interaccion,
            codigo,
            mensaje,
            parametros,
            respuesta,
            estado,
            mensaje_error,
            fecha_interaccion,
            fecha_respuesta
        FROM public.telegram_interacciones
        ORDER BY id
        """
    )

    op.execute(
        "ALTER TABLE public.telegram_barridos "
        "DROP CONSTRAINT fk_telegram_barridos_tipo_monitoreo_alerta"
    )
    op.execute(
        "ALTER TABLE public.telegram_barrido_respuestas "
        "DROP CONSTRAINT fk_telegram_barrido_respuestas_monitoreo_opcion"
    )
    op.execute(
        "ALTER TABLE public.telegram_eventos "
        "DROP CONSTRAINT fk_telegram_eventos_tipo_monitoreo_alerta, "
        "DROP CONSTRAINT fk_telegram_eventos_monitoreo_opcion"
    )

    op.execute("DROP TABLE public.monitoreo_recomendaciones")
    op.execute("DROP TABLE public.monitoreo_opciones")
    op.execute("DROP TABLE public.tipo_monitoreo_flujo")
    op.execute("DROP TABLE public.tipo_monitoreo_alertas")
    op.execute("DROP TABLE public.telegram_interacciones")

    op.execute("ALTER TABLE public.tipo_monitoreo_alertas_nueva RENAME TO tipo_monitoreo_alertas")
    op.execute("ALTER TABLE public.tipo_monitoreo_flujo_nueva RENAME TO tipo_monitoreo_flujo")
    op.execute("ALTER TABLE public.monitoreo_opciones_nueva RENAME TO monitoreo_opciones")
    op.execute("ALTER TABLE public.monitoreo_recomendaciones_nueva RENAME TO monitoreo_recomendaciones")
    op.execute("ALTER TABLE public.telegram_interacciones_nueva RENAME TO telegram_interacciones")

    op.execute(
        "ALTER SEQUENCE public.tipo_monitoreo_alertas_nueva_id_seq "
        "RENAME TO tipo_monitoreo_alertas_id_seq"
    )
    op.execute(
        "ALTER SEQUENCE public.tipo_monitoreo_flujo_nueva_id_seq "
        "RENAME TO tipo_monitoreo_flujo_id_seq"
    )
    op.execute(
        "ALTER SEQUENCE public.monitoreo_opciones_nueva_id_seq "
        "RENAME TO monitoreo_opciones_id_seq"
    )
    op.execute(
        "ALTER SEQUENCE public.monitoreo_recomendaciones_nueva_id_seq "
        "RENAME TO monitoreo_recomendaciones_id_seq"
    )
    op.execute(
        "ALTER SEQUENCE public.telegram_interacciones_nueva_id_seq "
        "RENAME TO telegram_interacciones_id_seq"
    )

    op.execute(
        """
        ALTER TABLE public.tipo_monitoreo_alertas
            ADD CONSTRAINT tipo_monitoreo_alertas_pkey PRIMARY KEY (id),
            ADD CONSTRAINT uq_tipo_monitoreo_alertas_descripcion UNIQUE (descripcion)
        """
    )
    op.execute(
        """
        ALTER TABLE public.tipo_monitoreo_flujo
            ADD CONSTRAINT tipo_monitoreo_flujo_pkey PRIMARY KEY (id),
            ADD CONSTRAINT uq_tipo_monitoreo_flujo_codigo UNIQUE (codigo)
        """
    )
    op.execute(
        """
        ALTER TABLE public.monitoreo_opciones
            ADD CONSTRAINT monitoreo_opciones_pkey PRIMARY KEY (id),
            ADD CONSTRAINT fk_monitoreo_opciones_tipo_alerta
                FOREIGN KEY (tipo_monitoreo_alerta_id)
                REFERENCES public.tipo_monitoreo_alertas (id),
            ADD CONSTRAINT fk_monitoreo_opciones_tipo_flujo
                FOREIGN KEY (tipo_monitoreo_flujo_id)
                REFERENCES public.tipo_monitoreo_flujo (id)
                ON UPDATE CASCADE
                ON DELETE RESTRICT
        """
    )
    op.execute(
        """
        ALTER TABLE public.monitoreo_recomendaciones
            ADD CONSTRAINT monitoreo_recomendaciones_pkey PRIMARY KEY (id),
            ADD CONSTRAINT fk_monitoreo_recomendaciones_tipo_alerta
                FOREIGN KEY (tipo_monitoreo_alerta_id)
                REFERENCES public.tipo_monitoreo_alertas (id),
            ADD CONSTRAINT uq_monitoreo_recomendaciones_tipo_orden
                UNIQUE (tipo_monitoreo_alerta_id, orden)
        """
    )
    op.execute(
        """
        ALTER TABLE public.telegram_interacciones
            ADD CONSTRAINT telegram_interacciones_pkey PRIMARY KEY (id),
            ADD CONSTRAINT fk_telegram_interacciones_contacto
                FOREIGN KEY (contacto_id)
                REFERENCES public.telegram_contactos (id),
            ADD CONSTRAINT chk_telegram_interacciones_estado
                CHECK (estado IN ('PENDIENTE', 'PROCESANDO', 'COMPLETADA', 'ERROR'))
        """
    )

    op.execute(
        """
        ALTER TABLE public.telegram_barridos
            ADD CONSTRAINT fk_telegram_barridos_tipo_monitoreo_alerta
                FOREIGN KEY (tipo_monitoreo_alerta_id)
                REFERENCES public.tipo_monitoreo_alertas (id)
                ON UPDATE CASCADE
                ON DELETE RESTRICT
        """
    )
    op.execute(
        """
        ALTER TABLE public.telegram_barrido_respuestas
            ADD CONSTRAINT fk_telegram_barrido_respuestas_monitoreo_opcion
                FOREIGN KEY (monitoreo_opcion_id)
                REFERENCES public.monitoreo_opciones (id)
                ON UPDATE CASCADE
                ON DELETE RESTRICT
        """
    )
    op.execute(
        """
        ALTER TABLE public.telegram_eventos
            ADD CONSTRAINT fk_telegram_eventos_tipo_monitoreo_alerta
                FOREIGN KEY (tipo_monitoreo_alerta_id)
                REFERENCES public.tipo_monitoreo_alertas (id),
            ADD CONSTRAINT fk_telegram_eventos_monitoreo_opcion
                FOREIGN KEY (monitoreo_opcion_id)
                REFERENCES public.monitoreo_opciones (id)
        """
    )

    op.execute(
        "CREATE INDEX idx_monitoreo_opciones_tipo_alerta "
        "ON public.monitoreo_opciones (tipo_monitoreo_alerta_id)"
    )
    op.execute(
        "CREATE INDEX idx_monitoreo_opciones_tipo_flujo "
        "ON public.monitoreo_opciones (tipo_monitoreo_flujo_id)"
    )
    op.execute(
        "CREATE UNIQUE INDEX uq_monitoreo_opciones_tipo_flujo_orden "
        "ON public.monitoreo_opciones (tipo_monitoreo_alerta_id, tipo_monitoreo_flujo_id, orden)"
    )
    op.execute(
        "CREATE INDEX idx_monitoreo_recomendaciones_tipo_alerta "
        "ON public.monitoreo_recomendaciones (tipo_monitoreo_alerta_id)"
    )
    op.execute(
        "CREATE INDEX idx_telegram_interacciones_contacto "
        "ON public.telegram_interacciones (contacto_id)"
    )
    op.execute(
        "CREATE INDEX idx_telegram_interacciones_codigo "
        "ON public.telegram_interacciones (codigo)"
    )
    op.execute(
        "CREATE INDEX idx_telegram_interacciones_fecha "
        "ON public.telegram_interacciones (fecha_interaccion)"
    )
    op.execute(
        "CREATE INDEX idx_telegram_interacciones_parametros "
        "ON public.telegram_interacciones USING gin (parametros)"
    )

    for table_name in (
        "tipo_monitoreo_alertas",
        "tipo_monitoreo_flujo",
        "monitoreo_opciones",
        "monitoreo_recomendaciones",
        "telegram_interacciones",
    ):
        op.execute(
            f"""
            SELECT setval(
                pg_get_serial_sequence('public.{table_name}', 'id'),
                GREATEST((SELECT COALESCE(max(id), 1) FROM public.{table_name}), 1),
                (SELECT count(*) > 0 FROM public.{table_name})
            )
            """
        )


def downgrade() -> None:
    # La reconstruccion no cambia el contrato logico. Se conserva el orden
    # corregido para que las migraciones anteriores puedan revertirse sin
    # reintroducir el esquema fisico inconsistente.
    pass
