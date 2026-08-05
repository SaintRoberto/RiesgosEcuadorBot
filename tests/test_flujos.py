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
    def __init__(self) -> None:
        self.messages: list[dict[str, object]] = []
        self.polls: list[dict[str, object]] = []
        self.photos: list[dict[str, object]] = []

    def send_message(
        self,
        chat_id: int,
        text: str,
        reply_markup: dict[str, object] | None = None,
    ) -> dict[str, object]:
        result = {"chat_id": chat_id, "text": text}
        if reply_markup is not None:
            result["reply_markup"] = reply_markup
        self.messages.append(result)
        return {"ok": True, "result": result}

    def send_poll(self, chat_id: int, question: str, options: list[str]) -> dict[str, object]:
        result = {
            "chat_id": chat_id,
            "poll": {
                "question": question,
                "options": [{"text": option} for option in options],
                "is_anonymous": False,
            },
        }
        self.polls.append(result)
        return {
            "ok": True,
            "result": result,
        }

    def answer_callback_query(self, callback_query_id: str) -> dict[str, object]:
        return {"ok": True, "result": True, "callback_query_id": callback_query_id}

    def send_photo(self, chat_id: int, photo: str, caption: str | None = None) -> dict[str, object]:
        result = {"chat_id": chat_id, "photo": photo, "caption": caption}
        self.photos.append(result)
        return {"ok": True, "result": result}

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
                tipo_alerta_id bigint,
                alerta_encuesta_id bigint,
                descripcion text NOT NULL,
                personas_en_riesgo boolean NOT NULL DEFAULT false,
                cantidad_personas_riesgo integer NOT NULL DEFAULT 0,
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
                    CHECK (longitud >= -180 AND longitud <= 180),
                CONSTRAINT chk_telegram_eventos_cantidad_personas_riesgo
                    CHECK (cantidad_personas_riesgo >= 0 AND cantidad_personas_riesgo <= 999999)
            )
            """
        )
    )
    session.execute(text("ALTER TABLE public.telegram_eventos ADD COLUMN IF NOT EXISTS tipo_alerta_id bigint"))
    session.execute(text("ALTER TABLE public.telegram_eventos ADD COLUMN IF NOT EXISTS alerta_encuesta_id bigint"))
    session.execute(
        text("ALTER TABLE public.telegram_eventos ADD COLUMN IF NOT EXISTS personas_en_riesgo boolean NOT NULL DEFAULT false")
    )
    session.execute(
        text(
            "ALTER TABLE public.telegram_eventos "
            "ADD COLUMN IF NOT EXISTS cantidad_personas_riesgo integer NOT NULL DEFAULT 0"
        )
    )


def _asegurar_tipo_alertas(session: Session) -> None:
    session.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS public.tipo_alertas
            (
                id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
                descripcion varchar(150) NOT NULL,
                activo boolean NOT NULL DEFAULT true,
                fecha_creacion timestamp with time zone NOT NULL DEFAULT now(),
                CONSTRAINT uq_tipo_alertas_descripcion UNIQUE (descripcion)
            )
            """
        )
    )
    session.execute(
        text(
            """
            INSERT INTO public.tipo_alertas (id, descripcion)
            OVERRIDING SYSTEM VALUE
            VALUES
                (1, 'CA\u00cdDA DE CENIZA'),
                (2, 'DERRUMBES'),
                (3, 'ESTADO DE RIOS/QUEBRADAS'),
                (4, 'SISMO'),
                (5, 'INCENDIOS FORESTALES'),
                (6, 'LLUVIAS'),
                (7, 'OLEAJE')
            ON CONFLICT (id) DO UPDATE
            SET descripcion = EXCLUDED.descripcion,
                activo = true
            """
        )
    )
    session.execute(
        text(
            """
            SELECT setval(
                pg_get_serial_sequence('public.tipo_alertas', 'id'),
                (SELECT max(id) FROM public.tipo_alertas),
                true
            )
            """
        )
    )


def _asegurar_catalogos_alertas(session: Session) -> None:
    _asegurar_tipo_alertas(session)
    session.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS public.alerta_encuesta
            (
                id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
                tipo_alerta_id bigint NOT NULL,
                nombre varchar(150) NOT NULL,
                descripcion text,
                color varchar(20),
                orden integer NOT NULL,
                activo boolean NOT NULL DEFAULT true,
                fecha_creacion timestamp with time zone NOT NULL DEFAULT now(),
                CONSTRAINT fk_alerta_encuesta_tipo_alerta
                    FOREIGN KEY (tipo_alerta_id)
                    REFERENCES public.tipo_alertas (id),
                CONSTRAINT uq_alerta_encuesta_tipo_orden
                    UNIQUE (tipo_alerta_id, orden)
            )
            """
        )
    )
    session.execute(text("ALTER TABLE public.alerta_encuesta ADD COLUMN IF NOT EXISTS color varchar(20)"))
    session.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS public.alerta_recomendaciones
            (
                id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
                tipo_alerta_id bigint NOT NULL,
                recomendacion text NOT NULL,
                orden integer NOT NULL,
                activo boolean NOT NULL DEFAULT true,
                fecha_creacion timestamp with time zone NOT NULL DEFAULT now(),
                CONSTRAINT fk_alerta_recomendaciones_tipo_alerta
                    FOREIGN KEY (tipo_alerta_id)
                    REFERENCES public.tipo_alertas (id),
                CONSTRAINT uq_alerta_recomendaciones_tipo_orden
                    UNIQUE (tipo_alerta_id, orden)
            )
            """
        )
    )
    session.execute(
        text(
            """
            UPDATE public.alerta_encuesta
            SET activo = false
            WHERE tipo_alerta_id IN (4, 6)
            """
        )
    )
    session.execute(
        text(
            """
            UPDATE public.alerta_recomendaciones
            SET activo = false
            WHERE tipo_alerta_id IN (4, 6)
            """
        )
    )
    session.execute(
        text(
            """
            INSERT INTO public.alerta_encuesta (tipo_alerta_id, orden, nombre, descripcion, color)
            VALUES
                (6, 1, 'LLUVIA MUY FUERTE', 'Rapido anegamiento de calles.', '🔴'),
                (6, 2, 'LLUVIA FUERTE', 'Poco anegamiento de calles que dificulta movilidad.', '🟡'),
                (6, 3, 'LLUVIA MODERADA', 'Visibilidad reducida y acumulacion leve de agua.', '🟢'),
                (4, 1, 'MUY FUERTE', 'Panico general, personas pierden estabilidad.', '🔴')
            ON CONFLICT (tipo_alerta_id, orden) DO UPDATE
            SET nombre = EXCLUDED.nombre,
                descripcion = EXCLUDED.descripcion,
                color = EXCLUDED.color,
                activo = true
            """
        )
    )
    session.execute(
        text(
            """
            INSERT INTO public.alerta_recomendaciones (tipo_alerta_id, orden, recomendacion)
            VALUES
                (6, 1, 'Evitar transitar en zonas inundadas.'),
                (6, 2, 'No acercarse a postes, cables o arboles.'),
                (4, 1, 'Conserve la calma.')
            ON CONFLICT (tipo_alerta_id, orden) DO UPDATE
            SET recomendacion = EXCLUDED.recomendacion,
                activo = true
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
    assert "/api/telegram/reportes/alertas/{tipo_alerta_id}" in paths
    assert "/api/telegram/tipo-alertas" in paths
    assert "/api/telegram/tipo-alertas/{tipo_alerta_id}" in paths
    assert "/api/telegram/alerta-encuesta" in paths
    assert "/api/telegram/tipo-alertas/{tipo_alerta_id}/encuesta" in paths
    assert "/api/telegram/alerta-recomendaciones" in paths
    assert "/api/telegram/tipo-alertas/{tipo_alerta_id}/recomendaciones" in paths
    assert "/api/telegram/eventos" in paths
    assert "/api/telegram/eventos/fotos" in paths
    assert "/api/telegram/eventos/{evento_id}/foto" in paths
    assert "/api/telegram/eventos/seguimientos" in paths


def test_endpoint_tipo_alertas_lista_catalogo() -> None:
    connection = engine.connect()
    transaction = connection.begin()
    session = Session(bind=connection, autoflush=False, expire_on_commit=False)

    try:
        _asegurar_tipo_alertas(session)

        def override_get_db() -> Generator[Session, None, None]:
            yield session

        app.dependency_overrides[get_db] = override_get_db
        client = TestClient(app)

        respuesta = client.get("/api/telegram/tipo-alertas")

        assert respuesta.status_code == 200
        data = respuesta.json()
        assert [item["id"] for item in data] == [1, 2, 3, 4, 5, 6, 7]
        assert data[0]["descripcion"] == "CA\u00cdDA DE CENIZA"
        assert data[5]["descripcion"] == "LLUVIAS"

        detalle = client.get("/api/telegram/tipo-alertas/6")
        assert detalle.status_code == 200
        assert detalle.json()["descripcion"] == "LLUVIAS"
    finally:
        app.dependency_overrides.clear()
        session.close()
        transaction.rollback()
        connection.close()


def test_endpoints_catalogos_alertas_filtran_por_tipo_alerta() -> None:
    connection = engine.connect()
    transaction = connection.begin()
    session = Session(bind=connection, autoflush=False, expire_on_commit=False)

    try:
        _asegurar_catalogos_alertas(session)

        def override_get_db() -> Generator[Session, None, None]:
            yield session

        app.dependency_overrides[get_db] = override_get_db
        client = TestClient(app)

        encuesta = client.get("/api/telegram/tipo-alertas/6/encuesta")
        assert encuesta.status_code == 200
        encuesta_data = encuesta.json()
        assert [item["nombre"] for item in encuesta_data] == [
            "LLUVIA MUY FUERTE",
            "LLUVIA FUERTE",
            "LLUVIA MODERADA",
        ]
        assert [item["color"] for item in encuesta_data] == ["🔴", "🟡", "🟢"]

        encuesta_filtrada = client.get("/api/telegram/alerta-encuesta", params={"tipo_alerta_id": 4})
        assert encuesta_filtrada.status_code == 200
        assert encuesta_filtrada.json()[0]["nombre"] == "MUY FUERTE"

        recomendaciones = client.get("/api/telegram/tipo-alertas/6/recomendaciones")
        assert recomendaciones.status_code == 200
        recomendaciones_data = recomendaciones.json()
        assert [item["recomendacion"] for item in recomendaciones_data] == [
            "Evitar transitar en zonas inundadas.",
            "No acercarse a postes, cables o arboles.",
        ]

        recomendaciones_filtradas = client.get(
            "/api/telegram/alerta-recomendaciones",
            params={"tipo_alerta_id": 4},
        )
        assert recomendaciones_filtradas.status_code == 200
        assert recomendaciones_filtradas.json()[0]["recomendacion"] == "Conserve la calma."
    finally:
        app.dependency_overrides.clear()
        session.close()
        transaction.rollback()
        connection.close()


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
        _asegurar_tipo_alertas(session)
        flujos.REGISTROS_TELEFONO_PENDIENTES.clear()
        session.execute(
            text(
                """
                INSERT INTO telegram_contactos
                    (telegram_user_id, chat_id, telefono, activo)
                VALUES
                    (0, 0, :telefono, true)
                """
            ),
            {"telefono": telefono},
        )

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
        assert start.json()["estado"] == "ACCESO_NO_AUTORIZADO"

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
        flujos.REGISTROS_TELEFONO_PENDIENTES.clear()
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

        sender = FakeTelegramSender()
        app.dependency_overrides[get_db] = override_get_db
        app.dependency_overrides[get_optional_telegram_sender] = lambda: sender
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
        _asegurar_tipo_alertas(session)
        session.execute(
            text(
                """
                INSERT INTO telegram_contactos
                    (telegram_user_id, chat_id, telefono, nombres, activo)
                VALUES
                    (:telegram_user_id, :chat_id, :telefono, :nombres, true)
                """
            ),
            {"telegram_user_id": chat_id, "chat_id": chat_id, "telefono": telefono, "nombres": "GAD"},
        )

        def override_get_db() -> Generator[Session, None, None]:
            yield session

        sender = FakeTelegramSender()
        app.dependency_overrides[get_db] = override_get_db
        app.dependency_overrides[get_optional_telegram_sender] = lambda: sender
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

        sender = FakeTelegramSender()
        app.dependency_overrides[get_db] = override_get_db
        app.dependency_overrides[get_optional_telegram_sender] = lambda: sender
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
        assert sender.messages[-1]["text"] == (
            "Hola GAD. Por favor seleccione el tipo de alerta que desea enviar a los organismos de "
            "gesti\u00f3n de riesgos y de primera respuesta:"
        )
    finally:
        app.dependency_overrides.clear()
        session.close()
        transaction.rollback()
        connection.close()


def test_webhook_reporte_alerta_completo_desde_tipo_alerta() -> None:
    connection = engine.connect()
    transaction = connection.begin()
    session = Session(bind=connection, autoflush=False, expire_on_commit=False)
    chat_id = -(uuid4().int % 1000000000)

    try:
        _asegurar_catalogos_alertas(session)
        _asegurar_tabla_eventos(session)
        telefono = f"+593020{uuid4().int % 1000000:06d}"
        session.execute(
            text(
                """
                INSERT INTO telegram_contactos
                    (telegram_user_id, chat_id, telefono, nombres, activo)
                VALUES
                    (:telegram_user_id, :chat_id, :telefono, :nombres, true)
                """
            ),
            {"telegram_user_id": chat_id, "chat_id": chat_id, "telefono": telefono, "nombres": "GAD"},
        )

        def override_get_db() -> Generator[Session, None, None]:
            yield session

        sender = FakeTelegramSender()
        app.dependency_overrides[get_db] = override_get_db
        app.dependency_overrides[get_optional_telegram_sender] = lambda: sender
        client = TestClient(app)

        seleccion = client.post(
            "/api/telegram/webhook",
            json={
                "update_id": 120,
                "callback_query": {
                    "id": "callback-tipo-alerta",
                    "from": {"id": chat_id, "first_name": "GAD"},
                    "message": {
                        "chat": {"id": chat_id, "first_name": "GAD", "type": "private"},
                    },
                    "data": "TIPO_ALERTA:6",
                },
            },
        )

        assert seleccion.status_code == 200
        assert seleccion.json()["estado"] == "REPORTE_ALERTA_ENCUESTA_ENVIADA"
        assert sender.polls[-1]["poll"]["question"] == "Ingrese el NIVEL de alerta que usted visualiza:"
        assert sender.polls[-1]["poll"]["options"][0]["text"].startswith("\U0001f534 LLUVIA MUY FUERTE")

        encuesta = client.post(
            "/api/telegram/webhook",
            json={
                "update_id": 121,
                "poll_answer": {
                    "poll_id": "poll-alerta",
                    "user": {"id": chat_id, "first_name": "GAD"},
                    "option_ids": [1],
                },
            },
        )
        assert encuesta.status_code == 200
        assert encuesta.json()["estado"] == "ALERTA_NIVEL_RECIBIDO"

        ubicacion = client.post(
            "/api/telegram/webhook",
            json={
                "update_id": 122,
                "message": {
                    "from": {"id": chat_id, "first_name": "GAD"},
                    "chat": {"id": chat_id, "first_name": "GAD", "type": "private"},
                    "location": {"latitude": -0.1806532, "longitude": -78.4678382},
                },
            },
        )
        assert ubicacion.status_code == 200
        assert ubicacion.json()["estado"] == "ALERTA_UBICACION_RECIBIDA"

        descripcion = client.post(
            "/api/telegram/webhook",
            json={
                "update_id": 123,
                "message": {
                    "from": {"id": chat_id, "first_name": "GAD"},
                    "chat": {"id": chat_id, "first_name": "GAD", "type": "private"},
                    "text": "Comunidad San Jose, lluvia fuerte con acumulacion de agua.",
                },
            },
        )
        assert descripcion.status_code == 200
        assert descripcion.json()["estado"] == "ALERTA_DESCRIPCION_RECIBIDA"

        riesgo = client.post(
            "/api/telegram/webhook",
            json={
                "update_id": 124,
                "callback_query": {
                    "id": "callback-riesgo",
                    "from": {"id": chat_id, "first_name": "GAD"},
                    "message": {"chat": {"id": chat_id, "first_name": "GAD", "type": "private"}},
                    "data": "ALERTA_RIESGO:SI",
                },
            },
        )
        assert riesgo.status_code == 200
        assert riesgo.json()["estado"] == "ALERTA_RIESGO_PERSONAS_CONFIRMADO"

        cantidad = client.post(
            "/api/telegram/webhook",
            json={
                "update_id": 125,
                "message": {
                    "from": {"id": chat_id, "first_name": "GAD"},
                    "chat": {"id": chat_id, "first_name": "GAD", "type": "private"},
                    "text": "25",
                },
            },
        )
        assert cantidad.status_code == 200
        assert cantidad.json()["estado"] == "ALERTA_CANTIDAD_PERSONAS_RECIBIDA"

        foto = client.post(
            "/api/telegram/webhook",
            json={
                "update_id": 126,
                "message": {
                    "from": {"id": chat_id, "first_name": "GAD"},
                    "chat": {"id": chat_id, "first_name": "GAD", "type": "private"},
                    "photo": [
                        {"file_id": "foto-small", "file_unique_id": "unique-small", "width": 10, "height": 10},
                        {"file_id": "foto-big", "file_unique_id": "unique-big", "width": 100, "height": 100},
                    ],
                },
            },
        )
        assert foto.status_code == 200
        assert foto.json()["estado"] == "REPORTE_ALERTA_GUARDADO"
        assert sender.messages[-2]["text"] == (
            "Recomendaciones:\n"
            "- Evitar transitar en zonas inundadas.\n"
            "- No acercarse a postes, cables o arboles."
        )
        assert "Muchas gracias por tu reporte" in str(sender.messages[-1]["text"])

        evento = session.execute(
            text(
                """
                SELECT
                    e.tipo_alerta_id,
                    t.descripcion AS tipo_alerta,
                    e.alerta_encuesta_id,
                    ae.nombre AS nivel_alerta,
                    e.descripcion,
                    e.personas_en_riesgo,
                    e.cantidad_personas_riesgo,
                    e.foto_file_id,
                    e.foto_file_unique_id
                FROM telegram_eventos e
                LEFT JOIN tipo_alertas t ON t.id = e.tipo_alerta_id
                LEFT JOIN alerta_encuesta ae ON ae.id = e.alerta_encuesta_id
                WHERE e.contacto_id = :contacto_id
                """
            ),
            {"contacto_id": foto.json()["contacto_id"]},
        ).mappings().one()
        assert evento["tipo_alerta_id"] == 6
        assert evento["tipo_alerta"] == "LLUVIAS"
        assert evento["alerta_encuesta_id"] is not None
        assert evento["nivel_alerta"] == "LLUVIA FUERTE"
        assert evento["descripcion"] == "Comunidad San Jose, lluvia fuerte con acumulacion de agua."
        assert evento["personas_en_riesgo"] is True
        assert evento["cantidad_personas_riesgo"] == 25
        assert evento["foto_file_id"] == "foto-big"
        assert evento["foto_file_unique_id"] == "unique-big"
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
    get_settings_original = flujos.get_settings

    try:
        flujos.SCRIPT_BARRIDO_LLUVIA_TELEFONOS = [telefono_1, telefono_2]
        flujos.get_settings = lambda: type(
            "SettingsStub",
            (),
            {"telegram_admin_user_ids": {chat_id_admin}},
        )()
        session.execute(
            text(
                """
                INSERT INTO telegram_contactos
                    (telegram_user_id, chat_id, telefono, activo)
                VALUES
                    (:telegram_user_id_admin, :chat_id_admin, :telefono_admin, true),
                    (:telegram_user_id_1, :chat_id_1, :telefono_1, true),
                    (:telegram_user_id_2, :chat_id_2, :telefono_2, true)
                """
            ),
            {
                "telegram_user_id_admin": chat_id_admin,
                "chat_id_admin": chat_id_admin,
                "telefono_admin": f"+593099{uuid4().int % 1000000:06d}",
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
        assert menu.json()["estado"] == "MENU_SCRIPTS"

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
        flujos.get_settings = get_settings_original
        app.dependency_overrides.clear()
        session.close()
        transaction.rollback()
        connection.close()


def test_endpoint_reporte_alerta_devuelve_json_y_chart_url() -> None:
    connection = engine.connect()
    transaction = connection.begin()
    session = Session(bind=connection, autoflush=False, expire_on_commit=False)
    telefono = f"+593013{uuid4().int % 1000000:06d}"
    chat_id = -(uuid4().int % 1000000000)

    try:
        _asegurar_catalogos_alertas(session)
        _asegurar_tabla_eventos(session)
        session.execute(text("DELETE FROM telegram_eventos"))
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
        lluvia_fuerte_id = session.execute(
            text(
                """
                SELECT id
                FROM alerta_encuesta
                WHERE tipo_alerta_id = 6
                  AND nombre = 'LLUVIA FUERTE'
                LIMIT 1
                """
            )
        ).scalar_one()
        session.execute(
            text(
                """
                INSERT INTO telegram_eventos
                    (contacto_id, tipo_alerta_id, alerta_encuesta_id, descripcion, foto_file_id, latitud, longitud)
                VALUES
                    (:contacto_id, 6, :alerta_encuesta_id, 'Lluvia fuerte 1', 'foto-1', -0.1806532, -78.4678382),
                    (:contacto_id, 6, :alerta_encuesta_id, 'Lluvia fuerte 2', 'foto-2', -0.1806532, -78.4678382)
                """
            ),
            {"contacto_id": contacto_id, "alerta_encuesta_id": lluvia_fuerte_id},
        )

        def override_get_db() -> Generator[Session, None, None]:
            yield session

        app.dependency_overrides[get_db] = override_get_db
        client = TestClient(app)

        respuesta = client.get("/api/telegram/reportes/alertas/6")

        assert respuesta.status_code == 200
        data = respuesta.json()
        assert data["tipo_alerta_id"] == 6
        assert data["nombre_alerta"] == "LLUVIAS"
        assert data["total"] == 2
        cantidades = {item["nombre"]: item["cantidad"] for item in data["opciones"]}
        assert cantidades["LLUVIA FUERTE"] == 2
        assert data["chart_url"].startswith("https://quickchart.io/chart?")
    finally:
        app.dependency_overrides.clear()
        session.close()
        transaction.rollback()
        connection.close()


def test_webhook_reportes_muestra_menu_y_envia_texto_con_grafico() -> None:
    connection = engine.connect()
    transaction = connection.begin()
    session = Session(bind=connection, autoflush=False, expire_on_commit=False)
    telefono = f"+593014{uuid4().int % 1000000:06d}"
    chat_id = -(uuid4().int % 1000000000)
    get_settings_original = flujos.get_settings

    try:
        flujos.get_settings = lambda: type(
            "SettingsStub",
            (),
            {"telegram_admin_user_ids": {chat_id}},
        )()
        _asegurar_catalogos_alertas(session)
        _asegurar_tabla_eventos(session)
        session.execute(text("DELETE FROM telegram_eventos"))
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
        lluvia_fuerte_id = session.execute(
            text(
                """
                SELECT id
                FROM alerta_encuesta
                WHERE tipo_alerta_id = 6
                  AND nombre = 'LLUVIA FUERTE'
                LIMIT 1
                """
            )
        ).scalar_one()
        session.execute(
            text(
                """
                INSERT INTO telegram_eventos
                    (contacto_id, tipo_alerta_id, alerta_encuesta_id, descripcion, foto_file_id, latitud, longitud)
                VALUES
                    (:contacto_id, 6, :alerta_encuesta_id, 'Lluvia fuerte', 'foto-1', -0.1806532, -78.4678382)
                """
            ),
            {"contacto_id": contacto_id, "alerta_encuesta_id": lluvia_fuerte_id},
        )

        def override_get_db() -> Generator[Session, None, None]:
            yield session

        sender = FakeTelegramSender()
        app.dependency_overrides[get_db] = override_get_db
        app.dependency_overrides[get_optional_telegram_sender] = lambda: sender
        client = TestClient(app)

        menu = client.post(
            "/api/telegram/webhook",
            json={
                "update_id": 100,
                "message": {
                    "from": {"id": chat_id, "first_name": "GAD"},
                    "chat": {"id": chat_id, "first_name": "GAD", "type": "private"},
                    "text": "/reportes",
                },
            },
        )

        assert menu.status_code == 200
        assert menu.json()["estado"] == "MENU_REPORTES"
        assert sender.messages[-1]["text"] == "Seleccione el tipo de alerta del reporte que desea visualizar:"
        assert sender.messages[-1]["reply_markup"]["inline_keyboard"][5][0]["callback_data"] == "REPORTE_ALERTA:6"

        respuesta = client.post(
            "/api/telegram/webhook",
            json={
                "update_id": 110,
                "callback_query": {
                    "id": "callback-reporte-alerta",
                    "from": {"id": chat_id, "first_name": "GAD"},
                    "message": {
                        "chat": {"id": chat_id, "first_name": "GAD", "type": "private"},
                    },
                    "data": "REPORTE_ALERTA:6",
                },
            },
        )

        assert respuesta.status_code == 200
        data = respuesta.json()
        assert data["estado"] == "REPORTE_ALERTA_ENVIADO"
        assert data["mensaje"] == "Reporte generado para LLUVIAS."
        assert "Reporte de alertas: LLUVIAS" in sender.messages[-1]["text"]
        assert "- LLUVIA FUERTE: 1" in sender.messages[-1]["text"]
        assert sender.photos[-1]["photo"].startswith("https://quickchart.io/chart?")
    finally:
        flujos.get_settings = get_settings_original
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


def test_endpoint_lista_eventos_con_nombre_alerta_y_campos_filtrados() -> None:
    connection = engine.connect()
    transaction = connection.begin()
    session = Session(bind=connection, autoflush=False, expire_on_commit=False)
    telefono = f"+593010{uuid4().int % 1000000:06d}"
    chat_id = -(uuid4().int % 1000000000)

    try:
        _asegurar_tipo_alertas(session)
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
                    (
                        contacto_id,
                        tipo_alerta_id,
                        descripcion,
                        cantidad_personas_riesgo,
                        foto_file_id,
                        foto_file_unique_id,
                        latitud,
                        longitud,
                        fecha_reporte
                    )
                VALUES
                    (
                        :contacto_id,
                        6,
                        :descripcion,
                        15,
                        :foto_file_id,
                        :foto_file_unique_id,
                        :latitud,
                        :longitud,
                        now()
                    )
                RETURNING id
                """
            ),
            {
                "contacto_id": contacto_id,
                "descripcion": "Lluvia fuerte en la comunidad.",
                "foto_file_id": "foto-evento",
                "foto_file_unique_id": "foto-evento-unique",
                "latitud": -0.1806532,
                "longitud": -78.4678382,
            },
        ).scalar_one()

        def override_get_db() -> Generator[Session, None, None]:
            yield session

        app.dependency_overrides[get_db] = override_get_db
        client = TestClient(app)

        respuesta = client.get("/api/telegram/eventos")

        assert respuesta.status_code == 200
        data = respuesta.json()
        item = next(evento for evento in data if evento["id"] == evento_id)
        assert item["contacto_id"] == contacto_id
        assert item["tipo_alerta_id"] == 6
        assert item["nombre_alerta"] == "LLUVIAS"
        assert item["descripcion"] == "Lluvia fuerte en la comunidad."
        assert item["cantidad_personas_riesgo"] == 15
        assert item["fecha_reporte"][2] == "/"
        assert item["fecha_reporte"][5] == "/"
        assert item["fecha_reporte"][10] == " "
        assert item["fecha_reporte"][13] == ":"
        assert item["foto_url"].endswith(f"/api/telegram/eventos/{evento_id}/foto")
        assert "activo" not in item
        assert "fecha_creacion" not in item
        assert "personas_en_riesgo" not in item
        assert "foto_file_id" not in item
        assert "foto_file_unique_id" not in item
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
