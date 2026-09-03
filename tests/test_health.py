from fastapi.testclient import TestClient

from app.config import Settings
from app.main import app


def test_health() -> None:
    response = TestClient(app).get("/health")

    assert response.status_code == 200
    assert response.json() == {"estado": "OK", "base_datos": "OK"}


def test_endpoint_consultas_no_esta_publicado() -> None:
    response = TestClient(app).post(
        "/api/consultas",
        json={"contacto_id": 1, "tipo_interaccion": "RIESGO"},
    )

    assert response.status_code == 404


def test_swagger_respeta_root_path_del_proxy() -> None:
    response = TestClient(app, root_path="/riesgos-bot").get("/docs")

    assert response.status_code == 200
    assert "url: './openapi.json'" in response.text
    assert "oauth2RedirectUrl: window.location.origin + './docs/oauth2-redirect'" in response.text


def test_settings_normaliza_app_root_path() -> None:
    settings = Settings(
        database_url="postgresql+psycopg://postgres:change-me@localhost:5432/RiesgosEcuadorBot",
        app_root_path="riesgos-bot/",
    )

    assert settings.app_root_path == "/riesgos-bot"
