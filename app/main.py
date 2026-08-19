from fastapi import FastAPI, HTTPException, status
from fastapi.openapi.docs import get_swagger_ui_html, get_swagger_ui_oauth2_redirect_html
from starlette.responses import HTMLResponse
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.config import get_settings
from app.database import SessionLocal
from app.routes.flujos import router as flujos_router
from app.schemas import HealthRespuesta

settings = get_settings()
app = FastAPI(
    title=settings.app_name,
    debug=settings.app_debug,
    version="1.0.0",
    root_path=settings.app_root_path,
    docs_url=None,
)
app.include_router(flujos_router)


@app.get("/docs", include_in_schema=False)
def swagger_ui_html() -> HTMLResponse:
    return get_swagger_ui_html(
        openapi_url="./openapi.json",
        title=f"{settings.app_name} - Swagger UI",
        oauth2_redirect_url="./docs/oauth2-redirect",
    )


@app.get("/docs/oauth2-redirect", include_in_schema=False)
def swagger_ui_redirect() -> HTMLResponse:
    return get_swagger_ui_oauth2_redirect_html()


@app.get("/health", response_model=HealthRespuesta, tags=["sistema"])
def health() -> HealthRespuesta:
    try:
        with SessionLocal() as db:
            db.execute(text("SELECT 1"))
    except SQLAlchemyError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="La base de datos no está disponible.",
        ) from exc

    return HealthRespuesta(estado="OK", base_datos="OK")
