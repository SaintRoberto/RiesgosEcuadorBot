from fastapi.testclient import TestClient

from app.main import app


def test_health() -> None:
    response = TestClient(app).get("/health")

    assert response.status_code == 200
    assert response.json() == {"estado": "OK", "base_datos": "OK"}


def test_endpoint_consultas_no_esta_publicado() -> None:
    response = TestClient(app).post(
        "/api/consultas",
        json={"contacto_id": 1, "tipo_consulta": "RIESGO"},
    )

    assert response.status_code == 404

