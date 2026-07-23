import re
from decimal import Decimal
from datetime import date, datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import CatalogoNivelEvento, TelegramBarrido, TelegramConsulta, TelegramContacto, TelegramEvento
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
TIPO_REGISTRO_TELEFONO = "REGISTRO_TELEFONO"
TIPO_REPORTE_EVENTO = "REPORTE_EVENTO"
FLUJO_REPORTE_BARRIDO = "REPORTE_BARRIDO"
FLUJO_REPORTE_EVENTO = "REPORTE_EVENTO"
PASO_EVENTO_FOTO = "ESPERANDO_FOTO"
PASO_EVENTO_DESCRIPCION = "ESPERANDO_DESCRIPCION"
PASO_EVENTO_UBICACION = "ESPERANDO_UBICACION"
PATRON_TELEFONO = re.compile(r"^\+?\d{8,15}$")
PATRON_TELEFONO_EN_TEXTO = re.compile(r"\+?\d[\d\s().-]{6,}\d")
OPCIONES_ENCUESTA_LLUVIA = ["Debil", "Moderado", "Fuerte", "Muy fuerte"]
MAPA_OPCIONES_ENCUESTA_LLUVIA = {
    0: NivelLluvia.debil,
    1: NivelLluvia.moderado,
    2: NivelLluvia.fuerte,
    3: NivelLluvia.muy_fuerte,
}
MENSAJE_SELECCION_NIVEL_LLUVIA = (
    "Ubicacion recibida. Ahora seleccione el nivel de lluvia:\n\n"
    "1) Debil\n2) Moderado\n3) Fuerte\n4) Muy fuerte"
)
MENSAJE_SOLICITAR_UBICACION = "Hola. Comparta su ubicacion actual usando Telegram para registrar el barrido."
MENSAJE_SOLICITAR_UBICACION_EVENTO = "Comparta la ubicacion del evento usando Telegram."
MENSAJE_MENU_PRINCIPAL = "Bienvenido. Seleccione una opcion:"
OPCION_REPORTE_BARRIDO = "Reporte de barrido"
OPCION_REPORTE_EVENTO = "Reporte de evento"
CALLBACK_REPORTE_BARRIDO = "REPORTE_BARRIDO"
CALLBACK_REPORTE_EVENTO = "REPORTE_EVENTO"


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


def _responder_si_es_posible(
    sender: TelegramSender | None,
    chat_id: int,
    texto: str,
    reply_markup: dict[str, Any] | None = None,
) -> None:
    if sender is None:
        return
    try:
        sender.send_message(chat_id=chat_id, text=texto, reply_markup=reply_markup)
    except TelegramDeliveryError:
        return


def _contacto_por_chat_id(db: Session, chat_id: int) -> TelegramContacto | None:
    return db.scalars(
        select(TelegramContacto).where(
            TelegramContacto.chat_id == chat_id,
            TelegramContacto.activo.is_(True),
        )
    ).first()


def _contacto_por_telegram_user_id(db: Session, telegram_user_id: int) -> TelegramContacto | None:
    return db.scalars(
        select(TelegramContacto).where(
            TelegramContacto.telegram_user_id == telegram_user_id,
            TelegramContacto.activo.is_(True),
        )
    ).first()


def _consulta_activa_por_contacto_y_tipo(
    db: Session,
    contacto_id: int,
    tipo_consulta: str,
) -> TelegramConsulta | None:
    return db.scalars(
        select(TelegramConsulta)
        .where(
            TelegramConsulta.contacto_id == contacto_id,
            TelegramConsulta.tipo_consulta == tipo_consulta,
            TelegramConsulta.estado.in_(["PENDIENTE", "PROCESANDO"]),
        )
        .order_by(TelegramConsulta.fecha_consulta.desc())
    ).first()


def _consulta_evento_activa_por_contacto(db: Session, contacto_id: int) -> TelegramConsulta | None:
    return _consulta_activa_por_contacto_y_tipo(db, contacto_id, TIPO_REPORTE_EVENTO)


def _teclado_menu_principal() -> dict[str, Any]:
    return {
        "inline_keyboard": [
            [{"text": OPCION_REPORTE_BARRIDO, "callback_data": CALLBACK_REPORTE_BARRIDO}],
            [{"text": OPCION_REPORTE_EVENTO, "callback_data": CALLBACK_REPORTE_EVENTO}],
        ],
    }


def _mostrar_menu_principal_si_es_posible(sender: TelegramSender | None, chat_id: int) -> None:
    _responder_si_es_posible(
        sender,
        chat_id,
        MENSAJE_MENU_PRINCIPAL,
        reply_markup=_teclado_menu_principal(),
    )


def _responder_callback_si_es_posible(sender: TelegramSender | None, callback_query_id: str | None) -> None:
    if sender is None or not callback_query_id:
        return
    try:
        sender.answer_callback_query(callback_query_id=callback_query_id)
    except TelegramDeliveryError:
        return


def _teclado_solicitar_ubicacion() -> dict[str, Any]:
    return {
        "keyboard": [[{"text": "Compartir ubicacion", "request_location": True}]],
        "resize_keyboard": True,
        "one_time_keyboard": True,
    }


def _solicitar_ubicacion_si_es_posible(sender: TelegramSender | None, chat_id: int) -> None:
    _responder_si_es_posible(
        sender,
        chat_id,
        MENSAJE_SOLICITAR_UBICACION,
        reply_markup=_teclado_solicitar_ubicacion(),
    )


def _solicitar_ubicacion_evento_si_es_posible(sender: TelegramSender | None, chat_id: int) -> None:
    _responder_si_es_posible(
        sender,
        chat_id,
        MENSAJE_SOLICITAR_UBICACION_EVENTO,
        reply_markup=_teclado_solicitar_ubicacion(),
    )


def _iniciar_registro_telefono(
    db: Session,
    contacto: TelegramContacto,
    sender: TelegramSender | None,
) -> None:
    registro = _consulta_activa_por_contacto_y_tipo(db, contacto.id, TIPO_REGISTRO_TELEFONO)
    if registro is None:
        registro = TelegramConsulta(
            contacto_id=contacto.id,
            usuario_id=contacto.usuario_id,
            tipo_consulta=TIPO_REGISTRO_TELEFONO,
            consulta="Solicitud de registro de telefono institucional",
            parametros={"flujo": TIPO_REGISTRO_TELEFONO},
            estado="PROCESANDO",
        )
        db.add(registro)
    else:
        registro.estado = "PROCESANDO"
    db.commit()
    _responder_si_es_posible(
        sender,
        contacto.chat_id,
        "Por favor envie su numero de telefono institucional. Ejemplo: +593987223658",
    )


def _registrar_telefono_pendiente(
    db: Session,
    contacto: TelegramContacto,
    telegram_user_id: int,
    chat_id: int,
    nombres: str | None,
    telefono: str,
) -> tuple[str, str, TelegramContacto]:
    existente = db.scalars(select(TelegramContacto).where(TelegramContacto.telefono == telefono)).first()
    registro = _consulta_activa_por_contacto_y_tipo(db, contacto.id, TIPO_REGISTRO_TELEFONO)

    if existente and existente.id != contacto.id:
        if existente.telegram_user_id != telegram_user_id:
            if registro:
                registro.estado = "COMPLETADA"
                registro.fecha_respuesta = datetime.now(timezone.utc)
                registro.respuesta = {"telefono": telefono, "estado": "YA_REGISTRADO_OTRA_CUENTA"}
            db.commit()
            return "TELEFONO_YA_REGISTRADO", "Este numero ya esta registrado con otra cuenta de Telegram.", contacto
        contacto = existente

    contacto.telegram_user_id = telegram_user_id
    contacto.chat_id = chat_id
    contacto.nombres = nombres or contacto.nombres
    contacto.activo = True
    if contacto.telefono == telefono:
        mensaje = "Este numero ya esta registrado para su cuenta."
        estado = "TELEFONO_YA_REGISTRADO"
    else:
        contacto.telefono = telefono
        mensaje = "Registro guardado correctamente."
        estado = "REGISTRADO"

    if registro:
        registro.estado = "COMPLETADA"
        registro.fecha_respuesta = datetime.now(timezone.utc)
        registro.respuesta = {"telefono": telefono, "estado": estado}
    db.commit()
    return estado, mensaje, contacto


def _texto_normalizado(texto: str) -> str:
    return texto.strip().lower()


def _es_inicio_o_menu(texto: str) -> bool:
    normalizado = _texto_normalizado(texto)
    return normalizado.startswith("/start") or normalizado in {"hola", "menu", "inicio"}


def _es_comando_registro(texto: str) -> bool:
    normalizado = _texto_normalizado(texto)
    return normalizado.startswith("/registrar") or normalizado in {
        "registrar",
        "registrar numero",
        "registrar número",
    }


def _es_opcion_reporte_barrido(texto: str) -> bool:
    normalizado = _texto_normalizado(texto)
    return normalizado in {"1", "1.", "1)", "barrido", "reporte de barrido"}


def _es_opcion_reporte_evento(texto: str) -> bool:
    normalizado = _texto_normalizado(texto)
    return normalizado in {"2", "2.", "2)", "evento", "reporte de evento"}


def _iniciar_reporte_barrido(
    db: Session,
    contacto: TelegramContacto,
    sender: TelegramSender | None,
) -> None:
    registro = _consulta_barrido_activa_por_contacto(db, contacto.id)
    if registro is None:
        registro = TelegramConsulta(
            contacto_id=contacto.id,
            usuario_id=contacto.usuario_id,
            tipo_consulta=TIPO_BARRIDO,
            consulta="Reporte de barrido iniciado desde Telegram",
            parametros={
                "canal": "TELEGRAM",
                "flujo": FLUJO_REPORTE_BARRIDO,
                "telefono": contacto.telefono,
            },
            estado="PROCESANDO",
        )
        db.add(registro)
    else:
        parametros = dict(registro.parametros or {})
        parametros["flujo"] = FLUJO_REPORTE_BARRIDO
        parametros["telefono"] = contacto.telefono
        registro.parametros = parametros
        registro.estado = "PROCESANDO"
    db.commit()
    _solicitar_ubicacion_si_es_posible(sender, contacto.chat_id)


def _extraer_foto_de_mensaje(message: dict[str, Any]) -> dict[str, Any] | None:
    fotos = message.get("photo")
    if not isinstance(fotos, list) or not fotos:
        return None
    fotos_validas = [foto for foto in fotos if isinstance(foto, dict) and foto.get("file_id")]
    if not fotos_validas:
        return None
    return max(
        fotos_validas,
        key=lambda foto: (
            int(foto.get("file_size") or 0),
            int(foto.get("width") or 0) * int(foto.get("height") or 0),
        ),
    )


def _iniciar_reporte_evento(
    db: Session,
    contacto: TelegramContacto,
    sender: TelegramSender | None,
) -> None:
    registro = _consulta_evento_activa_por_contacto(db, contacto.id)
    if registro is None:
        registro = TelegramConsulta(
            contacto_id=contacto.id,
            usuario_id=contacto.usuario_id,
            tipo_consulta=TIPO_REPORTE_EVENTO,
            consulta="Reporte de evento iniciado desde Telegram",
            parametros={
                "canal": "TELEGRAM",
                "flujo": FLUJO_REPORTE_EVENTO,
                "paso": PASO_EVENTO_FOTO,
                "telefono": contacto.telefono,
            },
            estado="PROCESANDO",
        )
        db.add(registro)
    else:
        parametros = dict(registro.parametros or {})
        parametros.update(
            {
                "canal": "TELEGRAM",
                "flujo": FLUJO_REPORTE_EVENTO,
                "paso": PASO_EVENTO_FOTO,
                "telefono": contacto.telefono,
            }
        )
        parametros.pop("foto", None)
        parametros.pop("descripcion", None)
        parametros.pop("ubicacion", None)
        registro.parametros = parametros
        registro.estado = "PROCESANDO"
    db.commit()
    _responder_si_es_posible(sender, contacto.chat_id, "Envie una foto del evento.")


def _guardar_foto_evento(
    db: Session,
    registro: TelegramConsulta,
    foto: dict[str, Any],
) -> None:
    parametros = dict(registro.parametros or {})
    parametros["foto"] = {
        "file_id": str(foto["file_id"]),
        "file_unique_id": foto.get("file_unique_id"),
    }
    parametros["paso"] = PASO_EVENTO_DESCRIPCION
    registro.parametros = parametros
    registro.estado = "PROCESANDO"
    db.commit()


def _guardar_descripcion_evento(db: Session, registro: TelegramConsulta, descripcion: str) -> None:
    parametros = dict(registro.parametros or {})
    parametros["descripcion"] = descripcion
    parametros["paso"] = PASO_EVENTO_UBICACION
    registro.parametros = parametros
    registro.estado = "PROCESANDO"
    db.commit()


def _guardar_reporte_evento(
    db: Session,
    contacto: TelegramContacto,
    registro: TelegramConsulta,
    latitud: float,
    longitud: float,
) -> tuple[TelegramConsulta, TelegramEvento]:
    parametros = dict(registro.parametros or {})
    foto = parametros.get("foto") or {}
    descripcion = str(parametros.get("descripcion") or "").strip()
    if not foto.get("file_id") or not descripcion:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="El reporte de evento no tiene foto y descripcion completas.",
        )

    evento = TelegramEvento(
        contacto_id=contacto.id,
        descripcion=descripcion,
        foto_file_id=str(foto["file_id"]),
        foto_file_unique_id=foto.get("file_unique_id"),
        latitud=Decimal(str(latitud)),
        longitud=Decimal(str(longitud)),
    )
    db.add(evento)
    parametros["ubicacion"] = {"latitud": latitud, "longitud": longitud}
    parametros["paso"] = "COMPLETADO"
    registro.parametros = parametros
    registro.respuesta = {
        "canal": "TELEGRAM",
        "telefono": contacto.telefono,
        "evento_id": None,
        "descripcion": descripcion,
        "foto_file_id": str(foto["file_id"]),
        "foto_file_unique_id": foto.get("file_unique_id"),
        "latitud": latitud,
        "longitud": longitud,
    }
    registro.estado = "COMPLETADA"
    registro.fecha_respuesta = datetime.now(timezone.utc)
    db.commit()
    db.refresh(registro)
    db.refresh(evento)
    registro.respuesta = dict(registro.respuesta or {}) | {"evento_id": evento.id}
    db.commit()
    db.refresh(registro)
    return registro, evento


def _seleccionar_reporte_barrido(
    db: Session,
    contacto: TelegramContacto,
    sender: TelegramSender | None,
) -> TelegramWebhookRespuesta:
    if not contacto.telefono:
        _responder_si_es_posible(
            sender,
            contacto.chat_id,
            "Primero registre su numero institucional con /registrar.",
        )
        return TelegramWebhookRespuesta(
            estado="TELEFONO_REQUERIDO",
            mensaje="El contacto debe registrar su telefono antes del reporte de barrido.",
            contacto_id=contacto.id,
            telefono=contacto.telefono,
            chat_id=contacto.chat_id,
        )

    _iniciar_reporte_barrido(db, contacto, sender)
    return TelegramWebhookRespuesta(
        estado="REPORTE_BARRIDO_INICIADO",
        mensaje="Se solicito compartir ubicacion para el reporte de barrido.",
        contacto_id=contacto.id,
        telefono=contacto.telefono,
        chat_id=contacto.chat_id,
    )


def _seleccionar_reporte_evento(
    db: Session,
    contacto: TelegramContacto,
    sender: TelegramSender | None,
) -> TelegramWebhookRespuesta:
    if not contacto.telefono:
        _responder_si_es_posible(
            sender,
            contacto.chat_id,
            "Primero registre su numero institucional con /registrar.",
        )
        return TelegramWebhookRespuesta(
            estado="TELEFONO_REQUERIDO",
            mensaje="El contacto debe registrar su telefono antes del reporte de evento.",
            contacto_id=contacto.id,
            telefono=contacto.telefono,
            chat_id=contacto.chat_id,
        )

    _iniciar_reporte_evento(db, contacto, sender)
    return TelegramWebhookRespuesta(
        estado="REPORTE_EVENTO_INICIADO",
        mensaje="Se solicito la foto del evento.",
        contacto_id=contacto.id,
        telefono=contacto.telefono,
        chat_id=contacto.chat_id,
    )


def _enviar_encuesta_lluvia_si_es_posible(sender: TelegramSender | None, chat_id: int) -> None:
    if sender is None:
        return
    try:
        sender.send_poll(
            chat_id=chat_id,
            question="Seleccione el nivel de lluvia",
            options=OPCIONES_ENCUESTA_LLUVIA,
        )
    except TelegramDeliveryError:
        _responder_si_es_posible(sender, chat_id, MENSAJE_SELECCION_NIVEL_LLUVIA)


def _nivel_desde_respuesta_encuesta(poll_answer: dict[str, Any]) -> NivelLluvia | None:
    option_ids = poll_answer.get("option_ids")
    if not isinstance(option_ids, list) or not option_ids:
        return None
    try:
        option_id = int(option_ids[0])
    except (TypeError, ValueError):
        return None
    return MAPA_OPCIONES_ENCUESTA_LLUVIA.get(option_id)


def _recibir_respuesta_encuesta_lluvia(
    poll_answer: dict[str, Any],
    db: Session,
    sender: TelegramSender | None,
) -> TelegramWebhookRespuesta:
    user = poll_answer.get("user") or {}
    telegram_user_id = user.get("id")
    if telegram_user_id is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="La respuesta de encuesta no contiene poll_answer.user.id.",
        )

    nivel = _nivel_desde_respuesta_encuesta(poll_answer)
    if nivel is None:
        return TelegramWebhookRespuesta(
            estado="ENCUESTA_IGNORADA",
            mensaje="La respuesta de encuesta no contiene una opcion valida.",
        )

    contacto = _contacto_por_telegram_user_id(db, int(telegram_user_id))
    if contacto is None:
        return TelegramWebhookRespuesta(
            estado="CONTACTO_NO_REGISTRADO",
            mensaje="No existe contacto activo para este usuario de Telegram.",
        )

    registro = _consulta_barrido_activa_por_contacto(db, contacto.id)
    ubicacion = (registro.parametros or {}).get("ubicacion_pendiente") if registro else None
    if not ubicacion:
        _responder_si_es_posible(
            sender,
            contacto.chat_id,
            "Primero comparta su ubicacion actual usando Telegram.",
        )
        return TelegramWebhookRespuesta(
            estado="UBICACION_REQUERIDA",
            mensaje="Se recibio el nivel por encuesta, pero no hay ubicacion pendiente.",
            contacto_id=contacto.id,
            telefono=contacto.telefono,
            chat_id=contacto.chat_id,
        )

    option_id = int(poll_answer["option_ids"][0])
    registro, barrido, nivel_evento = _guardar_barrido(
        db=db,
        contacto=contacto,
        nivel=nivel,
        latitud=float(ubicacion["latitud"]),
        longitud=float(ubicacion["longitud"]),
        registro=registro,
        observacion=f"Nivel recibido por encuesta Telegram: {option_id + 1}",
    )
    _responder_si_es_posible(sender, contacto.chat_id, "Barrido guardado correctamente.")
    return TelegramWebhookRespuesta(
        estado="BARRIDO_REGISTRADO",
        mensaje=f"Barrido guardado con nivel {nivel_evento.nombre}.",
        contacto_id=contacto.id,
        telefono=contacto.telefono,
        chat_id=contacto.chat_id,
    )


def _recibir_callback_menu_principal(
    callback_query: dict[str, Any],
    db: Session,
    sender: TelegramSender | None,
) -> TelegramWebhookRespuesta:
    _responder_callback_si_es_posible(sender, callback_query.get("id"))

    origen = callback_query.get("from") or {}
    message = callback_query.get("message") or {}
    chat = message.get("chat") or {}
    data = callback_query.get("data")
    chat_id = chat.get("id")
    telegram_user_id = origen.get("id") or chat_id
    if chat_id is None or telegram_user_id is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="El callback no contiene message.chat.id.",
        )

    contacto = _upsert_contacto_telegram(
        db=db,
        chat_id=int(chat_id),
        telegram_user_id=int(telegram_user_id),
        nombres=_nombre_desde_update(origen, chat),
    )

    if data == CALLBACK_REPORTE_BARRIDO:
        return _seleccionar_reporte_barrido(db, contacto, sender)
    if data == CALLBACK_REPORTE_EVENTO:
        return _seleccionar_reporte_evento(db, contacto, sender)

    _mostrar_menu_principal_si_es_posible(sender, int(chat_id))
    return TelegramWebhookRespuesta(
        estado="CALLBACK_IGNORADO",
        mensaje="Callback recibido, pero no coincide con una opcion del menu.",
        contacto_id=contacto.id,
        telefono=contacto.telefono,
        chat_id=contacto.chat_id,
    )


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
    poll_answer = update.get("poll_answer")
    if isinstance(poll_answer, dict):
        return _recibir_respuesta_encuesta_lluvia(poll_answer, db, sender)

    callback_query = update.get("callback_query")
    if isinstance(callback_query, dict):
        return _recibir_callback_menu_principal(callback_query, db, sender)

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

    if _es_inicio_o_menu(texto):
        contacto = _upsert_contacto_telegram(
            db=db,
            chat_id=int(chat_id),
            telegram_user_id=int(telegram_user_id),
            nombres=nombres,
        )
        _mostrar_menu_principal_si_es_posible(sender, int(chat_id))
        return TelegramWebhookRespuesta(
            estado="MENU_PRINCIPAL",
            mensaje="Contacto inicial registrado. Se mostro el menu principal.",
            contacto_id=contacto.id,
            telefono=contacto.telefono,
            chat_id=contacto.chat_id,
        )

    if _es_comando_registro(texto):
        contacto = _upsert_contacto_telegram(
            db=db,
            chat_id=int(chat_id),
            telegram_user_id=int(telegram_user_id),
            nombres=nombres,
        )
        _iniciar_registro_telefono(db, contacto, sender)
        return TelegramWebhookRespuesta(
            estado="ESPERANDO_TELEFONO",
            mensaje="Se solicito el telefono institucional.",
            contacto_id=contacto.id,
            telefono=contacto.telefono,
            chat_id=contacto.chat_id,
        )

    if _es_opcion_reporte_barrido(texto):
        contacto = _upsert_contacto_telegram(
            db=db,
            chat_id=int(chat_id),
            telegram_user_id=int(telegram_user_id),
            nombres=nombres,
        )
        return _seleccionar_reporte_barrido(db, contacto, sender)

    if _es_opcion_reporte_evento(texto):
        contacto = _upsert_contacto_telegram(
            db=db,
            chat_id=int(chat_id),
            telegram_user_id=int(telegram_user_id),
            nombres=nombres,
        )
        return _seleccionar_reporte_evento(db, contacto, sender)

    foto = _extraer_foto_de_mensaje(message)
    if foto:
        contacto = _contacto_por_chat_id(db, int(chat_id))
        if contacto is None:
            contacto = _upsert_contacto_telegram(
                db=db,
                chat_id=int(chat_id),
                telegram_user_id=int(telegram_user_id),
                nombres=nombres,
            )
        if not contacto.telefono:
            _responder_si_es_posible(
                sender,
                int(chat_id),
                "Primero registre su numero institucional con /registrar.",
            )
            return TelegramWebhookRespuesta(
                estado="TELEFONO_REQUERIDO",
                mensaje="Se recibio foto, pero el contacto no tiene telefono registrado.",
                contacto_id=contacto.id,
                telefono=contacto.telefono,
                chat_id=contacto.chat_id,
            )

        registro_evento = _consulta_evento_activa_por_contacto(db, contacto.id)
        paso_evento = (registro_evento.parametros or {}).get("paso") if registro_evento else None
        if registro_evento is None or paso_evento != PASO_EVENTO_FOTO:
            _responder_si_es_posible(
                sender,
                int(chat_id),
                "Primero seleccione Reporte de evento en el menu.",
            )
            _mostrar_menu_principal_si_es_posible(sender, int(chat_id))
            return TelegramWebhookRespuesta(
                estado="FLUJO_REQUERIDO",
                mensaje="Se recibio foto, pero no hay reporte de evento esperando foto.",
                contacto_id=contacto.id,
                telefono=contacto.telefono,
                chat_id=contacto.chat_id,
            )

        _guardar_foto_evento(db, registro_evento, foto)
        _responder_si_es_posible(sender, int(chat_id), "Foto recibida. Describa brevemente el evento.")
        return TelegramWebhookRespuesta(
            estado="FOTO_EVENTO_RECIBIDA",
            mensaje="Foto guardada temporalmente para el reporte de evento.",
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
        if not contacto.telefono:
            _responder_si_es_posible(
                sender,
                int(chat_id),
                "Primero registre su numero institucional con /registrar.",
            )
            return TelegramWebhookRespuesta(
                estado="TELEFONO_REQUERIDO",
                mensaje="Se recibio ubicacion, pero el contacto no tiene telefono registrado.",
                contacto_id=contacto.id,
                telefono=contacto.telefono,
                chat_id=contacto.chat_id,
            )

        registro_evento = _consulta_evento_activa_por_contacto(db, contacto.id)
        paso_evento = (registro_evento.parametros or {}).get("paso") if registro_evento else None
        if registro_evento is not None:
            if paso_evento == PASO_EVENTO_UBICACION:
                registro_evento, evento = _guardar_reporte_evento(
                    db=db,
                    contacto=contacto,
                    registro=registro_evento,
                    latitud=float(location["latitude"]),
                    longitud=float(location["longitude"]),
                )
                _responder_si_es_posible(sender, int(chat_id), "Reporte de evento guardado correctamente.")
                _mostrar_menu_principal_si_es_posible(sender, int(chat_id))
                return TelegramWebhookRespuesta(
                    estado="REPORTE_EVENTO_GUARDADO",
                    mensaje=f"Reporte de evento guardado con id {evento.id}.",
                    contacto_id=contacto.id,
                    telefono=contacto.telefono,
                    chat_id=contacto.chat_id,
                )

            if paso_evento == PASO_EVENTO_FOTO:
                _responder_si_es_posible(sender, int(chat_id), "Primero envie una foto del evento.")
                return TelegramWebhookRespuesta(
                    estado="FOTO_EVENTO_REQUERIDA",
                    mensaje="Se recibio ubicacion, pero el reporte de evento espera una foto.",
                    contacto_id=contacto.id,
                    telefono=contacto.telefono,
                    chat_id=contacto.chat_id,
                )

            if paso_evento == PASO_EVENTO_DESCRIPCION:
                _responder_si_es_posible(sender, int(chat_id), "Primero describa brevemente el evento.")
                return TelegramWebhookRespuesta(
                    estado="DESCRIPCION_EVENTO_REQUERIDA",
                    mensaje="Se recibio ubicacion, pero el reporte de evento espera descripcion.",
                    contacto_id=contacto.id,
                    telefono=contacto.telefono,
                    chat_id=contacto.chat_id,
                )

        registro = _consulta_barrido_activa_por_contacto(db, contacto.id)
        if registro is None:
            _responder_si_es_posible(
                sender,
                int(chat_id),
                "Primero seleccione Reporte de barrido en el menu.",
            )
            _mostrar_menu_principal_si_es_posible(sender, int(chat_id))
            return TelegramWebhookRespuesta(
                estado="FLUJO_REQUERIDO",
                mensaje="Se recibio ubicacion, pero no hay reporte de barrido activo.",
                contacto_id=contacto.id,
                telefono=contacto.telefono,
                chat_id=contacto.chat_id,
            )

        parametros = dict(registro.parametros or {})
        parametros["telefono"] = contacto.telefono
        parametros["flujo"] = FLUJO_REPORTE_BARRIDO
        parametros["ubicacion_pendiente"] = {
            "latitud": float(location["latitude"]),
            "longitud": float(location["longitude"]),
        }
        registro.parametros = parametros
        registro.estado = "PROCESANDO"
        db.commit()
        db.refresh(registro)
        _enviar_encuesta_lluvia_si_es_posible(sender, int(chat_id))
        return TelegramWebhookRespuesta(
            estado="UBICACION_RECIBIDA",
            mensaje="Ubicacion guardada temporalmente para el barrido.",
            contacto_id=contacto.id,
            telefono=contacto.telefono,
            chat_id=contacto.chat_id,
        )

    if texto:
        contacto_evento = _contacto_por_chat_id(db, int(chat_id))
        registro_evento = (
            _consulta_evento_activa_por_contacto(db, contacto_evento.id) if contacto_evento is not None else None
        )
        paso_evento = (registro_evento.parametros or {}).get("paso") if registro_evento else None
        if contacto_evento and registro_evento and paso_evento == PASO_EVENTO_FOTO:
            _responder_si_es_posible(sender, int(chat_id), "Primero envie una foto del evento.")
            return TelegramWebhookRespuesta(
                estado="FOTO_EVENTO_REQUERIDA",
                mensaje="El reporte de evento espera una foto.",
                contacto_id=contacto_evento.id,
                telefono=contacto_evento.telefono,
                chat_id=contacto_evento.chat_id,
            )
        if contacto_evento and registro_evento and paso_evento == PASO_EVENTO_DESCRIPCION:
            _guardar_descripcion_evento(db, registro_evento, texto)
            _solicitar_ubicacion_evento_si_es_posible(sender, int(chat_id))
            return TelegramWebhookRespuesta(
                estado="DESCRIPCION_EVENTO_RECIBIDA",
                mensaje="Descripcion guardada temporalmente para el reporte de evento.",
                contacto_id=contacto_evento.id,
                telefono=contacto_evento.telefono,
                chat_id=contacto_evento.chat_id,
            )
        if contacto_evento and registro_evento and paso_evento == PASO_EVENTO_UBICACION:
            _solicitar_ubicacion_evento_si_es_posible(sender, int(chat_id))
            return TelegramWebhookRespuesta(
                estado="UBICACION_EVENTO_REQUERIDA",
                mensaje="El reporte de evento espera ubicacion.",
                contacto_id=contacto_evento.id,
                telefono=contacto_evento.telefono,
                chat_id=contacto_evento.chat_id,
            )

    nivel = MAPA_NIVELES_LLUVIAS.get(texto.strip())
    if nivel:
        contacto = _contacto_por_chat_id(db, int(chat_id))
        if contacto is None:
            _responder_si_es_posible(
                sender,
                int(chat_id),
                "Primero debe registrarse con /registrar.",
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
        contacto = _contacto_por_chat_id(db, int(chat_id))
        if contacto is None:
            contacto = _upsert_contacto_telegram(
                db=db,
                chat_id=int(chat_id),
                telegram_user_id=int(telegram_user_id),
                nombres=nombres,
            )

        registro_telefono = _consulta_activa_por_contacto_y_tipo(db, contacto.id, TIPO_REGISTRO_TELEFONO)
        if registro_telefono is None:
            _responder_si_es_posible(
                sender,
                int(chat_id),
                "Para registrar su numero, primero escriba /registrar.",
            )
            return TelegramWebhookRespuesta(
                estado="REGISTRO_REQUERIDO",
                mensaje="Se recibio telefono, pero no hay registro de telefono pendiente.",
                contacto_id=contacto.id,
                telefono=contacto.telefono,
                chat_id=contacto.chat_id,
            )

        estado, mensaje, contacto = _registrar_telefono_pendiente(
            db=db,
            contacto=contacto,
            telegram_user_id=int(telegram_user_id),
            chat_id=int(chat_id),
            nombres=nombres,
            telefono=telefono,
        )
        _responder_si_es_posible(sender, int(chat_id), mensaje)
        _mostrar_menu_principal_si_es_posible(sender, int(chat_id))
        return TelegramWebhookRespuesta(
            estado=estado,
            mensaje=mensaje,
            contacto_id=contacto.id,
            telefono=contacto.telefono,
            chat_id=contacto.chat_id,
        )

    contacto = _contacto_por_chat_id(db, int(chat_id))
    if contacto and contacto.telefono:
        _mostrar_menu_principal_si_es_posible(sender, int(chat_id))
        return TelegramWebhookRespuesta(
            estado="MENU_PRINCIPAL",
            mensaje="Contacto registrado. Se mostro el menu principal.",
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
        "No pude reconocer la opcion. Escriba hola para ver el menu o /registrar para registrar su numero.",
    )
    return TelegramWebhookRespuesta(
        estado="OPCION_NO_RECONOCIDA",
        mensaje="Mensaje recibido, pero no coincide con una opcion del bot.",
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
