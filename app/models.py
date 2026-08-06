from datetime import datetime
from typing import Any

from decimal import Decimal

from sqlalchemy import BigInteger, Boolean, CheckConstraint, DateTime, ForeignKey, Identity, Index, Integer, Numeric, String, Text, func, text
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


class TipoAlerta(Base):
    __tablename__ = "tipo_alertas"

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    descripcion: Mapped[str] = mapped_column(String(150), nullable=False, unique=True)
    activo: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    fecha_creacion: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )


class AlertaEncuesta(Base):
    __tablename__ = "alerta_encuesta"
    __table_args__ = (
        Index("idx_alerta_encuesta_tipo_alerta", "tipo_alerta_id"),
        Index("uq_alerta_encuesta_tipo_orden", "tipo_alerta_id", "orden", unique=True),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    tipo_alerta_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("tipo_alertas.id", name="fk_alerta_encuesta_tipo_alerta"),
        nullable=False,
    )
    nombre: Mapped[str] = mapped_column(String(150), nullable=False)
    descripcion: Mapped[str | None] = mapped_column(Text)
    color: Mapped[str | None] = mapped_column(String(20))
    orden: Mapped[int] = mapped_column(Integer, nullable=False)
    activo: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    fecha_creacion: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )


class AlertaRecomendacion(Base):
    __tablename__ = "alerta_recomendaciones"
    __table_args__ = (
        Index("idx_alerta_recomendaciones_tipo_alerta", "tipo_alerta_id"),
        Index("uq_alerta_recomendaciones_tipo_orden", "tipo_alerta_id", "orden", unique=True),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    tipo_alerta_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("tipo_alertas.id", name="fk_alerta_recomendaciones_tipo_alerta"),
        nullable=False,
    )
    recomendacion: Mapped[str] = mapped_column(Text, nullable=False)
    orden: Mapped[int] = mapped_column(Integer, nullable=False)
    activo: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    fecha_creacion: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )


class TelegramBarrido(Base):
    __tablename__ = "telegram_barridos"
    __table_args__ = (
        Index("idx_telegram_barridos_tipo_alerta", "tipo_alerta_id"),
        Index("idx_telegram_barridos_fecha", "fecha_barrido"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    tipo_alerta_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("tipo_alertas.id", name="fk_telegram_barridos_tipo_alerta"),
        nullable=False,
    )
    codigo: Mapped[str | None] = mapped_column(String(150))
    mensaje: Mapped[str | None] = mapped_column(Text)
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


class TelegramBarridoRespuesta(Base):
    __tablename__ = "telegram_barrido_respuestas"
    __table_args__ = (
        CheckConstraint("latitud >= -90 AND latitud <= 90", name="chk_telegram_barrido_respuestas_latitud"),
        CheckConstraint("longitud >= -180 AND longitud <= 180", name="chk_telegram_barrido_respuestas_longitud"),
        Index("idx_telegram_barrido_respuestas_barrido", "barrido_id"),
        Index("idx_telegram_barrido_respuestas_contacto", "contacto_id"),
        Index("idx_telegram_barrido_respuestas_alerta_encuesta", "alerta_encuesta_id"),
        Index("uq_telegram_barrido_respuesta_contacto", "barrido_id", "contacto_id", unique=True),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    barrido_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("telegram_barridos.id", name="fk_telegram_barrido_respuestas_barrido"),
        nullable=False,
    )
    contacto_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("telegram_contactos.id", name="fk_telegram_barrido_respuestas_contacto"),
        nullable=False,
    )
    alerta_encuesta_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("alerta_encuesta.id", name="fk_telegram_barrido_respuestas_alerta_encuesta"),
        nullable=False,
    )
    latitud: Mapped[Decimal] = mapped_column(Numeric(10, 7), nullable=False)
    longitud: Mapped[Decimal] = mapped_column(Numeric(10, 7), nullable=False)
    fecha_respuesta: Mapped[datetime] = mapped_column(
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


class TelegramEvento(Base):
    __tablename__ = "telegram_eventos"
    __table_args__ = (
        CheckConstraint("latitud >= -90 AND latitud <= 90", name="chk_telegram_eventos_latitud"),
        CheckConstraint("longitud >= -180 AND longitud <= 180", name="chk_telegram_eventos_longitud"),
        CheckConstraint(
            "cantidad_personas_riesgo >= 0 AND cantidad_personas_riesgo <= 999999",
            name="chk_telegram_eventos_cantidad_personas_riesgo",
        ),
        Index("idx_telegram_eventos_tipo_alerta", "tipo_alerta_id"),
        Index("idx_telegram_eventos_alerta_encuesta", "alerta_encuesta_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    contacto_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("telegram_contactos.id", name="fk_telegram_eventos_contacto"),
        nullable=False,
    )
    tipo_alerta_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("tipo_alertas.id", name="fk_telegram_eventos_tipo_alerta"),
    )
    alerta_encuesta_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("alerta_encuesta.id", name="fk_telegram_eventos_alerta_encuesta"),
    )
    descripcion: Mapped[str] = mapped_column(Text, nullable=False)
    personas_en_riesgo: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    cantidad_personas_riesgo: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    latitud: Mapped[Decimal] = mapped_column(Numeric(10, 7), nullable=False)
    longitud: Mapped[Decimal] = mapped_column(Numeric(10, 7), nullable=False)
    fecha_reporte: Mapped[datetime] = mapped_column(
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
    foto_file_id: Mapped[str] = mapped_column(Text, nullable=False)
    foto_file_unique_id: Mapped[str | None] = mapped_column(Text)


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
