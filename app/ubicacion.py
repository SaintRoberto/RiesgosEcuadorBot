import json
import re
import unicodedata
from typing import Any
from urllib import error as urlerror
from urllib import request as urlrequest
from urllib.parse import urlencode

NOMINATIM_REVERSE_URL = "https://nominatim.openstreetmap.org/reverse"
NOMINATIM_USER_AGENT = "RiesgosEcuadorBot/1.0"
NOMINATIM_TIMEOUT_SECONDS = 5
PAIS_ECUADOR_CODIGO = "ec"


def ubicacion_admin_vacia() -> dict[str, str | None]:
    return {"provincia": None, "canton": None, "parroquia": None}


def _texto_address(address: dict[str, Any], campos: list[str]) -> str | None:
    for campo in campos:
        valor = address.get(campo)
        if isinstance(valor, str) and valor.strip():
            return valor.strip()
    return None


def _normalizar_texto_ubicacion(valor: str | None) -> str:
    if not valor:
        return ""
    texto = unicodedata.normalize("NFKD", valor)
    texto = "".join(caracter for caracter in texto if not unicodedata.combining(caracter))
    return re.sub(r"\s+", " ", texto).strip().casefold()


def _texto_address_distinto_de(
    address: dict[str, Any],
    campos: list[str],
    valores_excluidos: list[str | None],
) -> str | None:
    excluidos = {_normalizar_texto_ubicacion(valor) for valor in valores_excluidos if valor}
    for campo in campos:
        valor = address.get(campo)
        if not isinstance(valor, str) or not valor.strip():
            continue
        valor_limpio = valor.strip()
        if _normalizar_texto_ubicacion(valor_limpio) not in excluidos:
            return valor_limpio
    return None


def extraer_ubicacion_administrativa_desde_address(address: dict[str, Any]) -> dict[str, str | None]:
    if str(address.get("country_code") or "").lower() not in {"", PAIS_ECUADOR_CODIGO}:
        return ubicacion_admin_vacia()

    provincia = _texto_address(address, ["state", "region"])
    canton = _texto_address(address, ["county", "city", "municipality", "town"])
    parroquia = _texto_address_distinto_de(
        address,
        [
            "city_district",
            "village",
            "suburb",
            "residential",
            "neighbourhood",
            "quarter",
            "borough",
            "hamlet",
            "town",
            "municipality",
        ],
        [canton],
    )
    if parroquia is None:
        parroquia = _texto_address(address, ["city_district", "village", "suburb", "town", "municipality"])

    return {
        "provincia": provincia,
        "canton": canton,
        "parroquia": parroquia,
    }


def resolver_ubicacion_administrativa(latitud: float, longitud: float) -> dict[str, str | None]:
    if not (-90 <= latitud <= 90 and -180 <= longitud <= 180):
        return ubicacion_admin_vacia()

    query = urlencode(
        {
            "format": "jsonv2",
            "lat": latitud,
            "lon": longitud,
            "zoom": 18,
            "addressdetails": 1,
        }
    )
    http_request = urlrequest.Request(
        f"{NOMINATIM_REVERSE_URL}?{query}",
        headers={
            "Accept": "application/json",
            "User-Agent": NOMINATIM_USER_AGENT,
        },
        method="GET",
    )

    try:
        with urlrequest.urlopen(http_request, timeout=NOMINATIM_TIMEOUT_SECONDS) as response:
            body = response.read().decode("utf-8")
    except (OSError, urlerror.HTTPError, TimeoutError, ValueError):
        return ubicacion_admin_vacia()

    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        return ubicacion_admin_vacia()

    address = data.get("address") if isinstance(data, dict) else None
    if not isinstance(address, dict):
        return ubicacion_admin_vacia()
    return extraer_ubicacion_administrativa_desde_address(address)
