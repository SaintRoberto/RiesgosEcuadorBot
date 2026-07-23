# Riesgos Ecuador Bot API

Base de la API HTTP para los flujos del bot de Telegram.

## Requisitos

- Python 3.13
- PostgreSQL
- La tabla `telegram_contactos` debe existir antes de aplicar la migración.

## Instalación

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
Copy-Item .env.example .env
```

Configura `DATABASE_URL` dentro de `.env`. El formato esperado es:

```text
postgresql+psycopg://usuario:contraseña@servidor:5432/base
```

Para enviar mensajes reales por Telegram, configura tambien:

```text
TELEGRAM_BOT_TOKEN=token-del-bot
```

Para una base nueva, crea la tabla con:

```powershell
alembic upgrade head
```

Si `telegram_consultas` ya fue creada manualmente con el DDL del proyecto, registra la migración sin intentar recrearla:

```powershell
alembic stamp head
```

## Ejecución

```powershell
py app.py
```

El archivo detecta automáticamente `.venv`, por lo que no es necesario activar el entorno virtual. También puedes ejecutarlo explícitamente con `\.venv\Scripts\python.exe app.py`.

- Documentación Swagger: `http://127.0.0.1:8000/docs`
- Estado del servicio: `GET http://127.0.0.1:8000/health`

## Endpoints de flujos Telegram

- `POST /api/telegram/webhook`: recibe mensajes entrantes de Telegram y registra contactos.
- `POST /api/telegram/boletines`: envia el boletin diario por Telegram y registra el resultado.
- `POST /api/telegram/barridos/solicitudes`: envia la solicitud diaria de barrido a contactos GAD y registra el resultado.
- `POST /api/telegram/barridos/respuestas`: registra la respuesta del GAD con nivel de lluvia.
- `POST /api/telegram/eventos/seguimientos`: envia el seguimiento de eventos adversos por Telegram, con marca opcional de respaldo por correo.

Los endpoints reciben telefonos, no IDs internos. Ejemplo:

```json
{
  "telefonos": ["+593987223658"]
}
```

Registro manual de respuesta de barrido:

```json
{
  "telefono": "+593987223658",
  "nivel_lluvia": "4",
  "latitud": -0.1806532,
  "longitud": -78.4678382,
  "codigo": "BARRIDO-2026-07-17",
  "observacion": "Lluvia sostenida",
  "usuario_id": 1
}
```

Registro automatico por webhook:

1. El GAD comparte ubicacion en Telegram. El webhook guarda `message.location` como ubicacion pendiente.
2. El bot responde pidiendo el nivel.
3. El GAD responde `1`, `2`, `3` o `4`.
4. El webhook crea el registro en `telegram_barridos`.

Registro de contactos:

1. El usuario escribe `/start` al bot.
2. Telegram envia el update a `POST /api/telegram/webhook`.
3. El bot responde pidiendo el telefono institucional.
4. El usuario envia el telefono, por ejemplo `+593987223658`.
5. El webhook guarda `telefono -> chat_id` en `telegram_contactos`.

En local, Telegram no puede llamar a `127.0.0.1`. Para probar desde Swagger, copia un objeto de `result[]` de `getUpdates` y pegalo como body en `/api/telegram/webhook`.

## Pruebas

```powershell
python -m pip install -r requirements-dev.txt
pytest -q
```
