from datetime import datetime
from typing import Any

from decimal import Decimal

from sqlalchemy import BigInteger, Boolean, CheckConstraint, DateTime, ForeignKey, Identity, Index, Numeric, String, Text, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class TelegramContacto(Base):
    """Mapeo mínimo de la tabla existente, suficiente para validar el contacto."""

    __tablename__ = "telegram_contactos"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    usuario_id: Mapped[int | None] = mapped_column(BigInteger)
    telegram_user_id: Mapped[int] = mapped_column(BigInteger)
    chat_id: Mapped[int] = mapped_column(BigInteger)
    telefono: Mapped[str | None] = mapped_column(String(20))
    nombres: Mapped[str | None] = mapped_column(String(200))
    institucion: Mapped[str | None] = mapped_column(String(250))
    activo: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    fecha_modificacion: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class CatalogoNivelEvento(Base):
    __tablename__ = "catalogo_niveles_evento"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    nombre: Mapped[str] = mapped_column(String(100), nullable=False)
    descripcion: Mapped[str | None] = mapped_column(String(300))
    activo: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))


class TelegramBarrido(Base):
    __tablename__ = "telegram_barridos"

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    contacto_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("telegram_contactos.id", name="fk_telegram_barridos_contacto"),
        nullable=False,
    )
    nivel_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("catalogo_niveles_evento.id", name="fk_telegram_barridos_nivel"),
        nullable=False,
    )
    latitud: Mapped[Decimal] = mapped_column(Numeric(10, 7), nullable=False)
    longitud: Mapped[Decimal] = mapped_column(Numeric(10, 7), nullable=False)
    fecha_barrido: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    activo: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    fecha_creacion: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )


class TelegramConsulta(Base):
    __tablename__ = "telegram_consultas"
    __table_args__ = (
        CheckConstraint(
            "estado IN ('PENDIENTE', 'PROCESANDO', 'COMPLETADA', 'ERROR')",
            name="chk_telegram_consultas_estado",
        ),
        Index("idx_telegram_consultas_contacto", "contacto_id"),
        Index("idx_telegram_consultas_codigo", "codigo"),
        Index("idx_telegram_consultas_fecha", "fecha_consulta"),
        Index("idx_telegram_consultas_parametros", "parametros", postgresql_using="gin"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    contacto_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("telegram_contactos.id", name="fk_telegram_consultas_contacto"),
        nullable=False,
    )
    usuario_id: Mapped[int | None] = mapped_column(BigInteger)
    tipo_consulta: Mapped[str] = mapped_column(String(50), nullable=False)
    codigo: Mapped[str | None] = mapped_column(String(150))
    consulta: Mapped[str | None] = mapped_column(Text)
    parametros: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        server_default=text("'{}'::jsonb"),
    )
    respuesta: Mapped[Any | None] = mapped_column(JSONB)
    estado: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="PENDIENTE",
        server_default=text("'PENDIENTE'"),
    )
    mensaje_error: Mapped[str | None] = mapped_column(Text)
    fecha_consulta: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    fecha_respuesta: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
