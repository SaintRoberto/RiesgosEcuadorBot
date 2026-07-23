from fastapi import FastAPI, HTTPException, status
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
)
app.include_router(flujos_router)


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
