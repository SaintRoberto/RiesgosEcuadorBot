"""Crea catalogo de tipos de alerta."""

from collections.abc import Sequence

from alembic import op

revision: str = "20260804_0003"
down_revision: str | None = "20260723_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS public.tipo_alertas
        (
            id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            descripcion varchar(150) NOT NULL,
            activo boolean NOT NULL DEFAULT true,
            fecha_creacion timestamp with time zone NOT NULL DEFAULT now(),
            CONSTRAINT uq_tipo_alertas_descripcion UNIQUE (descripcion)
        )
        """
    )
    op.execute(
        """
        INSERT INTO public.tipo_alertas (id, descripcion)
        OVERRIDING SYSTEM VALUE
        VALUES
            (1, 'CA\u00cdDA DE CENIZA'),
            (2, 'DERRUMBES'),
            (3, 'ESTADO DE RIOS/QUEBRADAS'),
            (4, 'SISMO'),
            (5, 'INCENDIOS FORESTALES'),
            (6, 'LLUVIAS'),
            (7, 'OLEAJE')
        ON CONFLICT (id) DO UPDATE
        SET descripcion = EXCLUDED.descripcion,
            activo = true
        """
    )
    op.execute(
        """
        SELECT setval(
            pg_get_serial_sequence('public.tipo_alertas', 'id'),
            (SELECT max(id) FROM public.tipo_alertas),
            true
        )
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS public.tipo_alertas")
