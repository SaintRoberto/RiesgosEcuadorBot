from collections.abc import Generator
from contextlib import contextmanager
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

from app import ubicacion
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
        if file_id == "foto-sin-extension":
            return {"ok": True, "result": {"file_id": file_id, "file_path": "photos/evento-test"}}
        return {"ok": True, "result": {"file_id": file_id, "file_path": "photos/evento-test.jpg"}}

    def download_file(self, file_path: str) -> bytes:
        return b"\xff\xd8\xff\xe0fake-jpeg-content"


def test_extrae_ubicacion_administrativa_con_fallback_de_nominatim() -> None:
    ubicacion_admin = ubicacion.extraer_ubicacion_administrativa_desde_address(
        {
            "residential": "Coop. Puerto Rico",
            "suburb": "Terminal Portuario Internacional Puerto Hondo S.A. - TPI",
            "village": "Puerto Hondo",
            "city": "Guayaquil",
            "county": "Guayaquil",
            "state": "Guayas",
            "country_code": "ec",
        }
    )

    assert ubicacion_admin == {
        "provincia": "Guayas",
        "canton": "Guayaquil",
        "parroquia": "Puerto Hondo",
    }


def test_extrae_parroquia_mas_especifica_que_canton() -> None:
    ubicacion_admin = ubicacion.extraer_ubicacion_administrativa_desde_address(
        {
            "suburb": "La Puntilla",
            "town": "Samborondon",
            "county": "Samborondon",
            "state": "Guayas",
            "country_code": "ec",
        }
    )

    assert ubicacion_admin == {
        "provincia": "Guayas",
        "canton": "Samborondon",
        "parroquia": "La Puntilla",
    }


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
                provincia character varying(150),
                canton character varying(150),
                parroquia character varying(150),
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
    session.execute(text("ALTER TABLE public.telegram_eventos ADD COLUMN IF NOT EXISTS provincia character varying(150)"))
    session.execute(text("ALTER TABLE public.telegram_eventos ADD COLUMN IF NOT EXISTS canton character varying(150)"))
    session.execute(text("ALTER TABLE public.telegram_eventos ADD COLUMN IF NOT EXISTS parroquia character varying(150)"))
    session.execute(
        text("ALTER TABLE public.telegram_eventos ADD COLUMN IF NOT EXISTS personas_en_riesgo boolean NOT NULL DEFAULT false")
    )
    session.execute(
        text(
            "ALTER TABLE public.telegram_eventos "
            "ADD COLUMN IF NOT EXISTS cantidad_personas_riesgo integer NOT NULL DEFAULT 0"
        )
    )


def _asegurar_tablas_barridos(session: Session) -> None:
    session.execute(text("DROP TABLE IF EXISTS public.telegram_barrido_respuestas"))
    session.execute(text("DROP TABLE IF EXISTS public.telegram_barridos"))
    session.execute(
        text(
            """
            CREATE TABLE public.telegram_barridos
            (
                id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
                tipo_alerta_id bigint NOT NULL,
                codigo character varying(150),
                mensaje text,
                fecha_barrido timestamp with time zone NOT NULL DEFAULT now(),
                activo boolean NOT NULL DEFAULT true,
                fecha_creacion timestamp with time zone NOT NULL DEFAULT now(),
                CONSTRAINT fk_telegram_barridos_tipo_alerta
                    FOREIGN KEY (tipo_alerta_id)
                    REFERENCES public.tipo_alertas (id)
                    ON UPDATE CASCADE
                    ON DELETE RESTRICT
            )
            """
        )
    )
    session.execute(
        text(
            """
            CREATE TABLE public.telegram_barrido_respuestas
            (
                id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
                barrido_id bigint NOT NULL,
                contacto_id bigint NOT NULL,
                alerta_encuesta_id bigint NOT NULL,
                latitud numeric(10,7) NOT NULL,
                longitud numeric(10,7) NOT NULL,
                provincia character varying(150),
                canton character varying(150),
                parroquia character varying(150),
                descripcion text,
                personas_en_riesgo boolean NOT NULL DEFAULT false,
                cantidad_personas_riesgo integer NOT NULL DEFAULT 0,
                foto_file_id text,
                foto_file_unique_id text,
                fecha_respuesta timestamp with time zone NOT NULL DEFAULT now(),
                activo boolean NOT NULL DEFAULT true,
                fecha_creacion timestamp with time zone NOT NULL DEFAULT now(),
                CONSTRAINT fk_telegram_barrido_respuestas_barrido
                    FOREIGN KEY (barrido_id)
                    REFERENCES public.telegram_barridos (id)
                    ON UPDATE CASCADE
                    ON DELETE RESTRICT,
                CONSTRAINT fk_telegram_barrido_respuestas_contacto
                    FOREIGN KEY (contacto_id)
                    REFERENCES public.telegram_contactos (id)
                    ON UPDATE CASCADE
                    ON DELETE RESTRICT,
                CONSTRAINT fk_telegram_barrido_respuestas_alerta_encuesta
                    FOREIGN KEY (alerta_encuesta_id)
                    REFERENCES public.alerta_encuesta (id)
                    ON UPDATE CASCADE
                    ON DELETE RESTRICT,
                CONSTRAINT chk_telegram_barrido_respuestas_latitud
                    CHECK (latitud >= -90 AND latitud <= 90),
                CONSTRAINT chk_telegram_barrido_respuestas_longitud
                    CHECK (longitud >= -180 AND longitud <= 180),
                CONSTRAINT uq_telegram_barrido_respuesta_contacto
                    UNIQUE (barrido_id, contacto_id)
            )
            """
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
            CREATE TABLE IF NOT EXISTS public.tipo_flujo
            (
                id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
                codigo varchar(30) NOT NULL UNIQUE,
                descripcion varchar(100) NOT NULL,
                activo boolean NOT NULL DEFAULT true,
                fecha_creacion timestamp with time zone NOT NULL DEFAULT now()
            )
            """
        )
    )
    session.execute(
        text(
            """
            INSERT INTO public.tipo_flujo (id, codigo, descripcion)
            OVERRIDING SYSTEM VALUE
            VALUES
                (1, 'ALERTA', 'Reporte de alerta'),
                (2, 'BARRIDO', 'Reporte de barrido'),
                (3, 'AMBOS', 'Reporte de alerta y barrido')
            ON CONFLICT (id) DO UPDATE
            SET codigo = EXCLUDED.codigo,
                descripcion = EXCLUDED.descripcion,
                activo = true
            """
        )
    )
    session.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS public.alerta_encuesta
            (
                id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
                tipo_alerta_id bigint NOT NULL,
                tipo_flujo_id bigint NOT NULL DEFAULT 3,
                nombre varchar(150) NOT NULL,
                descripcion text,
                color varchar(20),
                orden integer NOT NULL,
                activo boolean NOT NULL DEFAULT true,
                fecha_creacion timestamp with time zone NOT NULL DEFAULT now(),
                CONSTRAINT fk_alerta_encuesta_tipo_alerta
                    FOREIGN KEY (tipo_alerta_id)
                    REFERENCES public.tipo_alertas (id),
                CONSTRAINT fk_alerta_encuesta_tipo_flujo
                    FOREIGN KEY (tipo_flujo_id)
                    REFERENCES public.tipo_flujo (id),
                CONSTRAINT uq_alerta_encuesta_tipo_flujo_orden
                    UNIQUE (tipo_alerta_id, tipo_flujo_id, orden)
            )
            """
        )
    )
    session.execute(text("ALTER TABLE public.alerta_encuesta ADD COLUMN IF NOT EXISTS color varchar(20)"))
    session.execute(text("ALTER TABLE public.alerta_encuesta ADD COLUMN IF NOT EXISTS tipo_flujo_id bigint"))
    session.execute(
        text(
            """
            UPDATE public.alerta_encuesta
            SET tipo_flujo_id = 3
            WHERE tipo_flujo_id IS NULL
            """
        )
    )
    session.execute(text("ALTER TABLE public.alerta_encuesta ALTER COLUMN tipo_flujo_id SET NOT NULL"))
    session.execute(text("ALTER TABLE public.alerta_encuesta DROP CONSTRAINT IF EXISTS uq_alerta_encuesta_tipo_orden"))
    session.execute(text("DROP INDEX IF EXISTS public.uq_alerta_encuesta_tipo_orden"))
    session.execute(
        text(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS uq_alerta_encuesta_tipo_flujo_orden
                ON public.alerta_encuesta (tipo_alerta_id, tipo_flujo_id, orden)
            """
        )
    )
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
            INSERT INTO public.alerta_encuesta (tipo_alerta_id, tipo_flujo_id, orden, nombre, descripcion, color)
            VALUES
                (6, 3, 1, 'LLUVIA MUY FUERTE', 'Rapido anegamiento de calles.', 'rojo'),
                (6, 3, 2, 'LLUVIA FUERTE', 'Poco anegamiento de calles que dificulta movilidad.', 'amarillo'),
                (6, 3, 3, 'LLUVIA MODERADA', 'Visibilidad reducida y acumulacion leve de agua.', 'verde'),
                (6, 2, 4, 'LLUVIA DEBIL', 'Precipitacion ligera sin acumulacion relevante.', 'verde'),
                (4, 3, 1, 'MUY FUERTE', 'Panico general, personas pierden estabilidad.', 'rojo')
            ON CONFLICT (tipo_alerta_id, tipo_flujo_id, orden) DO UPDATE
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
        _asegurar_catalogos_alertas(session)
        _asegurar_tablas_barridos(session)
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
    assert "/api/telegram/barridos/tipo-alerta/{tipo_alerta_id}" in paths
    assert "/api/telegram/barridos" not in paths
    assert "/api/telegram/barridos/solicitudes" in paths
    assert "/api/telegram/barridos/respuestas" in paths
    assert "/api/telegram/reportes/alertas/{tipo_alerta_id}" in paths
    assert "/api/telegram/reportes/barridos/tipo-alerta/{tipo_alerta_id}" in paths
    assert "/api/telegram/reportes/tipo-alerta/{tipo_alerta_id}/barridos/{barrido_id}" in paths
    assert "/api/telegram/reportes/barridos/{barrido_id}" not in paths
    assert "/api/telegram/tipo-alertas" in paths
    assert "/api/telegram/tipo-alertas/{tipo_alerta_id}" in paths
    assert "/api/telegram/tipo-flujos" in paths
    assert "/api/telegram/alerta-encuesta" in paths
    assert "/api/telegram/tipo-alertas/{tipo_alerta_id}/encuesta" in paths
    assert "/api/telegram/alerta-recomendaciones" in paths
    assert "/api/telegram/tipo-alertas/{tipo_alerta_id}/recomendaciones" in paths
    assert "/api/telegram/eventos" in paths
    assert "/api/telegram/eventos/fotos" in paths
    assert "/api/telegram/eventos/{evento_id}/foto" in paths
    assert "/api/telegram/eventos/seguimientos" in paths


def test_admin_telegram_user_ids_requiere_env_con_corchetes() -> None:
    get_settings_original = flujos.get_settings
    try:
        flujos.get_settings = lambda: type(
            "SettingsStub",
            (),
            {"telegram_admin_user_ids": "[6869758976,1234567890]"},
        )()
        assert flujos._admin_telegram_user_ids() == {6869758976, 1234567890}

        flujos.get_settings = lambda: type(
            "SettingsStub",
            (),
            {"telegram_admin_user_ids": "6869758976,1234567890"},
        )()
        assert flujos._admin_telegram_user_ids() == set()
    finally:
        flujos.get_settings = get_settings_original


def test_endpoint_tipo_alertas_lista_catalogo() -> None:
    connection = engine.connect()
    transaction = connection.begin()
    session = Session(bind=connection, autoflush=False, expire_on_commit=False)

    try:
        _asegurar_catalogos_alertas(session)

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
        assert [item["color"] for item in encuesta_data] == ["rojo", "amarillo", "verde"]
        assert all(item["tipo_flujo_id"] == 3 for item in encuesta_data)

        encuesta_barrido = client.get(
            "/api/telegram/tipo-alertas/6/encuesta",
            params={"tipo_flujo_codigo": "BARRIDO"},
        )
        assert encuesta_barrido.status_code == 200
        assert [item["nombre"] for item in encuesta_barrido.json()] == [
            "LLUVIA MUY FUERTE",
            "LLUVIA FUERTE",
            "LLUVIA MODERADA",
            "LLUVIA DEBIL",
        ]

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


def test_flujo_boletin_barrido_y_seguimiento(monkeypatch) -> None:
    monkeypatch.setattr(
        flujos,
        "_resolver_ubicacion_administrativa",
        lambda latitud, longitud: {"provincia": "Pichincha", "canton": "Quito", "parroquia": "Iñaquito"},
    )
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
        assert solicitud.json()["barrido_id"] > 0
        assert solicitud.json()["registros"][0]["estado"] == "PROCESANDO"
        alerta_encuesta_id = client.get("/api/telegram/alerta-encuesta?tipo_alerta_id=6").json()[0]["id"]

        respuesta = client.post(
            "/api/telegram/barridos/respuestas",
            json={
                "telefono": telefono,
                "codigo": "BARRIDO-TEST",
                "alerta_encuesta_id": alerta_encuesta_id,
                "latitud": -0.1806532,
                "longitud": -78.4678382,
                "observacion": "Lluvia sostenida",
            },
        )
        assert respuesta.status_code == 201
        assert respuesta.json()["estado"] == "COMPLETADA"
        assert respuesta.json()["barrido_id"] > 0
        assert respuesta.json()["barrido_respuesta_id"] > 0
        assert respuesta.json()["tipo_alerta_id"] == 6
        assert respuesta.json()["alerta_encuesta_id"] == alerta_encuesta_id

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
                    (telegram_user_id, chat_id, telefono, nombres, activo)
                VALUES
                    (0, 0, :telefono, 'Nombre Precargado', true)
                """
            ),
            {"telefono": telefono},
        )

        def override_get_db() -> Generator[Session, None, None]:
            yield session

        sender = FakeTelegramSender()
        app.dependency_overrides[get_db] = override_get_db
        app.dependency_overrides[get_telegram_sender] = lambda: sender
        app.dependency_overrides[get_optional_telegram_sender] = lambda: sender
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
        assert start.json()["estado"] == "ESPERANDO_TELEFONO"
        assert "reply_markup" not in sender.messages[-1]

        registro = client.post(
            "/api/telegram/webhook",
            json={
                "update_id": 3,
                "message": {
                    "from": {"id": chat_id, "first_name": "David"},
                    "chat": {"id": chat_id, "first_name": "David", "type": "private"},
                    "text": f"mi numero es {telefono}",
                    "entities": [
                        {
                            "type": "phone_number",
                            "offset": len("mi numero es "),
                            "length": len(telefono),
                        }
                    ],
                },
            },
        )
        assert registro.status_code == 200
        assert registro.json()["estado"] == "REGISTRADO"
        assert registro.json()["telefono"] == telefono
        assert registro.json()["chat_id"] == chat_id
        assert sender.messages[-1]["text"] == "Hola Nombre Precargado"
        assert "reply_markup" not in sender.messages[-1]
        assert all("CAIDA DE CENIZA" not in str(message.get("reply_markup")) for message in sender.messages)

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
        assert row["nombres"] == "Nombre Precargado"
        assert row["activo"] is True
    finally:
        flujos.REGISTROS_TELEFONO_PENDIENTES.clear()
        app.dependency_overrides.clear()
        session.close()
        transaction.rollback()
        connection.close()


def test_webhook_registra_telefono_con_espacios_y_formato_local() -> None:
    connection = engine.connect()
    transaction = connection.begin()
    session = Session(bind=connection, autoflush=False, expire_on_commit=False)
    sufijo = f"{uuid4().int % 100000000:08d}"
    telefono_local = f"09{sufijo}"
    telefono_telegram = f"+593 9{sufijo[0:1]} {sufijo[1:4]} {sufijo[4:]}"
    chat_id = -(uuid4().int % 1000000000)

    try:
        _asegurar_tipo_alertas(session)
        flujos.REGISTROS_TELEFONO_PENDIENTES.clear()
        session.execute(
            text(
                """
                INSERT INTO telegram_contactos
                    (telegram_user_id, chat_id, telefono, nombres, activo)
                VALUES
                    (0, 0, :telefono, 'David', true)
                """
            ),
            {"telefono": telefono_local},
        )

        def override_get_db() -> Generator[Session, None, None]:
            yield session

        sender = FakeTelegramSender()
        app.dependency_overrides[get_db] = override_get_db
        app.dependency_overrides[get_telegram_sender] = lambda: sender
        app.dependency_overrides[get_optional_telegram_sender] = lambda: sender
        client = TestClient(app)

        solicitud_registro = client.post(
            "/api/telegram/webhook",
            json={
                "update_id": 4,
                "message": {
                    "from": {"id": chat_id, "first_name": "David"},
                    "chat": {"id": chat_id, "first_name": "David", "type": "private"},
                    "text": "/registrar",
                },
            },
        )
        assert solicitud_registro.status_code == 200
        assert solicitud_registro.json()["estado"] == "ESPERANDO_TELEFONO"

        respuesta = client.post(
            "/api/telegram/webhook",
            json={
                "update_id": 5,
                "message": {
                    "from": {"id": chat_id, "first_name": "David"},
                    "chat": {"id": chat_id, "first_name": "David", "type": "private"},
                    "text": f"mi numero es {telefono_telegram}",
                    "entities": [
                        {
                            "type": "phone_number",
                            "offset": len("mi numero es "),
                            "length": len(telefono_telegram),
                        }
                    ],
                },
            },
        )

        assert respuesta.status_code == 200
        assert respuesta.json()["estado"] == "REGISTRADO"
        assert respuesta.json()["telefono"] == telefono_local
        assert sender.messages[-1]["text"] == "Hola David"
    finally:
        flujos.REGISTROS_TELEFONO_PENDIENTES.clear()
        app.dependency_overrides.clear()
        session.close()
        transaction.rollback()
        connection.close()


def test_webhook_hola_no_activa_registro_de_telefono() -> None:
    connection = engine.connect()
    transaction = connection.begin()
    session = Session(bind=connection, autoflush=False, expire_on_commit=False)
    chat_id = -(uuid4().int % 1000000000)

    try:
        _asegurar_tipo_alertas(session)
        flujos.REGISTROS_TELEFONO_PENDIENTES.clear()

        def override_get_db() -> Generator[Session, None, None]:
            yield session

        sender = FakeTelegramSender()
        app.dependency_overrides[get_db] = override_get_db
        app.dependency_overrides[get_optional_telegram_sender] = lambda: sender
        client = TestClient(app)

        respuesta = client.post(
            "/api/telegram/webhook",
            json={
                "update_id": 6,
                "message": {
                    "from": {"id": chat_id, "first_name": "David"},
                    "chat": {"id": chat_id, "first_name": "David", "type": "private"},
                    "text": "hola",
                },
            },
        )

        assert respuesta.status_code == 200
        assert respuesta.json()["estado"] == "ACCESO_NO_AUTORIZADO"
        assert sender.messages[-1]["text"] == flujos.MENSAJE_ACCESO_NO_AUTORIZADO
        assert chat_id not in flujos.REGISTROS_TELEFONO_PENDIENTES
    finally:
        flujos.REGISTROS_TELEFONO_PENDIENTES.clear()
        app.dependency_overrides.clear()
        session.close()
        transaction.rollback()
        connection.close()


def test_webhook_guarda_barrido_con_ubicacion_y_encuesta(monkeypatch) -> None:
    connection = engine.connect()
    transaction = connection.begin()
    session = Session(bind=connection, autoflush=False, expire_on_commit=False)
    telefono = f"+593003{uuid4().int % 1000000:06d}"
    chat_id = -(uuid4().int % 1000000000)

    try:
        monkeypatch.setattr(
            flujos,
            "_resolver_ubicacion_administrativa",
            lambda latitud, longitud: {"provincia": "Guayas", "canton": "Guayaquil", "parroquia": "Tarqui"},
        )
        _asegurar_catalogos_alertas(session)
        _asegurar_tablas_barridos(session)
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
        assert sender.polls[-1]["poll"]["question"] == "Ingrese el NIVEL de alerta que usted visualiza:"
        assert "LLUVIA MUY FUERTE" in sender.polls[-1]["poll"]["options"][0]["text"]
        assert "LLUVIA FUERTE" in sender.polls[-1]["poll"]["options"][1]["text"]
        assert "LLUVIA DEBIL" in sender.polls[-1]["poll"]["options"][3]["text"]

        nivel = client.post(
            "/api/telegram/webhook",
            json={
                "update_id": 10,
                "poll_answer": {
                    "poll_id": "poll-test",
                    "user": {"id": chat_id, "first_name": "GAD"},
                    "option_ids": [1],
                },
            },
        )
        assert nivel.status_code == 200
        assert nivel.json()["estado"] == "BARRIDO_NIVEL_RECIBIDO"

        ubicacion = client.post(
            "/api/telegram/webhook",
            json={
                "update_id": 11,
                "message": {
                    "from": {"id": chat_id, "first_name": "GAD"},
                    "chat": {"id": chat_id, "first_name": "GAD", "type": "private"},
                    "location": {"latitude": -0.1806532, "longitude": -78.4678382},
                },
            },
        )
        assert ubicacion.status_code == 200
        assert ubicacion.json()["estado"] == "BARRIDO_UBICACION_RECIBIDA"

        descripcion = client.post(
            "/api/telegram/webhook",
            json={
                "update_id": 12,
                "message": {
                    "from": {"id": chat_id, "first_name": "GAD"},
                    "chat": {"id": chat_id, "first_name": "GAD", "type": "private"},
                    "text": "Comunidad San Jose, lluvia fuerte con acumulacion de agua.",
                },
            },
        )
        assert descripcion.status_code == 200
        assert descripcion.json()["estado"] == "BARRIDO_DESCRIPCION_RECIBIDA"

        riesgo = client.post(
            "/api/telegram/webhook",
            json={
                "update_id": 13,
                "callback_query": {
                    "id": "callback-barrido-riesgo-no",
                    "from": {"id": chat_id, "first_name": "GAD"},
                    "message": {"chat": {"id": chat_id, "first_name": "GAD", "type": "private"}},
                    "data": "ALERTA_RIESGO:NO",
                },
            },
        )
        assert riesgo.status_code == 200
        assert riesgo.json()["estado"] == "BARRIDO_RIESGO_PERSONAS_CONFIRMADO"

        foto = client.post(
            "/api/telegram/webhook",
            json={
                "update_id": 14,
                "message": {
                    "from": {"id": chat_id, "first_name": "GAD"},
                    "chat": {"id": chat_id, "first_name": "GAD", "type": "private"},
                    "photo": [{"file_id": "foto-barrido", "file_unique_id": "unique-barrido"}],
                },
            },
        )
        assert foto.status_code == 200
        assert foto.json()["estado"] == "BARRIDO_REGISTRADO"
        assert sender.messages[-1]["reply_markup"] == {"remove_keyboard": True}

        row = session.execute(
            text(
                """
                SELECT
                    ae.nombre AS nivel,
                    br.latitud,
                    br.longitud,
                    b.tipo_alerta_id,
                    br.descripcion,
                    br.personas_en_riesgo,
                    br.cantidad_personas_riesgo,
                    br.foto_file_id,
                    br.provincia,
                    br.canton,
                    br.parroquia
                FROM telegram_barrido_respuestas br
                JOIN telegram_barridos b ON b.id = br.barrido_id
                JOIN telegram_contactos c ON c.id = br.contacto_id
                JOIN alerta_encuesta ae ON ae.id = br.alerta_encuesta_id
                WHERE c.telefono = :telefono
                """
            ),
            {"telefono": telefono},
        ).mappings().one()
        assert row["nivel"] == "LLUVIA FUERTE"
        assert row["tipo_alerta_id"] == 6
        assert float(row["latitud"]) == -0.1806532
        assert float(row["longitud"]) == -78.4678382
        assert row["descripcion"] == "Comunidad San Jose, lluvia fuerte con acumulacion de agua."
        assert row["personas_en_riesgo"] is False
        assert row["cantidad_personas_riesgo"] == 0
        assert row["foto_file_id"] == "foto-barrido"
        assert row["provincia"] == "Guayas"
        assert row["canton"] == "Guayaquil"
        assert row["parroquia"] == "Tarqui"
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
        _asegurar_catalogos_alertas(session)
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


def test_webhook_reporte_alerta_completo_desde_tipo_alerta(monkeypatch) -> None:
    connection = engine.connect()
    transaction = connection.begin()
    session = Session(bind=connection, autoflush=False, expire_on_commit=False)
    chat_id = -(uuid4().int % 1000000000)

    try:
        monkeypatch.setattr(
            flujos,
            "_resolver_ubicacion_administrativa",
            lambda latitud, longitud: {"provincia": "Guayas", "canton": "Guayaquil", "parroquia": "Tarqui"},
        )
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
        assert sender.polls[-1]["poll"]["options"][0]["text"].startswith("rojo LLUVIA MUY FUERTE")
        assert len(sender.polls[-1]["poll"]["options"]) == 3

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

        descripcion_con_emoji = client.post(
            "/api/telegram/webhook",
            json={
                "update_id": 1230,
                "message": {
                    "from": {"id": chat_id, "first_name": "GAD"},
                    "chat": {"id": chat_id, "first_name": "GAD", "type": "private"},
                    "text": "Comunidad San Jose, lluvia fuerte \U0001f327\ufe0f",
                },
            },
        )
        assert descripcion_con_emoji.status_code == 200
        assert descripcion_con_emoji.json()["estado"] == "ALERTA_DESCRIPCION_CON_EMOJI"
        assert "sin emojis" in str(sender.messages[-1]["text"])

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
        assert sender.messages[-1]["reply_markup"] == {"remove_keyboard": True}

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
                    e.foto_file_unique_id,
                    e.provincia,
                    e.canton,
                    e.parroquia
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
        assert evento["provincia"] == "Guayas"
        assert evento["canton"] == "Guayaquil"
        assert evento["parroquia"] == "Tarqui"
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
    telefono_admin = f"+593099{uuid4().int % 1000000:06d}"
    telefono_sin_telegram = f"+593099{uuid4().int % 1000000:06d}"
    get_settings_original = flujos.get_settings

    try:
        _asegurar_catalogos_alertas(session)
        _asegurar_tablas_barridos(session)
        flujos.get_settings = lambda: type(
            "SettingsStub",
            (),
            {"telegram_admin_user_ids": {chat_id_admin}},
        )()
        session.execute(text("UPDATE telegram_contactos SET activo = false"))
        session.execute(
            text(
                """
                INSERT INTO telegram_contactos
                    (telegram_user_id, chat_id, telefono, nombres, activo)
                VALUES
                    (:telegram_user_id_admin, :chat_id_admin, :telefono_admin, 'Admin GAD', true),
                    (:telegram_user_id_1, :chat_id_1, :telefono_1, 'Usuario Uno', true),
                    (:telegram_user_id_2, :chat_id_2, :telefono_2, 'Usuario Dos', true),
                    (0, 0, :telefono_sin_telegram, 'Sin Telegram', true)
                """
            ),
            {
                "telegram_user_id_admin": chat_id_admin,
                "chat_id_admin": chat_id_admin,
                "telefono_admin": telefono_admin,
                "telegram_user_id_1": chat_id_1,
                "chat_id_1": chat_id_1,
                "telefono_1": telefono_1,
                "telegram_user_id_2": chat_id_2,
                "chat_id_2": chat_id_2,
                "telefono_2": telefono_2,
                "telefono_sin_telegram": telefono_sin_telegram,
            },
        )

        def override_get_db() -> Generator[Session, None, None]:
            yield session

        app.dependency_overrides[get_db] = override_get_db
        sender = FakeTelegramSender()
        app.dependency_overrides[get_optional_telegram_sender] = lambda: sender
        client = TestClient(app)

        menu = client.post(
            "/api/telegram/webhook",
            json={
                "update_id": 80,
                "message": {
                    "from": {"id": chat_id_admin, "first_name": "Admin"},
                    "chat": {"id": chat_id_admin, "first_name": "Admin", "type": "private"},
                    "text": "/barridos",
                },
            },
        )
        assert menu.status_code == 200
        assert menu.json()["estado"] == "MENU_SCRIPTS"
        assert sender.messages[-1]["reply_markup"]["inline_keyboard"][0][0]["text"] == (
            "Barrido de ca\u00edda de ceniza"
        )
        assert sender.messages[-1]["reply_markup"]["inline_keyboard"][5][0]["text"] == (
            "Barrido de lluvias"
        )
        assert sender.messages[-1]["reply_markup"]["inline_keyboard"][5][0]["callback_data"] == "SCRIPT_ALERTA:6"

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
                    "data": "SCRIPT_ALERTA:6",
                },
            },
        )
        assert ejecucion.status_code == 200
        assert ejecucion.json()["estado"] == "SCRIPT_BARRIDO_EJECUTADO"
        assert "Barrido id:" in ejecucion.json()["mensaje"]
        barrido_id = int(str(ejecucion.json()["mensaje"]).split("Barrido id: ")[1].split(".")[0])
        fecha_hoy = flujos.date.today().strftime("%d-%m-%Y")
        mensajes_barrido = [
            mensaje["text"]
            for mensaje in sender.messages
            if str(mensaje["text"]).startswith("Hola ") and "ha ejecutado el barrido" in str(mensaje["text"])
        ]
        assert len(mensajes_barrido) == 3
        assert (
            f"Hola Usuario Uno la SNGR ha ejecutado el barrido por LLUVIAS No. {barrido_id} "
            f"para el {fecha_hoy}, ayudame registrando como percibes LLUVIAS en tu ubicacion actual:"
        ) in mensajes_barrido

        total = session.execute(
            text(
                """
                SELECT count(*)
                FROM telegram_consultas tc
                JOIN telegram_contactos c ON c.id = tc.contacto_id
                WHERE c.telefono IN (:telefono_admin, :telefono_1, :telefono_2)
                  AND tc.tipo_consulta = 'BARRIDO_GAD'
                  AND tc.estado = 'PROCESANDO'
                  AND tc.codigo = 'BARRIDO-AUTO'
                """
            ),
            {
                "telefono_admin": telefono_admin,
                "telefono_1": telefono_1,
                "telefono_2": telefono_2,
            },
        ).scalar_one()
        assert total == 3

        barridos = session.execute(
            text(
                """
                SELECT count(DISTINCT (tc.parametros->>'barrido_id')::bigint)
                FROM telegram_consultas tc
                JOIN telegram_contactos c ON c.id = tc.contacto_id
                WHERE c.telefono IN (:telefono_admin, :telefono_1, :telefono_2)
                  AND tc.tipo_consulta = 'BARRIDO_GAD'
                  AND tc.codigo = 'BARRIDO-AUTO'
                """
            ),
            {
                "telefono_admin": telefono_admin,
                "telefono_1": telefono_1,
                "telefono_2": telefono_2,
            },
        ).scalar_one()
        assert barridos == 1

        sin_telegram = session.execute(
            text(
                """
                SELECT count(*)
                FROM telegram_consultas tc
                JOIN telegram_contactos c ON c.id = tc.contacto_id
                WHERE c.telefono = :telefono
                  AND tc.tipo_consulta = 'BARRIDO_GAD'
                """
            ),
            {"telefono": telefono_sin_telegram},
        ).scalar_one()
        assert sin_telegram == 0
    finally:
        flujos.get_settings = get_settings_original
        app.dependency_overrides.clear()
        session.close()
        transaction.rollback()
        connection.close()


def test_barrido_por_tipo_alerta_lanza_misma_encuesta_de_alerta(monkeypatch) -> None:
    connection = engine.connect()
    transaction = connection.begin()
    session = Session(bind=connection, autoflush=False, expire_on_commit=False)
    telefono = f"+593015{uuid4().int % 1000000:06d}"
    chat_id = -(uuid4().int % 1000000000)
    get_settings_original = flujos.get_settings

    try:
        monkeypatch.setattr(
            flujos,
            "_resolver_ubicacion_administrativa",
            lambda latitud, longitud: {"provincia": "Guayas", "canton": "Guayaquil", "parroquia": "Tarqui"},
        )
        _asegurar_catalogos_alertas(session)
        _asegurar_tablas_barridos(session)
        flujos.get_settings = lambda: type(
            "SettingsStub",
            (),
            {"telegram_admin_user_ids": {chat_id}},
        )()
        session.execute(text("UPDATE telegram_contactos SET activo = false"))
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

        ejecucion = client.post(
            "/api/telegram/webhook",
            json={
                "update_id": 83,
                "callback_query": {
                    "id": "callback-script-sismo",
                    "from": {"id": chat_id, "first_name": "Admin"},
                    "message": {
                        "chat": {"id": chat_id, "first_name": "Admin", "type": "private"},
                    },
                    "data": "SCRIPT_ALERTA:4",
                },
            },
        )
        assert ejecucion.status_code == 200
        assert ejecucion.json()["estado"] == "SCRIPT_BARRIDO_EJECUTADO"
        assert sender.polls[-1]["poll"]["question"] == "Ingrese el NIVEL de alerta que usted visualiza:"
        assert "MUY FUERTE" in sender.polls[-1]["poll"]["options"][0]["text"]
        assert "Panico general" in sender.polls[-1]["poll"]["options"][0]["text"]
        assert "LLUVIA" not in sender.polls[-1]["poll"]["options"][0]["text"]

        nivel = client.post(
            "/api/telegram/webhook",
            json={
                "update_id": 84,
                "poll_answer": {
                    "poll_id": "poll-sismo",
                    "user": {"id": chat_id, "first_name": "Admin"},
                    "option_ids": [0],
                },
            },
        )
        assert nivel.status_code == 200
        assert nivel.json()["estado"] == "BARRIDO_NIVEL_RECIBIDO"

        ubicacion = client.post(
            "/api/telegram/webhook",
            json={
                "update_id": 85,
                "message": {
                    "from": {"id": chat_id, "first_name": "Admin"},
                    "chat": {"id": chat_id, "first_name": "Admin", "type": "private"},
                    "location": {"latitude": -0.1806532, "longitude": -78.4678382},
                },
            },
        )
        assert ubicacion.status_code == 200
        assert ubicacion.json()["estado"] == "BARRIDO_UBICACION_RECIBIDA"

        descripcion = client.post(
            "/api/telegram/webhook",
            json={
                "update_id": 86,
                "message": {
                    "from": {"id": chat_id, "first_name": "Admin"},
                    "chat": {"id": chat_id, "first_name": "Admin", "type": "private"},
                    "text": "Comunidad Centro, sismo fuerte sin danos visibles.",
                },
            },
        )
        assert descripcion.status_code == 200
        assert descripcion.json()["estado"] == "BARRIDO_DESCRIPCION_RECIBIDA"

        riesgo = client.post(
            "/api/telegram/webhook",
            json={
                "update_id": 87,
                "callback_query": {
                    "id": "callback-barrido-sismo-riesgo-no",
                    "from": {"id": chat_id, "first_name": "Admin"},
                    "message": {"chat": {"id": chat_id, "first_name": "Admin", "type": "private"}},
                    "data": "ALERTA_RIESGO:NO",
                },
            },
        )
        assert riesgo.status_code == 200
        assert riesgo.json()["estado"] == "BARRIDO_RIESGO_PERSONAS_CONFIRMADO"

        respuesta = client.post(
            "/api/telegram/webhook",
            json={
                "update_id": 88,
                "message": {
                    "from": {"id": chat_id, "first_name": "Admin"},
                    "chat": {"id": chat_id, "first_name": "Admin", "type": "private"},
                    "photo": [{"file_id": "foto-sismo", "file_unique_id": "unique-sismo"}],
                },
            },
        )
        assert respuesta.status_code == 200
        assert respuesta.json()["estado"] == "BARRIDO_REGISTRADO"

        row = session.execute(
            text(
                """
                SELECT
                    b.tipo_alerta_id,
                    ae.nombre,
                    br.descripcion,
                    br.foto_file_id,
                    br.provincia,
                    br.canton,
                    br.parroquia
                FROM telegram_barrido_respuestas br
                JOIN telegram_barridos b ON b.id = br.barrido_id
                JOIN alerta_encuesta ae ON ae.id = br.alerta_encuesta_id
                JOIN telegram_contactos c ON c.id = br.contacto_id
                WHERE c.telefono = :telefono
                """
            ),
            {"telefono": telefono},
        ).mappings().one()
        assert row["tipo_alerta_id"] == 4
        assert row["nombre"] == "MUY FUERTE"
        assert row["descripcion"] == "Comunidad Centro, sismo fuerte sin danos visibles."
        assert row["foto_file_id"] == "foto-sismo"
        assert row["provincia"] == "Guayas"
        assert row["canton"] == "Guayaquil"
        assert row["parroquia"] == "Tarqui"
    finally:
        flujos.get_settings = get_settings_original
        app.dependency_overrides.clear()
        session.close()
        transaction.rollback()
        connection.close()


def test_endpoint_reporte_alerta_devuelve_json_y_chart_url(monkeypatch) -> None:
    connection = engine.connect()
    transaction = connection.begin()
    session = Session(bind=connection, autoflush=False, expire_on_commit=False)
    telefono = f"+593013{uuid4().int % 1000000:06d}"
    chat_id = -(uuid4().int % 1000000000)

    try:
        monkeypatch.setattr(
            flujos,
            "_resolver_ubicacion_administrativa",
            lambda latitud, longitud: {"provincia": "Pichincha", "canton": "Quito", "parroquia": "Inaquito"},
        )
        _asegurar_catalogos_alertas(session)
        _asegurar_tablas_barridos(session)
        _asegurar_tabla_eventos(session)
        tipo_alerta_nombre = f"ALERTA TEST {uuid4().hex[:8]}"
        tipo_alerta_id = session.execute(
            text(
                """
                INSERT INTO tipo_alertas (descripcion, activo)
                VALUES (:descripcion, true)
                RETURNING id
                """
            ),
            {"descripcion": tipo_alerta_nombre},
        ).scalar_one()
        alerta_encuesta_id = session.execute(
            text(
                """
                INSERT INTO alerta_encuesta
                    (tipo_alerta_id, tipo_flujo_id, nombre, descripcion, color, orden, activo)
                VALUES
                    (:tipo_alerta_id, 3, 'NIVEL TEST', 'Nivel de prueba', 'verde', 1, true)
                RETURNING id
                """
            ),
            {"tipo_alerta_id": tipo_alerta_id},
        ).scalar_one()
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
        barrido_id = session.execute(
            text(
                """
                INSERT INTO telegram_barridos (tipo_alerta_id, codigo, mensaje)
                VALUES (:tipo_alerta_id, 'BARRIDO-REPORTE', 'Reporte de prueba')
                RETURNING id
                """
            ),
            {"tipo_alerta_id": tipo_alerta_id},
        ).scalar_one()
        session.execute(
            text(
                """
                INSERT INTO telegram_barrido_respuestas
                    (
                        barrido_id,
                        contacto_id,
                        alerta_encuesta_id,
                        latitud,
                        longitud,
                        provincia,
                        canton,
                        parroquia
                    )
                VALUES
                    (
                        :barrido_id,
                        :contacto_id,
                        :alerta_encuesta_id,
                        -0.1806532,
                        -78.4678382,
                        'Pichincha',
                        'Quito',
                        NULL
                    )
                """
            ),
            {"barrido_id": barrido_id, "contacto_id": contacto_id, "alerta_encuesta_id": alerta_encuesta_id},
        )
        session.execute(
            text(
                """
                INSERT INTO telegram_eventos
                    (
                        contacto_id,
                        tipo_alerta_id,
                        alerta_encuesta_id,
                        descripcion,
                        foto_file_id,
                        latitud,
                        longitud
                    )
                VALUES
                    (
                        :contacto_id,
                        :tipo_alerta_id,
                        :alerta_encuesta_id,
                        'Alerta de prueba',
                        'foto-alerta',
                        -0.1806532,
                        -78.4678382
                    )
                """
            ),
            {
                "contacto_id": contacto_id,
                "tipo_alerta_id": tipo_alerta_id,
                "alerta_encuesta_id": alerta_encuesta_id,
            },
        )

        def override_get_db() -> Generator[Session, None, None]:
            yield session

        app.dependency_overrides[get_db] = override_get_db
        client = TestClient(app)

        respuesta = client.get(f"/api/telegram/reportes/alertas/{tipo_alerta_id}")

        assert respuesta.status_code == 200
        data = respuesta.json()
        assert data["barrido_id"] is None
        assert data["tipo_alerta_id"] == tipo_alerta_id
        assert data["nombre_alerta"] == tipo_alerta_nombre
        assert data["total"] == 1
        cantidades = {item["nombre"]: item["cantidad"] for item in data["opciones"]}
        assert cantidades["NIVEL TEST"] == 1
        assert data["chart_url"].startswith("https://quickchart.io/chart?")

        reporte_barrido = client.get(f"/api/telegram/reportes/barridos/tipo-alerta/{tipo_alerta_id}")
        assert reporte_barrido.status_code == 200
        data_barrido = reporte_barrido.json()
        assert data_barrido["barrido_id"] == barrido_id
        assert data_barrido["tipo_alerta_id"] == tipo_alerta_id
        assert data_barrido["nombre_alerta"] == tipo_alerta_nombre
        assert data_barrido["total"] == 1

        lista_barridos = client.get(f"/api/telegram/barridos/tipo-alerta/{tipo_alerta_id}")
        assert lista_barridos.status_code == 200
        barrido_item = next(item for item in lista_barridos.json() if item["id"] == barrido_id)
        assert barrido_item["tipo_alerta_id"] == tipo_alerta_id
        assert barrido_item["nombre_alerta"] == tipo_alerta_nombre
        assert barrido_item["total_respuestas"] == 1

        respuesta_por_barrido = client.get(
            f"/api/telegram/reportes/tipo-alerta/{tipo_alerta_id}/barridos/{barrido_id}"
        )
        assert respuesta_por_barrido.status_code == 200
        assert respuesta_por_barrido.json()["barrido_id"] == barrido_id

        respuestas_barridos = client.get("/api/telegram/barridos/respuestas")
        assert respuestas_barridos.status_code == 200
        respuesta_item = next(item for item in respuestas_barridos.json() if item["barrido_id"] == barrido_id)
        assert respuesta_item["tipo_alerta_id"] == tipo_alerta_id
        assert respuesta_item["nombre_alerta"] == tipo_alerta_nombre
        assert respuesta_item["nivel_alerta"] == "NIVEL TEST"
        assert respuesta_item["contacto_id"] == contacto_id
        assert respuesta_item["latitud"] == -0.1806532
        assert respuesta_item["longitud"] == -78.4678382
        assert respuesta_item["provincia"] == "Pichincha"
        assert respuesta_item["canton"] == "Quito"
        assert respuesta_item["parroquia"] == "Inaquito"
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
        _asegurar_tablas_barridos(session)
        _asegurar_tabla_eventos(session)
        tipo_alerta_nombre = f"REPORTE TEST {uuid4().hex[:8]}"
        tipo_alerta_id = session.execute(
            text(
                """
                INSERT INTO tipo_alertas (descripcion, activo)
                VALUES (:descripcion, true)
                RETURNING id
                """
            ),
            {"descripcion": tipo_alerta_nombre},
        ).scalar_one()
        alerta_encuesta_id = session.execute(
            text(
                """
                INSERT INTO alerta_encuesta
                    (tipo_alerta_id, tipo_flujo_id, nombre, descripcion, color, orden, activo)
                VALUES
                    (:tipo_alerta_id, 3, 'NIVEL MENU', 'Nivel del menu', 'verde', 1, true)
                RETURNING id
                """
            ),
            {"tipo_alerta_id": tipo_alerta_id},
        ).scalar_one()
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
        barrido_id = session.execute(
            text(
                """
                INSERT INTO telegram_barridos (tipo_alerta_id, codigo, mensaje)
                VALUES (:tipo_alerta_id, 'BARRIDO-TELEGRAM', 'Reporte de prueba')
                RETURNING id
                """
            ),
            {"tipo_alerta_id": tipo_alerta_id},
        ).scalar_one()
        session.execute(
            text(
                """
                INSERT INTO telegram_barrido_respuestas
                    (barrido_id, contacto_id, alerta_encuesta_id, latitud, longitud)
                VALUES
                    (:barrido_id, :contacto_id, :alerta_encuesta_id, -0.1806532, -78.4678382)
                """
            ),
            {"barrido_id": barrido_id, "contacto_id": contacto_id, "alerta_encuesta_id": alerta_encuesta_id},
        )
        session.execute(
            text(
                """
                INSERT INTO telegram_eventos
                    (
                        contacto_id,
                        tipo_alerta_id,
                        alerta_encuesta_id,
                        descripcion,
                        foto_file_id,
                        latitud,
                        longitud
                    )
                VALUES
                    (
                        :contacto_id,
                        :tipo_alerta_id,
                        :alerta_encuesta_id,
                        'Alerta de menu',
                        'foto-alerta',
                        -0.1806532,
                        -78.4678382
                    )
                """
            ),
            {
                "contacto_id": contacto_id,
                "tipo_alerta_id": tipo_alerta_id,
                "alerta_encuesta_id": alerta_encuesta_id,
            },
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
        assert sender.messages[-1]["text"] == "Que reportes desea visualizar"
        assert sender.messages[-1]["reply_markup"]["inline_keyboard"] == [
            [{"text": "REPORTES ALERTAS", "callback_data": "MENU_REPORTE_ALERTAS"}],
            [{"text": "REPORTES BARRIDOS", "callback_data": "MENU_REPORTE_BARRIDOS"}],
        ]

        menu_alertas = client.post(
            "/api/telegram/webhook",
            json={
                "update_id": 110,
                "callback_query": {
                    "id": "callback-menu-reporte-alertas",
                    "from": {"id": chat_id, "first_name": "GAD"},
                    "message": {
                        "chat": {"id": chat_id, "first_name": "GAD", "type": "private"},
                    },
                    "data": "MENU_REPORTE_ALERTAS",
                },
            },
        )
        assert menu_alertas.status_code == 200
        assert menu_alertas.json()["estado"] == "MENU_REPORTES_ALERTAS"
        assert (
            sender.messages[-1]["text"]
            == "Seleccione el tipo de alerta del reporte de alertas que desea visualizar:"
        )
        assert any(
            row[0]["callback_data"] == f"REPORTE_ALERTAS:{tipo_alerta_id}"
            for row in sender.messages[-1]["reply_markup"]["inline_keyboard"]
        )

        respuesta_alertas = client.post(
            "/api/telegram/webhook",
            json={
                "update_id": 111,
                "callback_query": {
                    "id": "callback-reporte-alertas",
                    "from": {"id": chat_id, "first_name": "GAD"},
                    "message": {
                        "chat": {"id": chat_id, "first_name": "GAD", "type": "private"},
                    },
                    "data": f"REPORTE_ALERTAS:{tipo_alerta_id}",
                },
            },
        )

        assert respuesta_alertas.status_code == 200
        data_alertas = respuesta_alertas.json()
        assert data_alertas["estado"] == "REPORTE_ALERTA_ENVIADO"
        assert data_alertas["mensaje"] == f"Reporte generado para {tipo_alerta_nombre}."
        assert f"Reporte de alertas: {tipo_alerta_nombre}" in sender.messages[-1]["text"]
        assert "Barrido id:" not in sender.messages[-1]["text"]
        assert "- NIVEL MENU: 1" in sender.messages[-1]["text"]
        assert sender.photos[-1]["photo"].startswith("https://quickchart.io/chart?")

        menu_barridos = client.post(
            "/api/telegram/webhook",
            json={
                "update_id": 112,
                "callback_query": {
                    "id": "callback-menu-reporte-barridos",
                    "from": {"id": chat_id, "first_name": "GAD"},
                    "message": {
                        "chat": {"id": chat_id, "first_name": "GAD", "type": "private"},
                    },
                    "data": "MENU_REPORTE_BARRIDOS",
                },
            },
        )
        assert menu_barridos.status_code == 200
        assert menu_barridos.json()["estado"] == "MENU_REPORTES_BARRIDOS"
        assert (
            sender.messages[-1]["text"]
            == "Seleccione el tipo de alerta del reporte de barridos que desea visualizar:"
        )
        assert any(
            row[0]["callback_data"] == f"REPORTE_BARRIDOS:{tipo_alerta_id}"
            for row in sender.messages[-1]["reply_markup"]["inline_keyboard"]
        )

        respuesta_barridos = client.post(
            "/api/telegram/webhook",
            json={
                "update_id": 113,
                "callback_query": {
                    "id": "callback-reporte-barridos",
                    "from": {"id": chat_id, "first_name": "GAD"},
                    "message": {
                        "chat": {"id": chat_id, "first_name": "GAD", "type": "private"},
                    },
                    "data": f"REPORTE_BARRIDOS:{tipo_alerta_id}",
                },
            },
        )

        assert respuesta_barridos.status_code == 200
        data_barridos = respuesta_barridos.json()
        assert data_barridos["estado"] == "REPORTE_BARRIDO_ENVIADO"
        assert data_barridos["mensaje"] == f"Reporte de barrido generado para {tipo_alerta_nombre}."
        assert f"Reporte de alertas: {tipo_alerta_nombre}" in sender.messages[-1]["text"]
        assert f"Barrido id: {barrido_id}" in sender.messages[-1]["text"]
        assert "- NIVEL MENU: 1" in sender.messages[-1]["text"]
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


def test_webhook_guarda_reporte_evento_con_foto_descripcion_y_ubicacion(monkeypatch) -> None:
    connection = engine.connect()
    transaction = connection.begin()
    session = Session(bind=connection, autoflush=False, expire_on_commit=False)
    telefono = f"+593006{uuid4().int % 1000000:06d}"
    chat_id = -(uuid4().int % 1000000000)

    try:
        monkeypatch.setattr(
            flujos,
            "_resolver_ubicacion_administrativa",
            lambda latitud, longitud: {"provincia": "Pichincha", "canton": "Quito", "parroquia": "Inaquito"},
        )
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
                SELECT
                    e.descripcion,
                    e.foto_file_id,
                    e.foto_file_unique_id,
                    e.latitud,
                    e.longitud,
                    e.provincia,
                    e.canton,
                    e.parroquia
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
        assert row["provincia"] == "Pichincha"
        assert row["canton"] == "Quito"
        assert row["parroquia"] == "Inaquito"
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
        assert respuesta.headers["content-disposition"] == f'inline; filename="evento-{evento_id}.jpg"'
        assert respuesta.content.startswith(b"\xff\xd8")
    finally:
        app.dependency_overrides.clear()
        session.close()
        transaction.rollback()
        connection.close()


def test_endpoint_obtener_foto_evento_detecta_imagen_sin_extension() -> None:
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
                "descripcion": "Evento con foto sin extension",
                "foto_file_id": "foto-sin-extension",
                "foto_file_unique_id": "foto-sin-extension-unique",
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
        assert respuesta.headers["content-disposition"] == f'inline; filename="evento-{evento_id}.jpg"'
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


def test_endpoint_lista_eventos_con_nombre_alerta_y_campos_filtrados(monkeypatch) -> None:
    connection = engine.connect()
    transaction = connection.begin()
    session = Session(bind=connection, autoflush=False, expire_on_commit=False)
    telefono = f"+593010{uuid4().int % 1000000:06d}"
    chat_id = -(uuid4().int % 1000000000)

    try:
        monkeypatch.setattr(
            flujos,
            "_resolver_ubicacion_administrativa",
            lambda latitud, longitud: {"provincia": "Guayas", "canton": "Guayaquil", "parroquia": "Tarqui"},
        )
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
        assert item["provincia"] == "Guayas"
        assert item["canton"] == "Guayaquil"
        assert item["parroquia"] == "Tarqui"
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
