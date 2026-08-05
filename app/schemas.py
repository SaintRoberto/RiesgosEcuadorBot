from datetime import date
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field, HttpUrl, field_validator


class HealthRespuesta(BaseModel):
    estado: str
    base_datos: str


class NivelLluvia(StrEnum):
    debil = "DEBIL"
    moderado = "MODERADO"
    fuerte = "FUERTE"
    muy_fuerte = "MUY_FUERTE"


MAPA_NIVELES_LLUVIAS = {
    "1": NivelLluvia.debil,
    "2": NivelLluvia.moderado,
    "3": NivelLluvia.fuerte,
    "4": NivelLluvia.muy_fuerte,
}


class CrearBoletinRequest(BaseModel):
    telefonos: list[str] = Field(..., min_length=1, examples=[["+593987223658"]])
    url_boletin: HttpUrl
    titulo: str | None = Field(default=None, max_length=250)
    fecha_boletin: date | None = None
    codigo: str | None = Field(default=None, max_length=150)
    usuario_id: int | None = None


class SolicitarBarridoRequest(BaseModel):
    telefonos: list[str] = Field(..., min_length=1, examples=[["+593987223658"]])
    fecha_barrido: date | None = None
    codigo: str | None = Field(default=None, max_length=150)
    mensaje: str | None = Field(default=None, max_length=500)
    usuario_id: int | None = None


class RegistrarBarridoRequest(BaseModel):
    telefono: str = Field(..., examples=["+593987223658"])
    nivel_lluvia: NivelLluvia = Field(..., examples=["2"])
    latitud: float = Field(..., ge=-90, le=90, examples=[-0.1806532])
    longitud: float = Field(..., ge=-180, le=180, examples=[-78.4678382])
    codigo: str | None = Field(default=None, max_length=150)
    observacion: str | None = None
    usuario_id: int | None = None

    @field_validator("nivel_lluvia", mode="before")
    @classmethod
    def normalizar_nivel_lluvia(cls, value: Any) -> Any:
        if isinstance(value, int):
            return MAPA_NIVELES_LLUVIAS.get(str(value), value)
        if isinstance(value, str):
            texto = value.strip().upper()
            return MAPA_NIVELES_LLUVIAS.get(texto, texto)
        return value


class CrearSeguimientoEventoRequest(BaseModel):
    telefonos: list[str] = Field(..., min_length=1, examples=[["+593987223658"]])
    evento_codigo: str = Field(..., max_length=150)
    descripcion: str = Field(..., min_length=1)
    fecha_inicio: date | None = None
    fecha_fin: date | None = None
    enviar_correo: bool = True
    mensaje: str | None = Field(default=None, max_length=1000)
    usuario_id: int | None = None


class TipoAlertaRespuesta(BaseModel):
    id: int
    descripcion: str
    activo: bool


class AlertaEncuestaRespuesta(BaseModel):
    id: int
    tipo_alerta_id: int
    nombre: str
    descripcion: str | None
    color: str | None
    orden: int
    activo: bool


class AlertaRecomendacionRespuesta(BaseModel):
    id: int
    tipo_alerta_id: int
    recomendacion: str
    orden: int
    activo: bool


class RegistroFlujoRespuesta(BaseModel):
    id: int
    telefono: str | None
    tipo_consulta: str
    codigo: str | None
    estado: str


class EnvioFlujoRespuesta(BaseModel):
    codigo: str
    total: int
    registros: list[RegistroFlujoRespuesta]


class BarridoGuardadoRespuesta(BaseModel):
    id: int
    barrido_id: int
    telefono: str | None
    codigo: str | None
    estado: str
    nivel_lluvia: NivelLluvia
    nivel_id: int
    latitud: float
    longitud: float


class NivelLluviaResumenRespuesta(BaseModel):
    nivel: NivelLluvia
    etiqueta: str
    cantidad: int


class ReporteLluviaRespuesta(BaseModel):
    total: int
    niveles: list[NivelLluviaResumenRespuesta]


class EnviarReporteLluviaGraficoRequest(BaseModel):
    chat_id: int | None = Field(
        default=None,
        description="Chat de Telegram destino. Si se omite, se usa el admin configurado.",
    )
    titulo: str | None = Field(default=None, max_length=120)


class EnviarReporteLluviaGraficoRespuesta(BaseModel):
    chat_id: int
    chart_url: str
    telegram: dict[str, Any]


class TelegramWebhookRespuesta(BaseModel):
    estado: str
    mensaje: str
    contacto_id: int | None = None
    telefono: str | None = None
    chat_id: int | None = None


class EventoRespuesta(BaseModel):
    id: int
    contacto_id: int
    tipo_alerta_id: int | None = None
    nombre_alerta: str | None = None
    alerta_encuesta_id: int | None = None
    descripcion: str
    cantidad_personas_riesgo: int
    latitud: float
    longitud: float
    fecha_reporte: str
    foto_url: str


class FotoEventoRespuesta(BaseModel):
    evento_id: int
    contacto_id: int
    tipo_alerta_id: int | None = None
    alerta_encuesta_id: int | None = None
    descripcion: str
    personas_en_riesgo: bool
    cantidad_personas_riesgo: int
    latitud: float
    longitud: float
    fecha_reporte: str
    foto_url: str
    foto_file_unique_id: str | None = None
