import json
import re
from decimal import Decimal
from datetime import date, datetime, timezone
from typing import Any
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import CatalogoNivelEvento, TelegramBarrido, TelegramConsulta, TelegramContacto, TelegramEvento
from app.schemas import (
    BarridoGuardadoRespuesta,
    CrearBoletinRequest,
    CrearSeguimientoEventoRequest,
    EnviarReporteLluviaGraficoRequest,
    EnviarReporteLluviaGraficoRespuesta,
    EnvioFlujoRespuesta,
    FotoEventoRespuesta,
    MAPA_NIVELES_LLUVIAS,
    NivelLluvia,
    NivelLluviaResumenRespuesta,
    RegistrarBarridoRequest,
    ReporteLluviaRespuesta,
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
TIPO_SCRIPT_AUTH = "SCRIPT_AUTH"
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
MENSAJE_SOLICITAR_UBICACION = (
    "Para registrar el barrido, active el GPS del celular y permita el acceso a ubicacion en Telegram. "
    "Luego presione Compartir ubicacion."
)
MENSAJE_SOLICITAR_UBICACION_EVENTO = (
    "Para registrar el evento, active el GPS del celular y permita el acceso a ubicacion en Telegram. "
    "Luego presione Compartir ubicacion."
)
MENSAJE_UBICACION_BARRIDO_REQUERIDA = (
    "Aun falta compartir la ubicacion del barrido. Active el GPS y use el boton Compartir ubicacion."
)
MENSAJE_UBICACION_EVENTO_REQUERIDA = (
    "Aun falta compartir la ubicacion del evento. Active el GPS y use el boton Compartir ubicacion."
)
MENSAJE_MENU_PRINCIPAL = "Bienvenido. Seleccione una opcion:"
OPCION_REPORTE_BARRIDO = "Reporte de barrido"
OPCION_REPORTE_EVENTO = "Reporte de evento"
ETIQUETAS_NIVELES_LLUVIA = {
    NivelLluvia.debil.value: "Debil",
    NivelLluvia.moderado.value: "Moderado",
    NivelLluvia.fuerte.value: "Fuerte",
    NivelLluvia.muy_fuerte.value: "Muy fuerte",
}
CALLBACK_REPORTE_BARRIDO = "REPORTE_BARRIDO"
CALLBACK_REPORTE_EVENTO = "REPORTE_EVENTO"
MENSAJE_MENU_SCRIPTS = "Seleccione el script que desea ejecutar:"
OPCION_SCRIPT_BARRIDO_LLUVIA = "Ejecutar script de barridos lluvia"
OPCION_SCRIPT_BARRIDO_RIOS = "Ejecutar script de barridos rios"
OPCION_SCRIPT_BARRIDO_SISMOS = "Ejecutar script de barridos sismos"
OPCION_SCRIPT_BARRIDO_CENIZA = "Ejecutar script de barridos ceniza"
CALLBACK_SCRIPT_BARRIDO_LLUVIA = "SCRIPT_BARRIDO_LLUVIA"
CALLBACK_SCRIPT_BARRIDO_RIOS = "SCRIPT_BARRIDO_RIOS"
CALLBACK_SCRIPT_BARRIDO_SISMOS = "SCRIPT_BARRIDO_SISMOS"
CALLBACK_SCRIPT_BARRIDO_CENIZA = "SCRIPT_BARRIDO_CENIZA"
SCRIPT_BARRIDO_LLUVIA_TELEFONOS = ["+593984374917", "0987223658"]
SCRIPT_BARRIDO_LLUVIA_CODIGO = "BARRIDO-AUTO"
SCRIPT_BARRIDO_LLUVIA_MENSAJE = "Recordatorio de reporte de barrido: enviar su ubicacion y nivel de lluvia."
SCRIPT_ADMIN_TELEGRAM_USER_IDS = {6869758976}
SCRIPT_PASSCODE = "Sngre.2026"
SCRIPT_MAX_PASSCODE_INTENTOS = 3
QUICKCHART_URL = "https://quickchart.io/chart"
MEDIA_TYPE_POR_EXTENSION = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
}


def _codigo(prefix: str, valor: str | None, fecha: date | None = None) -> str:
    if valor:
        return valor
    return f"{prefix}-{(fecha or date.today()).isoformat()}"


def _media_type_desde_file_path(file_path: str) -> str:
    path = file_path.lower()
    for extension, media_type in MEDIA_TYPE_POR_EXTENSION.items():
        if path.endswith(extension):
            return media_type
    return "application/octet-stream"


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


def _teclado_menu_scripts() -> dict[str, Any]:
    return {
        "inline_keyboard": [
            [{"text": OPCION_SCRIPT_BARRIDO_LLUVIA, "callback_data": CALLBACK_SCRIPT_BARRIDO_LLUVIA}],
            [{"text": OPCION_SCRIPT_BARRIDO_RIOS, "callback_data": CALLBACK_SCRIPT_BARRIDO_RIOS}],
            [{"text": OPCION_SCRIPT_BARRIDO_SISMOS, "callback_data": CALLBACK_SCRIPT_BARRIDO_SISMOS}],
            [{"text": OPCION_SCRIPT_BARRIDO_CENIZA, "callback_data": CALLBACK_SCRIPT_BARRIDO_CENIZA}],
        ],
    }


def _mostrar_menu_principal_si_es_posible(sender: TelegramSender | None, chat_id: int) -> None:
    _responder_si_es_posible(
        sender,
        chat_id,
        MENSAJE_MENU_PRINCIPAL,
        reply_markup=_teclado_menu_principal(),
    )


def _mostrar_menu_scripts_si_es_posible(sender: TelegramSender | None, chat_id: int) -> None:
    _responder_si_es_posible(
        sender,
        chat_id,
        MENSAJE_MENU_SCRIPTS,
        reply_markup=_teclado_menu_scripts(),
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


def _es_comando_scripts(texto: str) -> bool:
    normalizado = _texto_normalizado(texto)
    return normalizado.startswith("/scripts") or normalizado == "scripts"


def _es_comando_reporte_lluvia(texto: str) -> bool:
    normalizado = _texto_normalizado(texto)
    return normalizado in {"/reporte_lluvia", "reporte lluvia"}


def _es_comando_reporte_lluvia_grafico(texto: str) -> bool:
    normalizado = _texto_normalizado(texto)
    return normalizado in {"/reporte_lluvia_grafico", "reporte lluvia grafico"}


def _puede_ejecutar_scripts(telegram_user_id: int) -> bool:
    return telegram_user_id in SCRIPT_ADMIN_TELEGRAM_USER_IDS


def _iniciar_auth_scripts(db: Session, contacto: TelegramContacto, sender: TelegramSender | None) -> None:
    registro = _consulta_activa_por_contacto_y_tipo(db, contacto.id, TIPO_SCRIPT_AUTH)
    if registro is None:
        registro = TelegramConsulta(
            contacto_id=contacto.id,
            usuario_id=contacto.usuario_id,
            tipo_consulta=TIPO_SCRIPT_AUTH,
            consulta="Autenticacion para ejecutar scripts",
            parametros={"flujo": TIPO_SCRIPT_AUTH, "intentos": 0},
            estado="PROCESANDO",
        )
        db.add(registro)
    else:
        parametros = dict(registro.parametros or {})
        parametros["intentos"] = 0
        registro.parametros = parametros
        registro.estado = "PROCESANDO"
    db.commit()
    _responder_si_es_posible(sender, contacto.chat_id, "Ingrese el passcode para ejecutar scripts.")


def _validar_passcode_scripts(
    db: Session,
    contacto: TelegramContacto,
    texto: str,
    sender: TelegramSender | None,
) -> TelegramWebhookRespuesta | None:
    registro = _consulta_activa_por_contacto_y_tipo(db, contacto.id, TIPO_SCRIPT_AUTH)
    if registro is None:
        return None

    if texto.strip() != SCRIPT_PASSCODE:
        parametros = dict(registro.parametros or {})
        intentos = int(parametros.get("intentos") or 0) + 1
        parametros["intentos"] = intentos
        registro.parametros = parametros

        if intentos >= SCRIPT_MAX_PASSCODE_INTENTOS:
            registro.estado = "COMPLETADA"
            registro.fecha_respuesta = datetime.now(timezone.utc)
            registro.respuesta = {"autenticado": False, "intentos": intentos}
            db.commit()
            _responder_si_es_posible(
                sender,
                contacto.chat_id,
                "Passcode incorrecto. Se alcanzo el maximo de intentos.",
            )
            return TelegramWebhookRespuesta(
                estado="PASSCODE_SCRIPT_BLOQUEADO",
                mensaje="Se alcanzo el maximo de intentos de passcode.",
                contacto_id=contacto.id,
                telefono=contacto.telefono,
                chat_id=contacto.chat_id,
            )

        db.commit()
        restantes = SCRIPT_MAX_PASSCODE_INTENTOS - intentos
        _responder_si_es_posible(
            sender,
            contacto.chat_id,
            f"Passcode incorrecto. Intente nuevamente. Intentos restantes: {restantes}.",
        )
        return TelegramWebhookRespuesta(
            estado="PASSCODE_SCRIPT_INVALIDO",
            mensaje="Passcode incorrecto.",
            contacto_id=contacto.id,
            telefono=contacto.telefono,
            chat_id=contacto.chat_id,
        )

    registro.estado = "COMPLETADA"
    registro.fecha_respuesta = datetime.now(timezone.utc)
    registro.respuesta = {"autenticado": True}
    db.commit()
    _mostrar_menu_scripts_si_es_posible(sender, contacto.chat_id)
    return TelegramWebhookRespuesta(
        estado="MENU_SCRIPTS",
        mensaje="Passcode validado. Se mostro el menu de scripts.",
        contacto_id=contacto.id,
        telefono=contacto.telefono,
        chat_id=contacto.chat_id,
    )


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
    codigo: str | None = None,
    mensaje: str | None = None,
    fecha_barrido: date | None = None,
    usuario_id: int | None = None,
) -> None:
    registro = _consulta_barrido_activa_por_contacto(db, contacto.id)
    if registro is None:
        registro = TelegramConsulta(
            contacto_id=contacto.id,
            usuario_id=usuario_id or contacto.usuario_id,
            tipo_consulta=TIPO_BARRIDO,
            codigo=codigo,
            consulta=mensaje or "Reporte de barrido iniciado desde Telegram",
            parametros={
                "canal": "TELEGRAM",
                "flujo": FLUJO_REPORTE_BARRIDO,
                "telefono": contacto.telefono,
                "fecha_barrido": fecha_barrido.isoformat() if fecha_barrido else None,
            },
            estado="PROCESANDO",
        )
        db.add(registro)
    else:
        registro.usuario_id = usuario_id or registro.usuario_id or contacto.usuario_id
        registro.codigo = codigo or registro.codigo
        registro.consulta = mensaje or registro.consulta or "Reporte de barrido iniciado desde Telegram"
        parametros = dict(registro.parametros or {})
        parametros["flujo"] = FLUJO_REPORTE_BARRIDO
        parametros["telefono"] = contacto.telefono
        parametros["fecha_barrido"] = fecha_barrido.isoformat() if fecha_barrido else parametros.get("fecha_barrido")
        registro.parametros = parametros
        registro.estado = "PROCESANDO"
    db.commit()
    if mensaje:
        _responder_si_es_posible(
            sender,
            contacto.chat_id,
            mensaje,
            reply_markup=_teclado_solicitar_ubicacion(),
        )
    else:
        _solicitar_ubicacion_si_es_posible(sender, contacto.chat_id)


def _ejecutar_script_barrido_lluvia(
    db: Session,
    sender: TelegramSender | None,
) -> tuple[int, list[str]]:
    telefonos = list(dict.fromkeys(SCRIPT_BARRIDO_LLUVIA_TELEFONOS))
    contactos = list(
        db.scalars(
            select(TelegramContacto).where(
                TelegramContacto.telefono.in_(telefonos),
                TelegramContacto.activo.is_(True),
            )
        )
    )
    telefonos_encontrados = {contacto.telefono for contacto in contactos}
    telefonos_faltantes = [telefono for telefono in telefonos if telefono not in telefonos_encontrados]
    fecha_barrido = date.today()
    for contacto in contactos:
        _iniciar_reporte_barrido(
            db=db,
            contacto=contacto,
            sender=sender,
            codigo=SCRIPT_BARRIDO_LLUVIA_CODIGO,
            mensaje=SCRIPT_BARRIDO_LLUVIA_MENSAJE,
            fecha_barrido=fecha_barrido,
        )
    return len(contactos), telefonos_faltantes


def _obtener_reporte_lluvia(db: Session) -> ReporteLluviaRespuesta:
    conteos = {nivel.value: 0 for nivel in NivelLluvia}
    rows = db.execute(
        select(CatalogoNivelEvento.nombre, func.count(TelegramBarrido.id))
        .join(TelegramBarrido, TelegramBarrido.nivel_id == CatalogoNivelEvento.id)
        .where(TelegramBarrido.activo.is_(True))
        .group_by(CatalogoNivelEvento.nombre)
    ).all()
    for nombre, total in rows:
        if nombre in conteos:
            conteos[str(nombre)] = int(total)

    return ReporteLluviaRespuesta(
        total=sum(conteos.values()),
        niveles=[
            NivelLluviaResumenRespuesta(
                nivel=nivel,
                etiqueta=ETIQUETAS_NIVELES_LLUVIA[nivel.value],
                cantidad=conteos[nivel.value],
            )
            for nivel in NivelLluvia
        ],
    )


def _formatear_reporte_lluvia(reporte: ReporteLluviaRespuesta) -> str:
    lineas = [
        "Reporte de barridos de lluvia",
        f"Total de reportes: {reporte.total}",
        "",
        "Intensidad por tipo:",
    ]
    for nivel in reporte.niveles:
        lineas.append(f"- {nivel.etiqueta}: {nivel.cantidad}")
    return "\n".join(lineas)


def _crear_url_grafico_reporte_lluvia(
    reporte: ReporteLluviaRespuesta,
    titulo: str | None = None,
) -> str:
    chart_config = {
        "type": "bar",
        "data": {
            "labels": [nivel.etiqueta for nivel in reporte.niveles],
            "datasets": [
                {
                    "label": "Cantidad",
                    "data": [nivel.cantidad for nivel in reporte.niveles],
                    "backgroundColor": ["#7dd3fc", "#38bdf8", "#fb923c", "#ef4444"],
                    "borderColor": ["#0284c7", "#0369a1", "#ea580c", "#b91c1c"],
                    "borderWidth": 1,
                }
            ],
        },
        "options": {
            "title": {
                "display": True,
                "text": titulo or "Reporte de barridos de lluvia",
                "fontSize": 18,
            },
            "legend": {"display": False},
            "plugins": {
                "datalabels": {
                    "anchor": "end",
                    "align": "top",
                    "color": "#111827",
                    "font": {"weight": "bold"},
                }
            },
            "scales": {
                "yAxes": [
                    {
                        "ticks": {
                            "beginAtZero": True,
                            "precision": 0,
                        }
                    }
                ]
            },
        },
    }
    query = urlencode(
        {
            "width": 900,
            "height": 520,
            "format": "png",
            "c": json.dumps(chart_config, separators=(",", ":")),
        }
    )
    return f"{QUICKCHART_URL}?{query}"


def _enviar_grafico_reporte_lluvia(
    db: Session,
    sender: TelegramSender,
    chat_id: int,
    titulo: str | None = None,
) -> EnviarReporteLluviaGraficoRespuesta:
    reporte = _obtener_reporte_lluvia(db)
    chart_url = _crear_url_grafico_reporte_lluvia(reporte, titulo)
    caption = f"Reporte de barridos de lluvia. Total: {reporte.total}"
    try:
        telegram_response = sender.send_photo(
            chat_id=chat_id,
            photo=chart_url,
            caption=caption,
        )
    except TelegramDeliveryError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="No se pudo enviar el grafico por Telegram.",
        ) from exc

    return EnviarReporteLluviaGraficoRespuesta(
        chat_id=chat_id,
        chart_url=chart_url,
        telegram=telegram_response,
    )


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
    media_group_id: str | None = None,
) -> None:
    parametros = dict(registro.parametros or {})
    parametros["foto"] = {
        "file_id": str(foto["file_id"]),
        "file_unique_id": foto.get("file_unique_id"),
    }
    if media_group_id:
        parametros["media_group_id"] = media_group_id
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
            MENSAJE_UBICACION_BARRIDO_REQUERIDA,
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

    if str(data).startswith("SCRIPT_") and not _puede_ejecutar_scripts(int(telegram_user_id)):
        _responder_si_es_posible(sender, int(chat_id), "No tiene permisos para ejecutar scripts.")
        return TelegramWebhookRespuesta(
            estado="SCRIPT_NO_AUTORIZADO",
            mensaje="El usuario no tiene permisos para ejecutar scripts.",
            contacto_id=contacto.id,
            telefono=contacto.telefono,
            chat_id=contacto.chat_id,
        )

    if data == CALLBACK_REPORTE_BARRIDO:
        return _seleccionar_reporte_barrido(db, contacto, sender)
    if data == CALLBACK_REPORTE_EVENTO:
        return _seleccionar_reporte_evento(db, contacto, sender)
    if data == CALLBACK_SCRIPT_BARRIDO_LLUVIA:
        total, faltantes = _ejecutar_script_barrido_lluvia(db, sender)
        mensaje = f"Script de barridos lluvia ejecutado. Solicitudes enviadas: {total}."
        if faltantes:
            mensaje = f"{mensaje} Telefonos no encontrados o inactivos: {', '.join(faltantes)}."
        _responder_si_es_posible(sender, int(chat_id), mensaje)
        return TelegramWebhookRespuesta(
            estado="SCRIPT_BARRIDO_LLUVIA_EJECUTADO",
            mensaje=mensaje,
            contacto_id=contacto.id,
            telefono=contacto.telefono,
            chat_id=contacto.chat_id,
        )
    if data in {
        CALLBACK_SCRIPT_BARRIDO_RIOS,
        CALLBACK_SCRIPT_BARRIDO_SISMOS,
        CALLBACK_SCRIPT_BARRIDO_CENIZA,
    }:
        _responder_si_es_posible(sender, int(chat_id), "Ese script aun no esta configurado.")
        return TelegramWebhookRespuesta(
            estado="SCRIPT_NO_CONFIGURADO",
            mensaje="El script seleccionado aun no esta configurado.",
            contacto_id=contacto.id,
            telefono=contacto.telefono,
            chat_id=contacto.chat_id,
        )

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

    if _es_comando_scripts(texto):
        contacto = _upsert_contacto_telegram(
            db=db,
            chat_id=int(chat_id),
            telegram_user_id=int(telegram_user_id),
            nombres=nombres,
        )
        if not _puede_ejecutar_scripts(int(telegram_user_id)):
            _responder_si_es_posible(sender, int(chat_id), "No tiene permisos para ejecutar scripts.")
            return TelegramWebhookRespuesta(
                estado="SCRIPT_NO_AUTORIZADO",
                mensaje="El usuario no tiene permisos para ejecutar scripts.",
                contacto_id=contacto.id,
                telefono=contacto.telefono,
                chat_id=contacto.chat_id,
            )
        _iniciar_auth_scripts(db, contacto, sender)
        return TelegramWebhookRespuesta(
            estado="ESPERANDO_PASSCODE_SCRIPT",
            mensaje="Se solicito el passcode para ejecutar scripts.",
            contacto_id=contacto.id,
            telefono=contacto.telefono,
            chat_id=contacto.chat_id,
        )

    if _es_comando_reporte_lluvia(texto):
        contacto = _upsert_contacto_telegram(
            db=db,
            chat_id=int(chat_id),
            telegram_user_id=int(telegram_user_id),
            nombres=nombres,
        )
        reporte = _obtener_reporte_lluvia(db)
        texto_reporte = _formatear_reporte_lluvia(reporte)
        _responder_si_es_posible(sender, int(chat_id), texto_reporte)
        return TelegramWebhookRespuesta(
            estado="REPORTE_LLUVIA_GENERADO",
            mensaje=texto_reporte,
            contacto_id=contacto.id,
            telefono=contacto.telefono,
            chat_id=contacto.chat_id,
        )

    contacto_auth_scripts = _contacto_por_chat_id(db, int(chat_id))
    if contacto_auth_scripts and _puede_ejecutar_scripts(int(telegram_user_id)):
        respuesta_auth = _validar_passcode_scripts(db, contacto_auth_scripts, texto, sender)
        if respuesta_auth is not None:
            return respuesta_auth

    if _es_comando_reporte_lluvia_grafico(texto):
        contacto = _upsert_contacto_telegram(
            db=db,
            chat_id=int(chat_id),
            telegram_user_id=int(telegram_user_id),
            nombres=nombres,
        )
        if sender is None:
            return TelegramWebhookRespuesta(
                estado="TELEGRAM_NO_CONFIGURADO",
                mensaje="TELEGRAM_BOT_TOKEN no esta configurado.",
                contacto_id=contacto.id,
                telefono=contacto.telefono,
                chat_id=contacto.chat_id,
            )
        resultado = _enviar_grafico_reporte_lluvia(
            db=db,
            sender=sender,
            chat_id=int(chat_id),
            titulo="Reporte de barridos de lluvia",
        )
        return TelegramWebhookRespuesta(
            estado="REPORTE_LLUVIA_GRAFICO_ENVIADO",
            mensaje=resultado.chart_url,
            contacto_id=contacto.id,
            telefono=contacto.telefono,
            chat_id=contacto.chat_id,
        )

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
        media_group_id = message.get("media_group_id")
        media_group_id_texto = str(media_group_id) if media_group_id else None
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
        parametros_evento = dict(registro_evento.parametros or {}) if registro_evento else {}
        if (
            registro_evento is not None
            and media_group_id_texto
            and parametros_evento.get("media_group_id") == media_group_id_texto
            and paso_evento != PASO_EVENTO_FOTO
        ):
            return TelegramWebhookRespuesta(
                estado="FOTO_EVENTO_IGNORADA",
                mensaje="Foto adicional ignorada porque ya se guardo la primera imagen del album.",
                contacto_id=contacto.id,
                telefono=contacto.telefono,
                chat_id=contacto.chat_id,
            )
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

        _guardar_foto_evento(db, registro_evento, foto, media_group_id_texto)
        mensaje_foto = "Foto recibida. Describa brevemente el evento."
        if media_group_id_texto:
            mensaje_foto = (
                "Foto recibida. Si envio varias imagenes, solo se usara la primera. "
                "Describa brevemente el evento."
            )
        _responder_si_es_posible(sender, int(chat_id), mensaje_foto)
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
            _responder_si_es_posible(
                sender,
                int(chat_id),
                MENSAJE_UBICACION_EVENTO_REQUERIDA,
                reply_markup=_teclado_solicitar_ubicacion(),
            )
            return TelegramWebhookRespuesta(
                estado="UBICACION_EVENTO_REQUERIDA",
                mensaje="El reporte de evento espera ubicacion.",
                contacto_id=contacto_evento.id,
                telefono=contacto_evento.telefono,
                chat_id=contacto_evento.chat_id,
            )

        contacto_barrido = contacto_evento or _contacto_por_chat_id(db, int(chat_id))
        registro_barrido = (
            _consulta_barrido_activa_por_contacto(db, contacto_barrido.id) if contacto_barrido is not None else None
        )
        if (
            contacto_barrido
            and registro_barrido
            and (registro_barrido.parametros or {}).get("flujo") == FLUJO_REPORTE_BARRIDO
            and not (registro_barrido.parametros or {}).get("ubicacion_pendiente")
        ):
            _responder_si_es_posible(
                sender,
                int(chat_id),
                MENSAJE_UBICACION_BARRIDO_REQUERIDA,
                reply_markup=_teclado_solicitar_ubicacion(),
            )
            return TelegramWebhookRespuesta(
                estado="UBICACION_REQUERIDA",
                mensaje="El reporte de barrido espera ubicacion.",
                contacto_id=contacto_barrido.id,
                telefono=contacto_barrido.telefono,
                chat_id=contacto_barrido.chat_id,
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
                MENSAJE_UBICACION_BARRIDO_REQUERIDA,
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
    mensaje = payload.mensaje or MENSAJE_SOLICITAR_UBICACION
    registros = []
    for contacto in contactos:
        _iniciar_reporte_barrido(
            db=db,
            contacto=contacto,
            sender=sender,
            codigo=codigo,
            mensaje=mensaje,
            fecha_barrido=fecha_barrido,
            usuario_id=payload.usuario_id,
        )
        registro = _consulta_barrido_activa_por_contacto(db, contacto.id)
        if registro is None:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="No se pudo crear la solicitud de barrido.",
            )
        registros.append(registro)
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


@router.get(
    "/barridos/reporte-lluvia",
    response_model=ReporteLluviaRespuesta,
    tags=["barridos"],
    summary="Obtener resumen de barridos de lluvia por intensidad",
)
def obtener_reporte_lluvia(
    db: Session = Depends(get_db),
) -> ReporteLluviaRespuesta:
    return _obtener_reporte_lluvia(db)


@router.post(
    "/barridos/reporte-lluvia/grafico/enviar",
    response_model=EnviarReporteLluviaGraficoRespuesta,
    tags=["barridos"],
    summary="Enviar grafico de reporte de lluvia por Telegram",
)
def enviar_grafico_reporte_lluvia(
    payload: EnviarReporteLluviaGraficoRequest,
    db: Session = Depends(get_db),
    sender: TelegramSender = Depends(get_telegram_sender),
) -> EnviarReporteLluviaGraficoRespuesta:
    chat_id = payload.chat_id or next(iter(SCRIPT_ADMIN_TELEGRAM_USER_IDS))
    return _enviar_grafico_reporte_lluvia(
        db=db,
        sender=sender,
        chat_id=chat_id,
        titulo=payload.titulo,
    )


@router.get(
    "/eventos/fotos",
    response_model=list[FotoEventoRespuesta],
    tags=["eventos"],
    summary="Listar fotos de reportes de eventos",
)
def listar_fotos_eventos(
    request: Request,
    db: Session = Depends(get_db),
) -> list[FotoEventoRespuesta]:
    eventos = list(
        db.scalars(
            select(TelegramEvento)
            .where(
                TelegramEvento.activo.is_(True),
                TelegramEvento.foto_file_id.is_not(None),
            )
            .order_by(TelegramEvento.fecha_reporte.desc())
        )
    )
    return [
        FotoEventoRespuesta(
            evento_id=evento.id,
            contacto_id=evento.contacto_id,
            descripcion=evento.descripcion,
            latitud=float(evento.latitud),
            longitud=float(evento.longitud),
            fecha_reporte=evento.fecha_reporte.isoformat(),
            foto_url=str(request.url_for("obtener_foto_evento", evento_id=evento.id)),
            foto_file_unique_id=evento.foto_file_unique_id,
        )
        for evento in eventos
    ]


@router.get(
    "/eventos/{evento_id}/foto",
    response_class=Response,
    tags=["eventos"],
    summary="Ver foto de un reporte de evento",
    responses={
        200: {"content": {"image/jpeg": {}, "image/png": {}, "image/webp": {}}},
        404: {"description": "Evento no encontrado o sin foto."},
        502: {"description": "No se pudo obtener la foto desde Telegram."},
    },
)
def obtener_foto_evento(
    evento_id: int,
    db: Session = Depends(get_db),
    sender: TelegramSender = Depends(get_telegram_sender),
) -> Response:
    evento = db.get(TelegramEvento, evento_id)
    if evento is None or not evento.activo:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Evento no encontrado.",
        )
    if not evento.foto_file_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="El evento no tiene foto registrada.",
        )

    try:
        file_response = sender.get_file(evento.foto_file_id)
        file_path = file_response.get("result", {}).get("file_path")
        if not file_path:
            raise TelegramDeliveryError("Telegram no devolvio file_path para la foto.")
        content = sender.download_file(str(file_path))
    except TelegramDeliveryError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="No se pudo obtener la foto desde Telegram.",
        ) from exc

    return Response(
        content=content,
        media_type=_media_type_desde_file_path(str(file_path)),
        headers={"Cache-Control": "private, max-age=300"},
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
