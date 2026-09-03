"""Renombra catalogos y referencias con terminologia de monitoreo.

Revision ID: 20260903_0012
Revises: 20260903_0011
Create Date: 2026-09-03
"""
from collections.abc import Sequence

from alembic import op

revision: str = "20260903_0012"
down_revision: str | None = "20260903_0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.rename_table("tipo_alertas", "tipo_monitoreo_alertas")
    op.rename_table("tipo_flujo", "tipo_monitoreo_flujo")
    op.rename_table("alerta_encuesta", "monitoreo_opciones")
    op.rename_table("alerta_recomendaciones", "monitoreo_recomendaciones")

    op.alter_column("monitoreo_opciones", "tipo_alerta_id", new_column_name="tipo_monitoreo_alerta_id")
    op.alter_column("monitoreo_opciones", "tipo_flujo_id", new_column_name="tipo_monitoreo_flujo_id")
    op.alter_column(
        "monitoreo_recomendaciones",
        "tipo_alerta_id",
        new_column_name="tipo_monitoreo_alerta_id",
    )
    op.alter_column("telegram_barridos", "tipo_alerta_id", new_column_name="tipo_monitoreo_alerta_id")
    op.alter_column(
        "telegram_barrido_respuestas",
        "alerta_encuesta_id",
        new_column_name="monitoreo_opcion_id",
    )
    op.alter_column("telegram_eventos", "tipo_alerta_id", new_column_name="tipo_monitoreo_alerta_id")
    op.alter_column("telegram_eventos", "alerta_encuesta_id", new_column_name="monitoreo_opcion_id")
    op.alter_column("telegram_interacciones", "tipo_consulta", new_column_name="tipo_interaccion")
    op.alter_column("telegram_interacciones", "consulta", new_column_name="mensaje")
    op.alter_column("telegram_interacciones", "fecha_consulta", new_column_name="fecha_interaccion")

    op.execute(
        "ALTER TABLE tipo_monitoreo_alertas "
        "RENAME CONSTRAINT tipo_alertas_pkey TO tipo_monitoreo_alertas_pkey"
    )
    op.execute(
        "ALTER TABLE tipo_monitoreo_alertas "
        "RENAME CONSTRAINT uq_tipo_alertas_descripcion TO uq_tipo_monitoreo_alertas_descripcion"
    )
    op.execute(
        "ALTER TABLE tipo_monitoreo_flujo "
        "RENAME CONSTRAINT tipo_flujo_pkey TO tipo_monitoreo_flujo_pkey"
    )
    op.execute(
        "ALTER TABLE tipo_monitoreo_flujo "
        "RENAME CONSTRAINT uq_tipo_flujo_codigo TO uq_tipo_monitoreo_flujo_codigo"
    )
    op.execute(
        "ALTER TABLE monitoreo_opciones "
        "RENAME CONSTRAINT alerta_encuesta_pkey TO monitoreo_opciones_pkey"
    )
    op.execute(
        "ALTER TABLE monitoreo_opciones "
        "RENAME CONSTRAINT fk_alerta_encuesta_tipo_alerta TO fk_monitoreo_opciones_tipo_alerta"
    )
    op.execute(
        "ALTER TABLE monitoreo_opciones "
        "RENAME CONSTRAINT fk_alerta_encuesta_tipo_flujo TO fk_monitoreo_opciones_tipo_flujo"
    )
    op.execute(
        "ALTER TABLE monitoreo_recomendaciones "
        "RENAME CONSTRAINT alerta_recomendaciones_pkey TO monitoreo_recomendaciones_pkey"
    )
    op.execute(
        "ALTER TABLE monitoreo_recomendaciones "
        "RENAME CONSTRAINT fk_alerta_recomendaciones_tipo_alerta "
        "TO fk_monitoreo_recomendaciones_tipo_alerta"
    )
    op.execute(
        "ALTER TABLE monitoreo_recomendaciones "
        "RENAME CONSTRAINT uq_alerta_recomendaciones_tipo_orden "
        "TO uq_monitoreo_recomendaciones_tipo_orden"
    )
    op.execute(
        "ALTER TABLE telegram_barridos "
        "RENAME CONSTRAINT fk_telegram_barridos_tipo_alerta "
        "TO fk_telegram_barridos_tipo_monitoreo_alerta"
    )
    op.execute(
        "ALTER TABLE telegram_barrido_respuestas "
        "RENAME CONSTRAINT fk_telegram_barrido_respuestas_alerta_encuesta "
        "TO fk_telegram_barrido_respuestas_monitoreo_opcion"
    )
    op.execute(
        "ALTER TABLE telegram_eventos "
        "RENAME CONSTRAINT fk_telegram_eventos_tipo_alerta "
        "TO fk_telegram_eventos_tipo_monitoreo_alerta"
    )
    op.execute(
        "ALTER TABLE telegram_eventos "
        "RENAME CONSTRAINT fk_telegram_eventos_alerta_encuesta "
        "TO fk_telegram_eventos_monitoreo_opcion"
    )

    op.execute("ALTER SEQUENCE tipo_alertas_id_seq RENAME TO tipo_monitoreo_alertas_id_seq")
    op.execute("ALTER SEQUENCE tipo_flujo_id_seq RENAME TO tipo_monitoreo_flujo_id_seq")
    op.execute("ALTER SEQUENCE alerta_encuesta_id_seq RENAME TO monitoreo_opciones_id_seq")
    op.execute("ALTER SEQUENCE alerta_recomendaciones_id_seq RENAME TO monitoreo_recomendaciones_id_seq")

    op.execute(
        "ALTER INDEX idx_alerta_encuesta_tipo_flujo RENAME TO idx_monitoreo_opciones_tipo_flujo"
    )
    op.execute(
        "ALTER INDEX uq_alerta_encuesta_tipo_flujo_orden "
        "RENAME TO uq_monitoreo_opciones_tipo_flujo_orden"
    )
    op.execute(
        "ALTER INDEX idx_telegram_barridos_tipo_alerta "
        "RENAME TO idx_telegram_barridos_tipo_monitoreo_alerta"
    )
    op.execute(
        "ALTER INDEX idx_telegram_barrido_respuestas_alerta_encuesta "
        "RENAME TO idx_telegram_barrido_respuestas_monitoreo_opcion"
    )
    op.execute(
        "ALTER INDEX idx_telegram_eventos_tipo_alerta "
        "RENAME TO idx_telegram_eventos_tipo_monitoreo_alerta"
    )
    op.execute(
        "ALTER INDEX idx_telegram_eventos_alerta_encuesta "
        "RENAME TO idx_telegram_eventos_monitoreo_opcion"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_monitoreo_opciones_tipo_alerta "
        "ON monitoreo_opciones (tipo_monitoreo_alerta_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_monitoreo_recomendaciones_tipo_alerta "
        "ON monitoreo_recomendaciones (tipo_monitoreo_alerta_id)"
    )

    op.execute(
        """
        UPDATE telegram_interacciones
        SET parametros =
            (parametros - 'tipo_alerta' - 'encuesta' - 'encuesta_barrido_opciones')
            || CASE WHEN parametros ? 'tipo_alerta'
                THEN jsonb_build_object('tipo_monitoreo_alerta', parametros->'tipo_alerta')
                ELSE '{}'::jsonb END
            || CASE WHEN parametros ? 'encuesta'
                THEN jsonb_build_object('opcion_monitoreo', parametros->'encuesta')
                ELSE '{}'::jsonb END
            || CASE WHEN parametros ? 'encuesta_barrido_opciones'
                THEN jsonb_build_object(
                    'opciones_monitoreo_barrido',
                    parametros->'encuesta_barrido_opciones'
                )
                ELSE '{}'::jsonb END
        """
    )
    op.execute(
        """
        UPDATE telegram_interacciones
        SET respuesta = CASE WHEN respuesta IS NULL THEN NULL ELSE
            (respuesta - 'tipo_alerta' - 'encuesta' - 'encuesta_barrido_opciones' - 'alerta_encuesta_id')
            || CASE WHEN respuesta ? 'tipo_alerta'
                THEN jsonb_build_object('tipo_monitoreo_alerta', respuesta->'tipo_alerta')
                ELSE '{}'::jsonb END
            || CASE WHEN respuesta ? 'encuesta'
                THEN jsonb_build_object('opcion_monitoreo', respuesta->'encuesta')
                ELSE '{}'::jsonb END
            || CASE WHEN respuesta ? 'encuesta_barrido_opciones'
                THEN jsonb_build_object(
                    'opciones_monitoreo_barrido',
                    respuesta->'encuesta_barrido_opciones'
                )
                ELSE '{}'::jsonb END
            || CASE WHEN respuesta ? 'alerta_encuesta_id'
                THEN jsonb_build_object('monitoreo_opcion_id', respuesta->'alerta_encuesta_id')
                ELSE '{}'::jsonb END
        END
        """
    )


def downgrade() -> None:
    op.execute(
        """
        UPDATE telegram_interacciones
        SET parametros =
            (parametros - 'tipo_monitoreo_alerta' - 'opcion_monitoreo' - 'opciones_monitoreo_barrido')
            || CASE WHEN parametros ? 'tipo_monitoreo_alerta'
                THEN jsonb_build_object('tipo_alerta', parametros->'tipo_monitoreo_alerta')
                ELSE '{}'::jsonb END
            || CASE WHEN parametros ? 'opcion_monitoreo'
                THEN jsonb_build_object('encuesta', parametros->'opcion_monitoreo')
                ELSE '{}'::jsonb END
            || CASE WHEN parametros ? 'opciones_monitoreo_barrido'
                THEN jsonb_build_object(
                    'encuesta_barrido_opciones',
                    parametros->'opciones_monitoreo_barrido'
                )
                ELSE '{}'::jsonb END
        """
    )
    op.execute(
        """
        UPDATE telegram_interacciones
        SET respuesta = CASE WHEN respuesta IS NULL THEN NULL ELSE
            (respuesta - 'tipo_monitoreo_alerta' - 'opcion_monitoreo' - 'opciones_monitoreo_barrido' - 'monitoreo_opcion_id')
            || CASE WHEN respuesta ? 'tipo_monitoreo_alerta'
                THEN jsonb_build_object('tipo_alerta', respuesta->'tipo_monitoreo_alerta')
                ELSE '{}'::jsonb END
            || CASE WHEN respuesta ? 'opcion_monitoreo'
                THEN jsonb_build_object('encuesta', respuesta->'opcion_monitoreo')
                ELSE '{}'::jsonb END
            || CASE WHEN respuesta ? 'opciones_monitoreo_barrido'
                THEN jsonb_build_object(
                    'encuesta_barrido_opciones',
                    respuesta->'opciones_monitoreo_barrido'
                )
                ELSE '{}'::jsonb END
            || CASE WHEN respuesta ? 'monitoreo_opcion_id'
                THEN jsonb_build_object('alerta_encuesta_id', respuesta->'monitoreo_opcion_id')
                ELSE '{}'::jsonb END
        END
        """
    )

    op.execute("DROP INDEX IF EXISTS idx_monitoreo_recomendaciones_tipo_alerta")
    op.execute("DROP INDEX IF EXISTS idx_monitoreo_opciones_tipo_alerta")
    op.execute(
        "ALTER INDEX idx_telegram_eventos_monitoreo_opcion "
        "RENAME TO idx_telegram_eventos_alerta_encuesta"
    )
    op.execute(
        "ALTER INDEX idx_telegram_eventos_tipo_monitoreo_alerta "
        "RENAME TO idx_telegram_eventos_tipo_alerta"
    )
    op.execute(
        "ALTER INDEX idx_telegram_barrido_respuestas_monitoreo_opcion "
        "RENAME TO idx_telegram_barrido_respuestas_alerta_encuesta"
    )
    op.execute(
        "ALTER INDEX idx_telegram_barridos_tipo_monitoreo_alerta "
        "RENAME TO idx_telegram_barridos_tipo_alerta"
    )
    op.execute(
        "ALTER INDEX uq_monitoreo_opciones_tipo_flujo_orden "
        "RENAME TO uq_alerta_encuesta_tipo_flujo_orden"
    )
    op.execute(
        "ALTER INDEX idx_monitoreo_opciones_tipo_flujo RENAME TO idx_alerta_encuesta_tipo_flujo"
    )

    op.execute("ALTER SEQUENCE monitoreo_recomendaciones_id_seq RENAME TO alerta_recomendaciones_id_seq")
    op.execute("ALTER SEQUENCE monitoreo_opciones_id_seq RENAME TO alerta_encuesta_id_seq")
    op.execute("ALTER SEQUENCE tipo_monitoreo_flujo_id_seq RENAME TO tipo_flujo_id_seq")
    op.execute("ALTER SEQUENCE tipo_monitoreo_alertas_id_seq RENAME TO tipo_alertas_id_seq")

    op.execute(
        "ALTER TABLE telegram_eventos "
        "RENAME CONSTRAINT fk_telegram_eventos_monitoreo_opcion "
        "TO fk_telegram_eventos_alerta_encuesta"
    )
    op.execute(
        "ALTER TABLE telegram_eventos "
        "RENAME CONSTRAINT fk_telegram_eventos_tipo_monitoreo_alerta "
        "TO fk_telegram_eventos_tipo_alerta"
    )
    op.execute(
        "ALTER TABLE telegram_barrido_respuestas "
        "RENAME CONSTRAINT fk_telegram_barrido_respuestas_monitoreo_opcion "
        "TO fk_telegram_barrido_respuestas_alerta_encuesta"
    )
    op.execute(
        "ALTER TABLE telegram_barridos "
        "RENAME CONSTRAINT fk_telegram_barridos_tipo_monitoreo_alerta "
        "TO fk_telegram_barridos_tipo_alerta"
    )
    op.execute(
        "ALTER TABLE monitoreo_recomendaciones "
        "RENAME CONSTRAINT uq_monitoreo_recomendaciones_tipo_orden "
        "TO uq_alerta_recomendaciones_tipo_orden"
    )
    op.execute(
        "ALTER TABLE monitoreo_recomendaciones "
        "RENAME CONSTRAINT fk_monitoreo_recomendaciones_tipo_alerta "
        "TO fk_alerta_recomendaciones_tipo_alerta"
    )
    op.execute(
        "ALTER TABLE monitoreo_recomendaciones "
        "RENAME CONSTRAINT monitoreo_recomendaciones_pkey TO alerta_recomendaciones_pkey"
    )
    op.execute(
        "ALTER TABLE monitoreo_opciones "
        "RENAME CONSTRAINT fk_monitoreo_opciones_tipo_flujo TO fk_alerta_encuesta_tipo_flujo"
    )
    op.execute(
        "ALTER TABLE monitoreo_opciones "
        "RENAME CONSTRAINT fk_monitoreo_opciones_tipo_alerta TO fk_alerta_encuesta_tipo_alerta"
    )
    op.execute(
        "ALTER TABLE monitoreo_opciones "
        "RENAME CONSTRAINT monitoreo_opciones_pkey TO alerta_encuesta_pkey"
    )
    op.execute(
        "ALTER TABLE tipo_monitoreo_flujo "
        "RENAME CONSTRAINT uq_tipo_monitoreo_flujo_codigo TO uq_tipo_flujo_codigo"
    )
    op.execute(
        "ALTER TABLE tipo_monitoreo_flujo "
        "RENAME CONSTRAINT tipo_monitoreo_flujo_pkey TO tipo_flujo_pkey"
    )
    op.execute(
        "ALTER TABLE tipo_monitoreo_alertas "
        "RENAME CONSTRAINT uq_tipo_monitoreo_alertas_descripcion TO uq_tipo_alertas_descripcion"
    )
    op.execute(
        "ALTER TABLE tipo_monitoreo_alertas "
        "RENAME CONSTRAINT tipo_monitoreo_alertas_pkey TO tipo_alertas_pkey"
    )

    op.alter_column("telegram_interacciones", "fecha_interaccion", new_column_name="fecha_consulta")
    op.alter_column("telegram_interacciones", "mensaje", new_column_name="consulta")
    op.alter_column("telegram_interacciones", "tipo_interaccion", new_column_name="tipo_consulta")
    op.alter_column("telegram_eventos", "monitoreo_opcion_id", new_column_name="alerta_encuesta_id")
    op.alter_column("telegram_eventos", "tipo_monitoreo_alerta_id", new_column_name="tipo_alerta_id")
    op.alter_column(
        "telegram_barrido_respuestas",
        "monitoreo_opcion_id",
        new_column_name="alerta_encuesta_id",
    )
    op.alter_column("telegram_barridos", "tipo_monitoreo_alerta_id", new_column_name="tipo_alerta_id")
    op.alter_column(
        "monitoreo_recomendaciones",
        "tipo_monitoreo_alerta_id",
        new_column_name="tipo_alerta_id",
    )
    op.alter_column("monitoreo_opciones", "tipo_monitoreo_flujo_id", new_column_name="tipo_flujo_id")
    op.alter_column("monitoreo_opciones", "tipo_monitoreo_alerta_id", new_column_name="tipo_alerta_id")

    op.rename_table("monitoreo_recomendaciones", "alerta_recomendaciones")
    op.rename_table("monitoreo_opciones", "alerta_encuesta")
    op.rename_table("tipo_monitoreo_flujo", "tipo_flujo")
    op.rename_table("tipo_monitoreo_alertas", "tipo_alertas")
