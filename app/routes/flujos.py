import re
from decimal import Decimal
from datetime import date, datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import CatalogoNivelEvento, TelegramBarrido, TelegramConsulta, TelegramContacto
from app.schemas import (
    BarridoGuardadoRespuesta,
    CrearBoletinRequest,
    CrearSeguimientoEventoRequest,
    EnvioFlujoRespuesta,
    MAPA_NIVELES_LLUVIAS,
    NivelLluvia,
    RegistrarBarridoRequest,
    RegistroFlujoRespuesta,
    SolicitarBarridoRequest,
    TelegramWebhookRespuesta,
)
from app.telegram import TelegramDeliveryError, TelegramSender, get_optional_telegram_sender, get_telegram_sender

router = APIRouter(prefix="/api/telegram", tags=["telegram"])

TIPO_BOLETIN = "BOLETIN_DIARIO"
TIPO_BARRIDO = "BARRIDO_GAD"
TIPO_SEGUIMIENTO = "SEGUIMIENTO_EVENTO"
PATRON_TELEFONO = re.compile(r"^\+?\d{8,15}$")
PATRON_TELEFONO_EN_TEXTO = re.compile(r"\+?\d[\d\s().-]{6,}\d")


def _codigo(prefix: str, valor: str | None, fecha: date | None = None) -> str:
    if valor:
        return valor
    return f"{prefix}-{(fecha or date.today()).isoformat()}"


def _contactos_activos_por_telefono(db: Session, telefonos: list[str]) -> list[TelegramContacto]:
    telefonos_unicos = list(dict.fromkeys(telefono.strip() for telefono in telefonos))
    contactos = list(
        db.scalars(
            select(TelegramContacto).where(
                TelegramContacto.telefono.in_(telefonos_unicos),
                TelegramContacto.activo.is_(True),
            )
        )
    )
    encontrados = {contacto.telefono for contacto in contactos}
    faltantes = [telefono for telefono in telefonos_unicos if telefono not in encontrados]
    if faltantes:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "mensaje": "Uno o mas contactos no existen o no estan activos.",
                "telefonos": faltantes,
            },
        )
    return contactos


def _respuesta_envio(codigo: str, registros: list[TelegramConsulta]) -> EnvioFlujoRespuesta:
    return EnvioFlujoRespuesta(
        codigo=codigo,
        total=len(registros),
        registros=[
            RegistroFlujoRespuesta(
                id=registro.id,
                telefono=registro.parametros.get("telefono") if registro.parametros else None,
                tipo_consulta=registro.tipo_consulta,
                codigo=registro.codigo,
                estado=registro.estado,
            )
            for registro in registros
        ],
    )


def _obtener_nivel_evento(db: Session, nivel: NivelLluvia) -> CatalogoNivelEvento:
    nivel_evento = db.scalars(
        select(CatalogoNivelEvento).where(
            CatalogoNivelEvento.nombre == nivel.value,
            CatalogoNivelEvento.activo.is_(True),
        )
    ).first()
    if nivel_evento is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No existe un nivel activo en catalogo_niveles_evento para {nivel.value}.",
        )
    return nivel_evento


def _consulta_barrido_activa_por_contacto(db: Session, contacto_id: int) -> TelegramConsulta | None:
    return db.scalars(
        select(TelegramConsulta)
        .where(
            TelegramConsulta.contacto_id == contacto_id,
            TelegramConsulta.tipo_consulta == TIPO_BARRIDO,
            TelegramConsulta.estado.in_(["PENDIENTE", "PROCESANDO", "COMPLETADA"]),
        )
        .order_by(TelegramConsulta.fecha_consulta.desc())
    ).first()


def _guardar_barrido(
    db: Session,
    contacto: TelegramContacto,
    nivel: NivelLluvia,
    latitud: float,
    longitud: float,
    registro: TelegramConsulta | None,
    observacion: str | None = None,
) -> tuple[TelegramConsulta, TelegramBarrido, CatalogoNivelEvento]:
    nivel_evento = _obtener_nivel_evento(db, nivel)
    if registro is None:
        registro = TelegramConsulta(
            contacto_id=contacto.id,
            usuario_id=contacto.usuario_id,
            tipo_consulta=TIPO_BARRIDO,
            consulta="Respuesta de barrido recibida por Telegram",
        )
        db.add(registro)

    barrido = TelegramBarrido(
        contacto_id=contacto.id,
        nivel_id=nivel_evento.id,
        latitud=Decimal(str(latitud)),
        longitud=Decimal(str(longitud)),
    )
    db.add(barrido)
    parametros = dict(registro.parametros or {})
    parametros.pop("ubicacion_pendiente", None)
    registro.parametros = parametros
    registro.respuesta = {
        "canal": "TELEGRAM",
        "telefono": contacto.telefono,
        "nivel_lluvia": nivel.value,
        "nivel_id": nivel_evento.id,
        "latitud": latitud,
        "longitud": longitud,
        "observacion": observacion,
    }
    registro.estado = "COMPLETADA"
    registro.fecha_respuesta = datetime.now(timezone.utc)
    db.commit()
    db.refresh(registro)
    db.refresh(barrido)
    return registro, barrido, nivel_evento


def _marcar_envio(registro: TelegramConsulta, sender: TelegramSender, chat_id: int, texto: str) -> None:
    try:
        registro.respuesta = sender.send_message(chat_id=chat_id, text=texto)
        registro.estado = "COMPLETADA"
        registro.fecha_respuesta = datetime.now(timezone.utc)
        registro.mensaje_error = None
    except TelegramDeliveryError as exc:
        registro.estado = "ERROR"
        registro.mensaje_error = str(exc)
        registro.fecha_respuesta = datetime.now(timezone.utc)


def _normalizar_telefono(texto: str) -> str | None:
    telefono = re.sub(r"[\s().-]", "", texto.strip())
    if not PATRON_TELEFONO.fullmatch(telefono):
        return None
    return telefono


def _extraer_telefono_de_mensaje(message: dict[str, Any], texto: str) -> str | None:
    for entity in message.get("entities") or []:
        if entity.get("type") != "phone_number":
            continue
        offset = int(entity.get("offset") or 0)
        length = int(entity.get("length") or 0)
        telefono = _normalizar_telefono(texto[offset : offset + length])
        if telefono:
            return telefono

    for match in PATRON_TELEFONO_EN_TEXTO.finditer(texto):
        telefono = _normalizar_telefono(match.group(0))
        if telefono:
            return telefono
    return None


def _nombre_desde_update(origen: dict[str, Any], chat: dict[str, Any]) -> str | None:
    partes = [
        origen.get("first_name") or chat.get("first_name"),
        origen.get("last_name") or chat.get("last_name"),
    ]
    nombre = " ".join(str(parte) for parte in partes if parte)
    return nombre or None


def _upsert_contacto_telegram(
    db: Session,
    chat_id: int,
    telegram_user_id: int,
    nombres: str | None,
    telefono: str | None = None,
) -> TelegramContacto:
    contacto = None
    if telefono:
        contacto = db.scalars(select(TelegramContacto).where(TelegramContacto.telefono == telefono)).first()
    if contacto is None:
        contacto = db.scalars(
            select(TelegramContacto).where(TelegramContacto.telegram_user_id == telegram_user_id)
        ).first()
    if contacto is None:
        contacto = TelegramContacto(
            telegram_user_id=telegram_user_id,
            chat_id=chat_id,
            telefono=telefono,
            nombres=nombres,
            activo=True,
        )
        db.add(contacto)
    else:
        contacto.telegram_user_id = telegram_user_id
        contacto.chat_id = chat_id
        contacto.nombres = nombres or contacto.nombres
        contacto.activo = True
        if telefono:
            contacto.telefono = telefono
    db.commit()
    db.refresh(contacto)
    return contacto


def _responder_si_es_posible(sender: TelegramSender | None, chat_id: int, texto: str) -> None:
    if sender is None:
        return
    try:
        sender.send_message(chat_id=chat_id, text=texto)
    except TelegramDeliveryError:
        return


def _contacto_por_chat_id(db: Session, chat_id: int) -> TelegramContacto | None:
    return db.scalars(
        select(TelegramContacto).where(
            TelegramContacto.chat_id == chat_id,
            TelegramContacto.activo.is_(True),
        )
    ).first()


@router.post(
    "/webhook",
    response_model=TelegramWebhookRespuesta,
    tags=["webhook"],
    summary="Recibir mensajes entrantes de Telegram y registrar contactos",
)
def recibir_webhook_telegram(
    update: dict[str, Any],
    db: Session = Depends(get_db),
    sender: TelegramSender | None = Depends(get_optional_telegram_sender),
) -> TelegramWebhookRespuesta:
    message = update.get("message") or update.get("edited_message")
    if not isinstance(message, dict):
        return TelegramWebhookRespuesta(estado="IGNORADO", mensaje="Update sin mensaje.")

    chat = message.get("chat") or {}
    origen = message.get("from") or {}
    texto = str(message.get("text") or "").strip()
    chat_id = chat.get("id")
    telegram_user_id = origen.get("id") or chat_id
    if chat_id is None or telegram_user_id is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="El update no contiene message.chat.id.",
        )

    nombres = _nombre_desde_update(origen, chat)

    if texto.lower().startswith("/start"):
        contacto = _upsert_contacto_telegram(
            db=db,
            chat_id=int(chat_id),
            telegram_user_id=int(telegram_user_id),
            nombres=nombres,
        )
        _responder_si_es_posible(
            sender,
            int(chat_id),
            "Bienvenido. Por favor envie su numero de telefono institucional. Ejemplo: +593987223658",
        )
        return TelegramWebhookRespuesta(
            estado="ESPERANDO_TELEFONO",
            mensaje="Contacto inicial registrado. Se solicito el telefono.",
            contacto_id=contacto.id,
            telefono=contacto.telefono,
            chat_id=contacto.chat_id,
        )

    location = message.get("location")
    if isinstance(location, dict):
        contacto = _contacto_por_chat_id(db, int(chat_id))
        if contacto is None:
            contacto = _upsert_contacto_telegram(
                db=db,
                chat_id=int(chat_id),
                telegram_user_id=int(telegram_user_id),
                nombres=nombres,
            )
        registro = _consulta_barrido_activa_por_contacto(db, contacto.id)
        if registro is None:
            registro = TelegramConsulta(
                contacto_id=contacto.id,
                usuario_id=contacto.usuario_id,
                tipo_consulta=TIPO_BARRIDO,
                consulta="Ubicacion recibida para barrido",
                estado="PROCESANDO",
            )
            db.add(registro)

        parametros = dict(registro.parametros or {})
        parametros["telefono"] = contacto.telefono
        parametros["ubicacion_pendiente"] = {
            "latitud": float(location["latitude"]),
            "longitud": float(location["longitude"]),
        }
        registro.parametros = parametros
        registro.estado = "PROCESANDO"
        db.commit()
        db.refresh(registro)
        _responder_si_es_posible(
            sender,
            int(chat_id),
            "Ubicacion recibida. Ahora seleccione el nivel de lluvia:\n\n1) Debil\n2) Moderado\n3) Fuerte\n4) Muy fuerte",
        )
        return TelegramWebhookRespuesta(
            estado="UBICACION_RECIBIDA",
            mensaje="Ubicacion guardada temporalmente para el barrido.",
            contacto_id=contacto.id,
            telefono=contacto.telefono,
            chat_id=contacto.chat_id,
        )

    nivel = MAPA_NIVELES_LLUVIAS.get(texto.strip())
    if nivel:
        contacto = _contacto_por_chat_id(db, int(chat_id))
        if contacto is None:
            _responder_si_es_posible(
                sender,
                int(chat_id),
                "Primero debe registrarse enviando su numero de telefono institucional.",
            )
            return TelegramWebhookRespuesta(
                estado="CONTACTO_NO_REGISTRADO",
                mensaje="No existe contacto activo para este chat_id.",
                chat_id=int(chat_id),
            )

        registro = _consulta_barrido_activa_por_contacto(db, contacto.id)
        ubicacion = (registro.parametros or {}).get("ubicacion_pendiente") if registro else None
        if not ubicacion:
            _responder_si_es_posible(
                sender,
                int(chat_id),
                "Primero comparta su ubicacion actual usando Telegram.",
            )
            return TelegramWebhookRespuesta(
                estado="UBICACION_REQUERIDA",
                mensaje="Se recibio el nivel, pero no hay ubicacion pendiente.",
                contacto_id=contacto.id,
                telefono=contacto.telefono,
                chat_id=contacto.chat_id,
            )

        registro, barrido, nivel_evento = _guardar_barrido(
            db=db,
            contacto=contacto,
            nivel=nivel,
            latitud=float(ubicacion["latitud"]),
            longitud=float(ubicacion["longitud"]),
            registro=registro,
            observacion=f"Nivel recibido por Telegram: {texto}",
        )
        _responder_si_es_posible(sender, int(chat_id), "Barrido guardado correctamente.")
        return TelegramWebhookRespuesta(
            estado="BARRIDO_REGISTRADO",
            mensaje=f"Barrido guardado con nivel {nivel_evento.nombre}.",
            contacto_id=contacto.id,
            telefono=contacto.telefono,
            chat_id=contacto.chat_id,
        )

    telefono = _extraer_telefono_de_mensaje(message, texto)
    if telefono:
        contacto = _upsert_contacto_telegram(
            db=db,
            chat_id=int(chat_id),
            telegram_user_id=int(telegram_user_id),
            nombres=nombres,
            telefono=telefono,
        )
        _responder_si_es_posible(sender, int(chat_id), "Registro guardado correctamente.")
        return TelegramWebhookRespuesta(
            estado="REGISTRADO",
            mensaje="Telefono vinculado al chat_id.",
            contacto_id=contacto.id,
            telefono=contacto.telefono,
            chat_id=contacto.chat_id,
        )

    contacto = _upsert_contacto_telegram(
        db=db,
        chat_id=int(chat_id),
        telegram_user_id=int(telegram_user_id),
        nombres=nombres,
    )
    _responder_si_es_posible(
        sender,
        int(chat_id),
        "No pude reconocer el numero. Envie el telefono con codigo de pais. Ejemplo: +593987223658",
    )
    return TelegramWebhookRespuesta(
        estado="TELEFONO_REQUERIDO",
        mensaje="Mensaje recibido, pero no contiene un telefono valido.",
        contacto_id=contacto.id,
        telefono=contacto.telefono,
        chat_id=contacto.chat_id,
    )


@router.post(
    "/boletines",
    response_model=EnvioFlujoRespuesta,
    status_code=status.HTTP_201_CREATED,
    tags=["boletines"],
    summary="Crear envio de boletin diario",
)
def crear_boletin(
    payload: CrearBoletinRequest,
    db: Session = Depends(get_db),
    sender: TelegramSender = Depends(get_telegram_sender),
) -> EnvioFlujoRespuesta:
    contactos = _contactos_activos_por_telefono(db, payload.telefonos)
    codigo = _codigo("BOLETIN", payload.codigo, payload.fecha_boletin)
    registros = []
    for contacto in contactos:
        titulo = payload.titulo or "Boletin diario"
        texto = f"{titulo}\n{payload.url_boletin}"
        registro = TelegramConsulta(
            contacto_id=contacto.id,
            usuario_id=payload.usuario_id or contacto.usuario_id,
            tipo_consulta=TIPO_BOLETIN,
            codigo=codigo,
            consulta=texto,
            parametros={
                "canal": "TELEGRAM",
                "url_boletin": str(payload.url_boletin),
                "telefono": contacto.telefono,
                "titulo": payload.titulo,
                "fecha_boletin": (payload.fecha_boletin or date.today()).isoformat(),
            },
        )
        _marcar_envio(registro, sender, contacto.chat_id, texto)
        registros.append(registro)
    db.add_all(registros)
    db.commit()
    for registro in registros:
        db.refresh(registro)
    return _respuesta_envio(codigo, registros)


@router.post(
    "/barridos/solicitudes",
    response_model=EnvioFlujoRespuesta,
    status_code=status.HTTP_201_CREATED,
    tags=["barridos"],
    summary="Crear solicitud diaria de barrido a GAD",
)
def solicitar_barrido(
    payload: SolicitarBarridoRequest,
    db: Session = Depends(get_db),
    sender: TelegramSender = Depends(get_telegram_sender),
) -> EnvioFlujoRespuesta:
    contactos = _contactos_activos_por_telefono(db, payload.telefonos)
    fecha_barrido = payload.fecha_barrido or date.today()
    codigo = _codigo("BARRIDO", payload.codigo, fecha_barrido)
    mensaje = payload.mensaje or "Indique el nivel de lluvia: DEBIL, MODERADO, FUERTE o MUY_FUERTE."
    registros = []
    for contacto in contactos:
        registro = TelegramConsulta(
            contacto_id=contacto.id,
            usuario_id=payload.usuario_id or contacto.usuario_id,
            tipo_consulta=TIPO_BARRIDO,
            codigo=codigo,
            consulta=mensaje,
            parametros={
                "canal": "TELEGRAM",
                "telefono": contacto.telefono,
                "fecha_barrido": fecha_barrido.isoformat(),
                "opciones": [nivel.value for nivel in NivelLluvia],
            },
        )
        _marcar_envio(registro, sender, contacto.chat_id, mensaje)
        registros.append(registro)
    db.add_all(registros)
    db.commit()
    for registro in registros:
        db.refresh(registro)
    return _respuesta_envio(codigo, registros)


@router.post(
    "/barridos/respuestas",
    response_model=BarridoGuardadoRespuesta,
    status_code=status.HTTP_201_CREATED,
    tags=["barridos"],
    summary="Registrar respuesta de barrido enviada por Telegram",
)
def registrar_respuesta_barrido(
    payload: RegistrarBarridoRequest,
    db: Session = Depends(get_db),
) -> BarridoGuardadoRespuesta:
    contacto = _contactos_activos_por_telefono(db, [payload.telefono])[0]
    filtros = [
        TelegramConsulta.contacto_id == contacto.id,
        TelegramConsulta.tipo_consulta == TIPO_BARRIDO,
        TelegramConsulta.estado.in_(["PENDIENTE", "PROCESANDO"]),
    ]
    if payload.codigo:
        filtros.append(TelegramConsulta.codigo == payload.codigo)

    registro = db.scalars(
        select(TelegramConsulta).where(*filtros).order_by(TelegramConsulta.fecha_consulta.desc())
    ).first()
    registro, barrido, nivel_evento = _guardar_barrido(
        db=db,
        contacto=contacto,
        nivel=payload.nivel_lluvia,
        latitud=payload.latitud,
        longitud=payload.longitud,
        registro=registro,
        observacion=payload.observacion,
    )
    if payload.codigo and not registro.codigo:
        registro.codigo = payload.codigo
        db.commit()
        db.refresh(registro)

    return BarridoGuardadoRespuesta(
        id=registro.id,
        barrido_id=barrido.id,
        telefono=contacto.telefono,
        codigo=registro.codigo,
        estado=registro.estado,
        nivel_lluvia=payload.nivel_lluvia,
        nivel_id=nivel_evento.id,
        latitud=float(barrido.latitud),
        longitud=float(barrido.longitud),
    )


@router.post(
    "/eventos/seguimientos",
    response_model=EnvioFlujoRespuesta,
    status_code=status.HTTP_201_CREATED,
    tags=["seguimiento eventos"],
    summary="Crear seguimiento de evento adverso",
)
def crear_seguimiento_evento(
    payload: CrearSeguimientoEventoRequest,
    db: Session = Depends(get_db),
    sender: TelegramSender = Depends(get_telegram_sender),
) -> EnvioFlujoRespuesta:
    contactos = _contactos_activos_por_telefono(db, payload.telefonos)
    registros = []
    texto = payload.mensaje or payload.descripcion
    for contacto in contactos:
        registro = TelegramConsulta(
            contacto_id=contacto.id,
            usuario_id=payload.usuario_id or contacto.usuario_id,
            tipo_consulta=TIPO_SEGUIMIENTO,
            codigo=payload.evento_codigo,
            consulta=texto,
            parametros={
                "canal": "TELEGRAM",
                "telefono": contacto.telefono,
                "evento_codigo": payload.evento_codigo,
                "descripcion": payload.descripcion,
                "fecha_inicio": payload.fecha_inicio.isoformat() if payload.fecha_inicio else None,
                "fecha_fin": payload.fecha_fin.isoformat() if payload.fecha_fin else None,
                "enviar_correo": payload.enviar_correo,
            },
        )
        _marcar_envio(registro, sender, contacto.chat_id, texto)
        registros.append(registro)
    db.add_all(registros)
    db.commit()
    for registro in registros:
        db.refresh(registro)
    return _respuesta_envio(payload.evento_codigo, registros)
