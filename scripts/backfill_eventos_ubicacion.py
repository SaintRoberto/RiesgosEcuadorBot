from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from sqlalchemy import select

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from app.database import SessionLocal
from app.models import TelegramEvento
from app.ubicacion import resolver_ubicacion_administrativa


def backfill_eventos_ubicacion(delay_seconds: float) -> tuple[int, int]:
    cache: dict[tuple[float, float], dict[str, str | None]] = {}
    actualizados = 0

    with SessionLocal() as db:
        eventos = list(db.scalars(select(TelegramEvento).order_by(TelegramEvento.id)))
        for evento in eventos:
            latitud = float(evento.latitud)
            longitud = float(evento.longitud)
            cache_key = (latitud, longitud)
            ubicacion = cache.get(cache_key)
            if ubicacion is None:
                ubicacion = resolver_ubicacion_administrativa(latitud, longitud)
                cache[cache_key] = ubicacion
                if delay_seconds > 0:
                    time.sleep(delay_seconds)

            if not (ubicacion["provincia"] or ubicacion["canton"] or ubicacion["parroquia"]):
                continue

            evento.provincia = ubicacion["provincia"]
            evento.canton = ubicacion["canton"]
            evento.parroquia = ubicacion["parroquia"]
            actualizados += 1

        db.commit()
        return len(eventos), actualizados


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Actualiza provincia, canton y parroquia de telegram_eventos desde latitud/longitud."
    )
    parser.add_argument(
        "--delay-seconds",
        type=float,
        default=1.0,
        help="Pausa entre consultas unicas a Nominatim.",
    )
    args = parser.parse_args()

    total, actualizados = backfill_eventos_ubicacion(args.delay_seconds)
    print(f"Eventos revisados: {total}")
    print(f"Eventos actualizados: {actualizados}")


if __name__ == "__main__":
    main()
