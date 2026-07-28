from collections.abc import Generator
from contextlib import contextmanager
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.routes import flujos
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

    def get_file(self, file_id: str) -> dict[str, object]:
        return {"ok": True, "result": {"file_id": file_id, "file_path": "photos/evento-test.jpg"}}

    def download_file(self, file_path: str) -> bytes:
        return b"\xff\xd8\xff\xe0fake-jpeg-content"


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


def _asegurar_tabla_eventos(session: Session) -> None:
    session.execute(
        text(
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
    )


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
    assert "/api/telegram/eventos/fotos" in paths
    assert "/api/telegram/eventos/{evento_id}/foto" in paths
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
        assert solicitud.json()["registros"][0]["estado"] == "PROCESANDO"

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


def test_webhook_barrido_esperando_ubicacion_repite_instruccion_gps() -> None:
    connection = engine.connect()
    transaction = connection.begin()
    session = Session(bind=connection, autoflush=False, expire_on_commit=False)
    telefono = f"+593011{uuid4().int % 1000000:06d}"
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

        seleccion = client.post(
            "/api/telegram/webhook",
            json={
                "update_id": 70,
                "callback_query": {
                    "id": "callback-barrido-gps",
                    "from": {"id": chat_id, "first_name": "GAD"},
                    "message": {
                        "chat": {"id": chat_id, "first_name": "GAD", "type": "private"},
                    },
                    "data": "REPORTE_BARRIDO",
                },
            },
        )
        assert seleccion.status_code == 200

        respuesta = client.post(
            "/api/telegram/webhook",
            json={
                "update_id": 71,
                "message": {
                    "from": {"id": chat_id, "first_name": "GAD"},
                    "chat": {"id": chat_id, "first_name": "GAD", "type": "private"},
                    "text": "no puedo enviar ubicacion",
                },
            },
        )

        assert respuesta.status_code == 200
        assert respuesta.json()["estado"] == "UBICACION_REQUERIDA"
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


def test_webhook_scripts_ejecuta_barrido_lluvia() -> None:
    connection = engine.connect()
    transaction = connection.begin()
    session = Session(bind=connection, autoflush=False, expire_on_commit=False)
    telefono_1 = f"+593012{uuid4().int % 1000000:06d}"
    telefono_2 = f"09{uuid4().int % 100000000:08d}"
    chat_id_admin = -(uuid4().int % 1000000000)
    chat_id_1 = -(uuid4().int % 1000000000)
    chat_id_2 = -(uuid4().int % 1000000000)
    telefonos_originales = flujos.SCRIPT_BARRIDO_LLUVIA_TELEFONOS
    admins_originales = flujos.SCRIPT_ADMIN_TELEGRAM_USER_IDS

    try:
        flujos.SCRIPT_BARRIDO_LLUVIA_TELEFONOS = [telefono_1, telefono_2]
        flujos.SCRIPT_ADMIN_TELEGRAM_USER_IDS = {chat_id_admin}
        session.execute(
            text(
                """
                INSERT INTO telegram_contactos
                    (telegram_user_id, chat_id, telefono, activo)
                VALUES
                    (:telegram_user_id_1, :chat_id_1, :telefono_1, true),
                    (:telegram_user_id_2, :chat_id_2, :telefono_2, true)
                """
            ),
            {
                "telegram_user_id_1": chat_id_1,
                "chat_id_1": chat_id_1,
                "telefono_1": telefono_1,
                "telegram_user_id_2": chat_id_2,
                "chat_id_2": chat_id_2,
                "telefono_2": telefono_2,
            },
        )

        def override_get_db() -> Generator[Session, None, None]:
            yield session

        app.dependency_overrides[get_db] = override_get_db
        app.dependency_overrides[get_optional_telegram_sender] = lambda: FakeTelegramSender()
        client = TestClient(app)

        menu = client.post(
            "/api/telegram/webhook",
            json={
                "update_id": 80,
                "message": {
                    "from": {"id": chat_id_admin, "first_name": "Admin"},
                    "chat": {"id": chat_id_admin, "first_name": "Admin", "type": "private"},
                    "text": "/scripts",
                },
            },
        )
        assert menu.status_code == 200
        assert menu.json()["estado"] == "ESPERANDO_PASSCODE_SCRIPT"

        passcode = client.post(
            "/api/telegram/webhook",
            json={
                "update_id": 81,
                "message": {
                    "from": {"id": chat_id_admin, "first_name": "Admin"},
                    "chat": {"id": chat_id_admin, "first_name": "Admin", "type": "private"},
                    "text": "Sngre.2026",
                },
            },
        )
        assert passcode.status_code == 200
        assert passcode.json()["estado"] == "MENU_SCRIPTS"

        ejecucion = client.post(
            "/api/telegram/webhook",
            json={
                "update_id": 82,
                "callback_query": {
                    "id": "callback-script-lluvia",
                    "from": {"id": chat_id_admin, "first_name": "Admin"},
                    "message": {
                        "chat": {"id": chat_id_admin, "first_name": "Admin", "type": "private"},
                    },
                    "data": "SCRIPT_BARRIDO_LLUVIA",
                },
            },
        )
        assert ejecucion.status_code == 200
        assert ejecucion.json()["estado"] == "SCRIPT_BARRIDO_LLUVIA_EJECUTADO"

        total = session.execute(
            text(
                """
                SELECT count(*)
                FROM telegram_consultas tc
                JOIN telegram_contactos c ON c.id = tc.contacto_id
                WHERE c.telefono IN (:telefono_1, :telefono_2)
                  AND tc.tipo_consulta = 'BARRIDO_GAD'
                  AND tc.estado = 'PROCESANDO'
                  AND tc.codigo = 'BARRIDO-AUTO'
                """
            ),
            {"telefono_1": telefono_1, "telefono_2": telefono_2},
        ).scalar_one()
        assert total == 2
    finally:
        flujos.SCRIPT_BARRIDO_LLUVIA_TELEFONOS = telefonos_originales
        flujos.SCRIPT_ADMIN_TELEGRAM_USER_IDS = admins_originales
        app.dependency_overrides.clear()
        session.close()
        transaction.rollback()
        connection.close()


def test_webhook_scripts_bloquea_passcode_tras_tres_intentos() -> None:
    connection = engine.connect()
    transaction = connection.begin()
    session = Session(bind=connection, autoflush=False, expire_on_commit=False)
    chat_id_admin = -(uuid4().int % 1000000000)
    admins_originales = flujos.SCRIPT_ADMIN_TELEGRAM_USER_IDS

    try:
        flujos.SCRIPT_ADMIN_TELEGRAM_USER_IDS = {chat_id_admin}

        def override_get_db() -> Generator[Session, None, None]:
            yield session

        app.dependency_overrides[get_db] = override_get_db
        app.dependency_overrides[get_optional_telegram_sender] = lambda: FakeTelegramSender()
        client = TestClient(app)

        menu = client.post(
            "/api/telegram/webhook",
            json={
                "update_id": 90,
                "message": {
                    "from": {"id": chat_id_admin, "first_name": "Admin"},
                    "chat": {"id": chat_id_admin, "first_name": "Admin", "type": "private"},
                    "text": "/scripts",
                },
            },
        )
        assert menu.status_code == 200
        assert menu.json()["estado"] == "ESPERANDO_PASSCODE_SCRIPT"

        for update_id in [91, 92]:
            respuesta = client.post(
                "/api/telegram/webhook",
                json={
                    "update_id": update_id,
                    "message": {
                        "from": {"id": chat_id_admin, "first_name": "Admin"},
                        "chat": {"id": chat_id_admin, "first_name": "Admin", "type": "private"},
                        "text": "incorrecto",
                    },
                },
            )
            assert respuesta.status_code == 200
            assert respuesta.json()["estado"] == "PASSCODE_SCRIPT_INVALIDO"

        bloqueo = client.post(
            "/api/telegram/webhook",
            json={
                "update_id": 93,
                "message": {
                    "from": {"id": chat_id_admin, "first_name": "Admin"},
                    "chat": {"id": chat_id_admin, "first_name": "Admin", "type": "private"},
                    "text": "incorrecto",
                },
            },
        )
        assert bloqueo.status_code == 200
        assert bloqueo.json()["estado"] == "PASSCODE_SCRIPT_BLOQUEADO"

        pendientes = session.execute(
            text(
                """
                SELECT count(*)
                FROM telegram_consultas tc
                JOIN telegram_contactos c ON c.id = tc.contacto_id
                WHERE c.telegram_user_id = :telegram_user_id
                  AND tc.tipo_consulta = 'SCRIPT_AUTH'
                  AND tc.estado = 'PROCESANDO'
                """
            ),
            {"telegram_user_id": chat_id_admin},
        ).scalar_one()
        assert pendientes == 0
    finally:
        flujos.SCRIPT_ADMIN_TELEGRAM_USER_IDS = admins_originales
        app.dependency_overrides.clear()
        session.close()
        transaction.rollback()
        connection.close()


def test_webhook_reporte_lluvia_muestra_conteos_por_intensidad() -> None:
    connection = engine.connect()
    transaction = connection.begin()
    session = Session(bind=connection, autoflush=False, expire_on_commit=False)
    telefono = f"+593013{uuid4().int % 1000000:06d}"
    chat_id_admin = -(uuid4().int % 1000000000)
    admins_originales = flujos.SCRIPT_ADMIN_TELEGRAM_USER_IDS

    try:
        flujos.SCRIPT_ADMIN_TELEGRAM_USER_IDS = {chat_id_admin}
        _asegurar_niveles(session)
        session.execute(text("DELETE FROM telegram_barridos"))
        contacto_id = session.execute(
            text(
                """
                INSERT INTO telegram_contactos
                    (telegram_user_id, chat_id, telefono, activo)
                VALUES
                    (:telegram_user_id, :chat_id, :telefono, true)
                RETURNING id
                """
            ),
            {"telegram_user_id": chat_id_admin, "chat_id": chat_id_admin, "telefono": telefono},
        ).scalar_one()
        niveles = {
            row["nombre"]: row["id"]
            for row in session.execute(
                text(
                    """
                    SELECT id, nombre
                    FROM catalogo_niveles_evento
                    WHERE nombre IN ('DEBIL', 'MODERADO', 'FUERTE', 'MUY_FUERTE')
                    """
                )
            ).mappings()
        }
        session.execute(
            text(
                """
                INSERT INTO telegram_barridos
                    (contacto_id, nivel_id, latitud, longitud)
                VALUES
                    (:contacto_id, :nivel_debil, -0.1806532, -78.4678382),
                    (:contacto_id, :nivel_fuerte, -0.1806532, -78.4678382),
                    (:contacto_id, :nivel_fuerte, -0.1806532, -78.4678382),
                    (:contacto_id, :nivel_muy_fuerte, -0.1806532, -78.4678382)
                """
            ),
            {
                "contacto_id": contacto_id,
                "nivel_debil": niveles["DEBIL"],
                "nivel_fuerte": niveles["FUERTE"],
                "nivel_muy_fuerte": niveles["MUY_FUERTE"],
            },
        )

        def override_get_db() -> Generator[Session, None, None]:
            yield session

        app.dependency_overrides[get_db] = override_get_db
        app.dependency_overrides[get_optional_telegram_sender] = lambda: FakeTelegramSender()
        client = TestClient(app)

        respuesta = client.post(
            "/api/telegram/webhook",
            json={
                "update_id": 100,
                "message": {
                    "from": {"id": chat_id_admin, "first_name": "Admin"},
                    "chat": {"id": chat_id_admin, "first_name": "Admin", "type": "private"},
                    "text": "/reporte_lluvia",
                },
            },
        )

        assert respuesta.status_code == 200
        data = respuesta.json()
        assert data["estado"] == "REPORTE_LLUVIA_GENERADO"
        assert "Total de reportes: 4" in data["mensaje"]
        assert "- Debil: 1" in data["mensaje"]
        assert "- Moderado: 0" in data["mensaje"]
        assert "- Fuerte: 2" in data["mensaje"]
        assert "- Muy fuerte: 1" in data["mensaje"]
    finally:
        flujos.SCRIPT_ADMIN_TELEGRAM_USER_IDS = admins_originales
        app.dependency_overrides.clear()
        session.close()
        transaction.rollback()
        connection.close()


def test_webhook_reporte_evento_queda_seleccionado() -> None:
    connection = engine.connect()
    transaction = connection.begin()
    session = Session(bind=connection, autoflush=False, expire_on_commit=False)
    telefono = f"+593007{uuid4().int % 1000000:06d}"
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


def test_webhook_guarda_reporte_evento_con_foto_descripcion_y_ubicacion() -> None:
    connection = engine.connect()
    transaction = connection.begin()
    session = Session(bind=connection, autoflush=False, expire_on_commit=False)
    telefono = f"+593006{uuid4().int % 1000000:06d}"
    chat_id = -(uuid4().int % 1000000000)

    try:
        _asegurar_tabla_eventos(session)
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
                "update_id": 50,
                "callback_query": {
                    "id": "callback-evento-full",
                    "from": {"id": chat_id, "first_name": "GAD"},
                    "message": {
                        "chat": {"id": chat_id, "first_name": "GAD", "type": "private"},
                    },
                    "data": "REPORTE_EVENTO",
                },
            },
        )
        assert seleccion.status_code == 200
        assert seleccion.json()["estado"] == "REPORTE_EVENTO_INICIADO"

        foto = client.post(
            "/api/telegram/webhook",
            json={
                "update_id": 51,
                "message": {
                    "from": {"id": chat_id, "first_name": "GAD"},
                    "chat": {"id": chat_id, "first_name": "GAD", "type": "private"},
                    "photo": [
                        {
                            "file_id": "foto-small",
                            "file_unique_id": "foto-small-unique",
                            "width": 320,
                            "height": 240,
                            "file_size": 1000,
                        },
                        {
                            "file_id": "foto-large",
                            "file_unique_id": "foto-large-unique",
                            "width": 1280,
                            "height": 960,
                            "file_size": 5000,
                        },
                    ],
                },
            },
        )
        assert foto.status_code == 200
        assert foto.json()["estado"] == "FOTO_EVENTO_RECIBIDA"

        descripcion = client.post(
            "/api/telegram/webhook",
            json={
                "update_id": 52,
                "message": {
                    "from": {"id": chat_id, "first_name": "GAD"},
                    "chat": {"id": chat_id, "first_name": "GAD", "type": "private"},
                    "text": "Deslizamiento junto a la via principal",
                },
            },
        )
        assert descripcion.status_code == 200
        assert descripcion.json()["estado"] == "DESCRIPCION_EVENTO_RECIBIDA"

        ubicacion = client.post(
            "/api/telegram/webhook",
            json={
                "update_id": 53,
                "message": {
                    "from": {"id": chat_id, "first_name": "GAD"},
                    "chat": {"id": chat_id, "first_name": "GAD", "type": "private"},
                    "location": {"latitude": -0.1806532, "longitude": -78.4678382},
                },
            },
        )
        assert ubicacion.status_code == 200
        assert ubicacion.json()["estado"] == "REPORTE_EVENTO_GUARDADO"

        row = session.execute(
            text(
                """
                SELECT e.descripcion, e.foto_file_id, e.foto_file_unique_id, e.latitud, e.longitud
                FROM telegram_eventos e
                JOIN telegram_contactos c ON c.id = e.contacto_id
                WHERE c.telefono = :telefono
                """
            ),
            {"telefono": telefono},
        ).mappings().one()
        assert row["descripcion"] == "Deslizamiento junto a la via principal"
        assert row["foto_file_id"] == "foto-large"
        assert row["foto_file_unique_id"] == "foto-large-unique"
        assert float(row["latitud"]) == -0.1806532
        assert float(row["longitud"]) == -78.4678382
    finally:
        app.dependency_overrides.clear()
        session.close()
        transaction.rollback()
        connection.close()


def test_endpoint_obtener_foto_evento_devuelve_imagen() -> None:
    connection = engine.connect()
    transaction = connection.begin()
    session = Session(bind=connection, autoflush=False, expire_on_commit=False)
    telefono = f"+593008{uuid4().int % 1000000:06d}"
    chat_id = -(uuid4().int % 1000000000)

    try:
        _asegurar_tabla_eventos(session)
        contacto_id = session.execute(
            text(
                """
                INSERT INTO telegram_contactos
                    (telegram_user_id, chat_id, telefono, activo)
                VALUES
                    (:telegram_user_id, :chat_id, :telefono, true)
                RETURNING id
                """
            ),
            {"telegram_user_id": chat_id, "chat_id": chat_id, "telefono": telefono},
        ).scalar_one()
        evento_id = session.execute(
            text(
                """
                INSERT INTO telegram_eventos
                    (contacto_id, descripcion, foto_file_id, foto_file_unique_id, latitud, longitud)
                VALUES
                    (:contacto_id, :descripcion, :foto_file_id, :foto_file_unique_id, :latitud, :longitud)
                RETURNING id
                """
            ),
            {
                "contacto_id": contacto_id,
                "descripcion": "Evento con foto",
                "foto_file_id": "foto-large",
                "foto_file_unique_id": "foto-large-unique",
                "latitud": -0.1806532,
                "longitud": -78.4678382,
            },
        ).scalar_one()

        def override_get_db() -> Generator[Session, None, None]:
            yield session

        app.dependency_overrides[get_db] = override_get_db
        app.dependency_overrides[get_telegram_sender] = lambda: FakeTelegramSender()
        client = TestClient(app)

        respuesta = client.get(f"/api/telegram/eventos/{evento_id}/foto")

        assert respuesta.status_code == 200
        assert respuesta.headers["content-type"] == "image/jpeg"
        assert respuesta.content.startswith(b"\xff\xd8")
    finally:
        app.dependency_overrides.clear()
        session.close()
        transaction.rollback()
        connection.close()


def test_endpoint_lista_fotos_eventos() -> None:
    connection = engine.connect()
    transaction = connection.begin()
    session = Session(bind=connection, autoflush=False, expire_on_commit=False)
    telefono = f"+593009{uuid4().int % 1000000:06d}"
    chat_id = -(uuid4().int % 1000000000)

    try:
        _asegurar_tabla_eventos(session)
        contacto_id = session.execute(
            text(
                """
                INSERT INTO telegram_contactos
                    (telegram_user_id, chat_id, telefono, activo)
                VALUES
                    (:telegram_user_id, :chat_id, :telefono, true)
                RETURNING id
                """
            ),
            {"telegram_user_id": chat_id, "chat_id": chat_id, "telefono": telefono},
        ).scalar_one()
        evento_id = session.execute(
            text(
                """
                INSERT INTO telegram_eventos
                    (contacto_id, descripcion, foto_file_id, foto_file_unique_id, latitud, longitud)
                VALUES
                    (:contacto_id, :descripcion, :foto_file_id, :foto_file_unique_id, :latitud, :longitud)
                RETURNING id
                """
            ),
            {
                "contacto_id": contacto_id,
                "descripcion": "Evento listado",
                "foto_file_id": "foto-listada",
                "foto_file_unique_id": "foto-listada-unique",
                "latitud": -0.1806532,
                "longitud": -78.4678382,
            },
        ).scalar_one()

        def override_get_db() -> Generator[Session, None, None]:
            yield session

        app.dependency_overrides[get_db] = override_get_db
        client = TestClient(app)

        respuesta = client.get("/api/telegram/eventos/fotos")

        assert respuesta.status_code == 200
        data = respuesta.json()
        item = next(evento for evento in data if evento["evento_id"] == evento_id)
        assert item["descripcion"] == "Evento listado"
        assert item["foto_file_unique_id"] == "foto-listada-unique"
        assert item["foto_url"].endswith(f"/api/telegram/eventos/{evento_id}/foto")
    finally:
        app.dependency_overrides.clear()
        session.close()
        transaction.rollback()
        connection.close()


def test_webhook_evento_con_album_usa_solo_primera_foto() -> None:
    connection = engine.connect()
    transaction = connection.begin()
    session = Session(bind=connection, autoflush=False, expire_on_commit=False)
    telefono = f"+593010{uuid4().int % 1000000:06d}"
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

        seleccion = client.post(
            "/api/telegram/webhook",
            json={
                "update_id": 60,
                "callback_query": {
                    "id": "callback-evento-album",
                    "from": {"id": chat_id, "first_name": "GAD"},
                    "message": {
                        "chat": {"id": chat_id, "first_name": "GAD", "type": "private"},
                    },
                    "data": "REPORTE_EVENTO",
                },
            },
        )
        assert seleccion.status_code == 200

        primera = client.post(
            "/api/telegram/webhook",
            json={
                "update_id": 61,
                "message": {
                    "from": {"id": chat_id, "first_name": "GAD"},
                    "chat": {"id": chat_id, "first_name": "GAD", "type": "private"},
                    "media_group_id": "album-1",
                    "photo": [
                        {
                            "file_id": "primera-foto",
                            "file_unique_id": "primera-foto-unique",
                            "width": 1280,
                            "height": 960,
                            "file_size": 5000,
                        },
                    ],
                },
            },
        )
        assert primera.status_code == 200
        assert primera.json()["estado"] == "FOTO_EVENTO_RECIBIDA"

        segunda = client.post(
            "/api/telegram/webhook",
            json={
                "update_id": 62,
                "message": {
                    "from": {"id": chat_id, "first_name": "GAD"},
                    "chat": {"id": chat_id, "first_name": "GAD", "type": "private"},
                    "media_group_id": "album-1",
                    "photo": [
                        {
                            "file_id": "segunda-foto",
                            "file_unique_id": "segunda-foto-unique",
                            "width": 1280,
                            "height": 960,
                            "file_size": 5000,
                        },
                    ],
                },
            },
        )
        assert segunda.status_code == 200
        assert segunda.json()["estado"] == "FOTO_EVENTO_IGNORADA"

        parametros = session.execute(
            text(
                """
                SELECT tc.parametros
                FROM telegram_consultas tc
                JOIN telegram_contactos c ON c.id = tc.contacto_id
                WHERE c.telefono = :telefono
                  AND tc.tipo_consulta = 'REPORTE_EVENTO'
                """
            ),
            {"telefono": telefono},
        ).scalar_one()
        assert parametros["foto"]["file_id"] == "primera-foto"
        assert parametros["media_group_id"] == "album-1"
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
