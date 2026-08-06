from datetime import date

from pydantic import BaseModel, Field, HttpUrl


class HealthRespuesta(BaseModel):
    estado: str
    base_datos: str


class CrearBoletinRequest(BaseModel):
    telefonos: list[str] = Field(..., min_length=1, examples=[["+593987223658"]])
    url_boletin: HttpUrl
    titulo: str | None = Field(default=None, max_length=250)
    fecha_boletin: date | None = None
    codigo: str | None = Field(default=None, max_length=150)
    usuario_id: int | None = None


class SolicitarBarridoRequest(BaseModel):
    telefonos: list[str] = Field(..., min_length=1, examples=[["+593987223658"]])
    tipo_alerta_id: int = Field(default=6, examples=[6])
    fecha_barrido: date | None = None
    codigo: str | None = Field(default=None, max_length=150)
    mensaje: str | None = Field(default=None, max_length=500)
    usuario_id: int | None = None


class RegistrarBarridoRequest(BaseModel):
    telefono: str = Field(..., examples=["+593987223658"])
    alerta_encuesta_id: int = Field(..., examples=[16])
    latitud: float = Field(..., ge=-90, le=90, examples=[-0.1806532])
    longitud: float = Field(..., ge=-180, le=180, examples=[-78.4678382])
    codigo: str | None = Field(default=None, max_length=150)
    observacion: str | None = None
    usuario_id: int | None = None


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
    barrido_id: int | None = None
    total: int
    registros: list[RegistroFlujoRespuesta]


class BarridoGuardadoRespuesta(BaseModel):
    id: int
    barrido_id: int
    barrido_respuesta_id: int
    telefono: str | None
    codigo: str | None
    estado: str
    tipo_alerta_id: int
    alerta_encuesta_id: int
    latitud: float
    longitud: float


class BarridoResumenRespuesta(BaseModel):
    id: int
    tipo_alerta_id: int
    nombre_alerta: str | None = None
    codigo: str | None = None
    mensaje: str | None = None
    fecha_barrido: str
    total_respuestas: int
    activo: bool


class BarridoRespuestaDetalle(BaseModel):
    id: int
    barrido_id: int
    fecha_barrido: str
    tipo_alerta_id: int
    nombre_alerta: str | None = None
    alerta_encuesta_id: int
    nivel_alerta: str | None = None
    contacto_id: int
    telefono: str | None = None
    nombres: str | None = None
    descripcion: str | None = None
    personas_en_riesgo: bool
    cantidad_personas_riesgo: int
    latitud: float
    longitud: float
    provincia: str | None = None
    canton: str | None = None
    parroquia: str | None = None
    fecha_respuesta: str


class ReporteAlertaOpcionRespuesta(BaseModel):
    alerta_encuesta_id: int
    nombre: str
    descripcion: str | None = None
    color: str | None = None
    cantidad: int


class ReporteAlertaRespuesta(BaseModel):
    barrido_id: int | None = None
    tipo_alerta_id: int
    nombre_alerta: str
    fecha_barrido: str | None = None
    total: int
    opciones: list[ReporteAlertaOpcionRespuesta]
    chart_url: str


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
    provincia: str | None = None
    canton: str | None = None
    parroquia: str | None = None
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
