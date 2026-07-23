from collections.abc import Generator
from contextlib import contextmanager
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.database import engine, get_db
from app.main import app
from app.telegram import get_optional_telegram_sender, get_telegram_sender


class FakeTelegramSender:
    def send_message(
        self,
        chat_id: int,
        text: str,
        reply_markup: dict[str, object] | None = None,
    ) -> dict[str, object]:
        result = {"chat_id": chat_id, "text": text}
        if reply_markup is not None:
            result["reply_markup"] = reply_markup
        return {"ok": True, "result": result}

    def send_poll(self, chat_id: int, question: str, options: list[str]) -> dict[str, object]:
        return {
            "ok": True,
            "result": {
                "chat_id": chat_id,
                "poll": {
                    "question": question,
                    "options": [{"text": option} for option in options],
                    "is_anonymous": False,
                },
            },
        }

    def answer_callback_query(self, callback_query_id: str) -> dict[str, object]:
        return {"ok": True, "result": True, "callback_query_id": callback_query_id}


def _asegurar_niveles(session: Session) -> None:
    total = session.execute(
        text(
            """
            SELECT count(*)
            FROM catalogo_niveles_evento
            WHERE nombre IN ('DEBIL', 'MODERADO', 'FUERTE', 'MUY_FUERTE')
              AND activo = true
            """
        )
    ).scalar_one()
    assert total == 4


@contextmanager
def _client_con_contacto() -> Generator[tuple[TestClient, str], None, None]:
    connection = engine.connect()
    transaction = connection.begin()
    session = Session(bind=connection, autoflush=False, expire_on_commit=False)
    telefono = f"+593000{uuid4().int % 1000000:06d}"

    try:
        _asegurar_niveles(session)
        session.execute(
            text(
                """
                INSERT INTO telegram_contactos
                    (telegram_user_id, chat_id, telefono, activo)
                VALUES
                    (:telegram_user_id, :chat_id, :telefono, true)
                RETURNING id
                """
            ),
            {
                "telegram_user_id": -900001,
                "chat_id": -900001,
                "telefono": telefono,
            },
        ).scalar_one()

        def override_get_db() -> Generator[Session, None, None]:
            yield session

        app.dependency_overrides[get_db] = override_get_db
        app.dependency_overrides[get_telegram_sender] = lambda: FakeTelegramSender()
        app.dependency_overrides[get_optional_telegram_sender] = lambda: FakeTelegramSender()
        yield TestClient(app), telefono
    finally:
        app.dependency_overrides.clear()
        session.close()
        transaction.rollback()
        connection.close()


def test_flujos_telegram_en_swagger() -> None:
    paths = TestClient(app).get("/openapi.json").json()["paths"]

    assert "/api/telegram/webhook" in paths
    assert "/api/telegram/boletines" in paths
    assert "/api/telegram/barridos/solicitudes" in paths
    assert "/api/telegram/barridos/respuestas" in paths
    assert "/api/telegram/eventos/seguimientos" in paths


def test_flujo_boletin_barrido_y_seguimiento() -> None:
    with _client_con_contacto() as (client, telefono):
        boletin = client.post(
            "/api/telegram/boletines",
            json={
                "telefonos": [telefono],
                "url_boletin": "https://example.com/boletin.pdf",
                "titulo": "Boletin diario",
                "codigo": "BOLETIN-TEST",
            },
        )
        assert boletin.status_code == 201
        assert boletin.json()["total"] == 1

        solicitud = client.post(
            "/api/telegram/barridos/solicitudes",
            json={"telefonos": [telefono], "codigo": "BARRIDO-TEST"},
        )
        assert solicitud.status_code == 201
        assert solicitud.json()["registros"][0]["estado"] == "COMPLETADA"

        respuesta = client.post(
            "/api/telegram/barridos/respuestas",
            json={
                "telefono": telefono,
                "codigo": "BARRIDO-TEST",
                "nivel_lluvia": "4",
                "latitud": -0.1806532,
                "longitud": -78.4678382,
                "observacion": "Lluvia sostenida",
            },
        )
        assert respuesta.status_code == 201
        assert respuesta.json()["estado"] == "COMPLETADA"
        assert respuesta.json()["nivel_lluvia"] == "MUY_FUERTE"
        assert respuesta.json()["barrido_id"] > 0
        assert respuesta.json()["nivel_id"] == 4

        seguimiento = client.post(
            "/api/telegram/eventos/seguimientos",
            json={
                "telefonos": [telefono],
                "evento_codigo": "EVT-TEST",
                "descripcion": "Seguimiento de deslizamiento",
            },
        )
        assert seguimiento.status_code == 201
        assert seguimiento.json()["codigo"] == "EVT-TEST"


def test_webhook_registra_contacto_con_telefono() -> None:
    connection = engine.connect()
    transaction = connection.begin()
    session = Session(bind=connection, autoflush=False, expire_on_commit=False)
    telefono = f"+593001{uuid4().int % 1000000:06d}"
    chat_id = -(uuid4().int % 1000000000)

    try:
        def override_get_db() -> Generator[Session, None, None]:
            yield session

        app.dependency_overrides[get_db] = override_get_db
        app.dependency_overrides[get_telegram_sender] = lambda: FakeTelegramSender()
        app.dependency_overrides[get_optional_telegram_sender] = lambda: FakeTelegramSender()
        client = TestClient(app)

        start = client.post(
            "/api/telegram/webhook",
            json={
                "update_id": 1,
                "message": {
                    "from": {"id": chat_id, "first_name": "David"},
                    "chat": {"id": chat_id, "first_name": "David", "type": "private"},
                    "text": "/start",
                },
            },
        )
        assert start.status_code == 200
        assert start.json()["estado"] == "MENU_PRINCIPAL"

        registrar = client.post(
            "/api/telegram/webhook",
            json={
                "update_id": 2,
                "message": {
                    "from": {"id": chat_id, "first_name": "David"},
                    "chat": {"id": chat_id, "first_name": "David", "type": "private"},
                    "text": "/registrar",
                },
            },
        )
        assert registrar.status_code == 200
        assert registrar.json()["estado"] == "ESPERANDO_TELEFONO"

        mensaje = f"mi numero {telefono}"
        registro = client.post(
            "/api/telegram/webhook",
            json={
                "update_id": 3,
                "message": {
                    "from": {"id": chat_id, "first_name": "David"},
                    "chat": {"id": chat_id, "first_name": "David", "type": "private"},
                    "text": mensaje,
                    "entities": [{"offset": 10, "length": len(telefono), "type": "phone_number"}],
                },
            },
        )
        assert registro.status_code == 200
        assert registro.json()["estado"] == "REGISTRADO"
        assert registro.json()["telefono"] == telefono
        assert registro.json()["chat_id"] == chat_id

        row = session.execute(
            text(
                """
                select telefono, chat_id, telegram_user_id, nombres, activo
                from telegram_contactos
                where telefono = :telefono
                """
            ),
            {"telefono": telefono},
        ).mappings().one()
        assert row["chat_id"] == chat_id
        assert row["telegram_user_id"] == chat_id
        assert row["activo"] is True
    finally:
        app.dependency_overrides.clear()
        session.close()
        transaction.rollback()
        connection.close()


def test_webhook_guarda_barrido_con_ubicacion_y_encuesta() -> None:
    connection = engine.connect()
    transaction = connection.begin()
    session = Session(bind=connection, autoflush=False, expire_on_commit=False)
    telefono = f"+593003{uuid4().int % 1000000:06d}"
    chat_id = -(uuid4().int % 1000000000)

    try:
        _asegurar_niveles(session)
        session.execute(
            text(
                """
                INSERT INTO telegram_contactos
                    (telegram_user_id, chat_id, telefono, activo)
                VALUES
                    (:telegram_user_id, :chat_id, :telefono, true)
                """
            ),
            {"telegram_user_id": chat_id, "chat_id": chat_id, "telefono": telefono},
        )

        def override_get_db() -> Generator[Session, None, None]:
            yield session

        app.dependency_overrides[get_db] = override_get_db
        app.dependency_overrides[get_optional_telegram_sender] = lambda: FakeTelegramSender()
        client = TestClient(app)

        seleccion = client.post(
            "/api/telegram/webhook",
            json={
                "update_id": 9,
                "callback_query": {
                    "id": "callback-barrido",
                    "from": {"id": chat_id, "first_name": "GAD"},
                    "message": {
                        "chat": {"id": chat_id, "first_name": "GAD", "type": "private"},
                    },
                    "data": "REPORTE_BARRIDO",
                },
            },
        )
        assert seleccion.status_code == 200
        assert seleccion.json()["estado"] == "REPORTE_BARRIDO_INICIADO"

        ubicacion = client.post(
            "/api/telegram/webhook",
            json={
                "update_id": 10,
                "message": {
                    "from": {"id": chat_id, "first_name": "GAD"},
                    "chat": {"id": chat_id, "first_name": "GAD", "type": "private"},
                    "location": {"latitude": -0.1806532, "longitude": -78.4678382},
                },
            },
        )
        assert ubicacion.status_code == 200
        assert ubicacion.json()["estado"] == "UBICACION_RECIBIDA"

        nivel = client.post(
            "/api/telegram/webhook",
            json={
                "update_id": 11,
                "poll_answer": {
                    "poll_id": "poll-test",
                    "user": {"id": chat_id, "first_name": "GAD"},
                    "option_ids": [3],
                },
            },
        )
        assert nivel.status_code == 200
        assert nivel.json()["estado"] == "BARRIDO_REGISTRADO"

        row = session.execute(
            text(
                """
                SELECT b.nivel_id, b.latitud, b.longitud
                FROM telegram_barridos b
                JOIN telegram_contactos c ON c.id = b.contacto_id
                WHERE c.telefono = :telefono
                """
            ),
            {"telefono": telefono},
        ).mappings().one()
        assert row["nivel_id"] == 4
        assert float(row["latitud"]) == -0.1806532
        assert float(row["longitud"]) == -78.4678382
    finally:
        app.dependency_overrides.clear()
        session.close()
        transaction.rollback()
        connection.close()


def test_webhook_contacto_registrado_muestra_menu_con_texto() -> None:
    connection = engine.connect()
    transaction = connection.begin()
    session = Session(bind=connection, autoflush=False, expire_on_commit=False)
    telefono = f"+593004{uuid4().int % 1000000:06d}"
    chat_id = -(uuid4().int % 1000000000)

    try:
        session.execute(
            text(
                """
                INSERT INTO telegram_contactos
                    (telegram_user_id, chat_id, telefono, activo)
                VALUES
                    (:telegram_user_id, :chat_id, :telefono, true)
                """
            ),
            {"telegram_user_id": chat_id, "chat_id": chat_id, "telefono": telefono},
        )

        def override_get_db() -> Generator[Session, None, None]:
            yield session

        app.dependency_overrides[get_db] = override_get_db
        app.dependency_overrides[get_optional_telegram_sender] = lambda: FakeTelegramSender()
        client = TestClient(app)

        respuesta = client.post(
            "/api/telegram/webhook",
            json={
                "update_id": 20,
                "message": {
                    "from": {"id": chat_id, "first_name": "GAD"},
                    "chat": {"id": chat_id, "first_name": "GAD", "type": "private"},
                    "text": "hola",
                },
            },
        )

        assert respuesta.status_code == 200
        assert respuesta.json()["estado"] == "MENU_PRINCIPAL"
        assert respuesta.json()["telefono"] == telefono
    finally:
        app.dependency_overrides.clear()
        session.close()
        transaction.rollback()
        connection.close()


def test_webhook_reporte_evento_queda_seleccionado() -> None:
    connection = engine.connect()
    transaction = connection.begin()
    session = Session(bind=connection, autoflush=False, expire_on_commit=False)
    chat_id = -(uuid4().int % 1000000000)

    try:
        def override_get_db() -> Generator[Session, None, None]:
            yield session

        app.dependency_overrides[get_db] = override_get_db
        app.dependency_overrides[get_optional_telegram_sender] = lambda: FakeTelegramSender()
        client = TestClient(app)

        respuesta = client.post(
            "/api/telegram/webhook",
            json={
                "update_id": 30,
                "callback_query": {
                    "id": "callback-evento",
                    "from": {"id": chat_id, "first_name": "GAD"},
                    "message": {
                        "chat": {"id": chat_id, "first_name": "GAD", "type": "private"},
                    },
                    "data": "REPORTE_EVENTO",
                },
            },
        )

        assert respuesta.status_code == 200
        assert respuesta.json()["estado"] == "REPORTE_EVENTO_INICIADO"
    finally:
        app.dependency_overrides.clear()
        session.close()
        transaction.rollback()
        connection.close()


def test_webhook_no_permite_registrar_telefono_de_otra_cuenta() -> None:
    connection = engine.connect()
    transaction = connection.begin()
    session = Session(bind=connection, autoflush=False, expire_on_commit=False)
    telefono = f"+593005{uuid4().int % 1000000:06d}"
    chat_id_existente = -(uuid4().int % 1000000000)
    chat_id_nuevo = -(uuid4().int % 1000000000)

    try:
        session.execute(
            text(
                """
                INSERT INTO telegram_contactos
                    (telegram_user_id, chat_id, telefono, activo)
                VALUES
                    (:telegram_user_id, :chat_id, :telefono, true)
                """
            ),
            {
                "telegram_user_id": chat_id_existente,
                "chat_id": chat_id_existente,
                "telefono": telefono,
            },
        )

        def override_get_db() -> Generator[Session, None, None]:
            yield session

        app.dependency_overrides[get_db] = override_get_db
        app.dependency_overrides[get_optional_telegram_sender] = lambda: FakeTelegramSender()
        client = TestClient(app)

        registrar = client.post(
            "/api/telegram/webhook",
            json={
                "update_id": 40,
                "message": {
                    "from": {"id": chat_id_nuevo, "first_name": "Nuevo"},
                    "chat": {"id": chat_id_nuevo, "first_name": "Nuevo", "type": "private"},
                    "text": "/registrar",
                },
            },
        )
        assert registrar.status_code == 200

        respuesta = client.post(
            "/api/telegram/webhook",
            json={
                "update_id": 41,
                "message": {
                    "from": {"id": chat_id_nuevo, "first_name": "Nuevo"},
                    "chat": {"id": chat_id_nuevo, "first_name": "Nuevo", "type": "private"},
                    "text": telefono,
                },
            },
        )

        assert respuesta.status_code == 200
        assert respuesta.json()["estado"] == "TELEFONO_YA_REGISTRADO"

        row = session.execute(
            text(
                """
                SELECT telegram_user_id, chat_id
                FROM telegram_contactos
                WHERE telefono = :telefono
                """
            ),
            {"telefono": telefono},
        ).mappings().one()
        assert row["telegram_user_id"] == chat_id_existente
        assert row["chat_id"] == chat_id_existente
    finally:
        app.dependency_overrides.clear()
        session.close()
        transaction.rollback()
        connection.close()
