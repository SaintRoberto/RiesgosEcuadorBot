"""Crea reportes de eventos de Telegram."""

from collections.abc import Sequence

from alembic import op

revision: str = "20260723_0002"
down_revision: str | None = "20260716_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS public.telegram_eventos
        (
            id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            contacto_id bigint NOT NULL,
            descripcion text NOT NULL,
            latitud numeric(10,7) NOT NULL,
            longitud numeric(10,7) NOT NULL,
            fecha_reporte timestamp with time zone NOT NULL DEFAULT now(),
            activo boolean NOT NULL DEFAULT true,
            fecha_creacion timestamp with time zone NOT NULL DEFAULT now(),
            foto_file_id text NOT NULL,
            foto_file_unique_id text,
            CONSTRAINT fk_telegram_eventos_contacto
                FOREIGN KEY (contacto_id)
                REFERENCES public.telegram_contactos (id)
                ON UPDATE NO ACTION
                ON DELETE NO ACTION,
            CONSTRAINT chk_telegram_eventos_latitud
                CHECK (latitud >= -90 AND latitud <= 90),
            CONSTRAINT chk_telegram_eventos_longitud
                CHECK (longitud >= -180 AND longitud <= 180)
        )
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS public.telegram_eventos")
