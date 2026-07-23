import json
from typing import Any, Protocol
from urllib import error, request

from fastapi import HTTPException, status

from app.config import get_settings


class TelegramSender(Protocol):
    def send_message(self, chat_id: int, text: str) -> dict[str, Any]:
        pass


class TelegramDeliveryError(RuntimeError):
    pass


class TelegramBotSender:
    def __init__(self, token: str, timeout: int = 10) -> None:
        self._token = token
        self._timeout = timeout

    def send_message(self, chat_id: int, text: str) -> dict[str, Any]:
        payload = json.dumps({"chat_id": chat_id, "text": text}).encode("utf-8")
        http_request = request.Request(
            f"https://api.telegram.org/bot{self._token}/sendMessage",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        try:
            with request.urlopen(http_request, timeout=self._timeout) as response:
                body = response.read().decode("utf-8")
        except error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise TelegramDeliveryError(f"Telegram rechazo el mensaje: {detail}") from exc
        except OSError as exc:
            raise TelegramDeliveryError(f"No se pudo conectar con Telegram: {exc}") from exc

        data = json.loads(body)
        if not data.get("ok"):
            raise TelegramDeliveryError(f"Telegram respondio con error: {data}")
        return data


def get_telegram_sender() -> TelegramSender:
    token = get_settings().telegram_bot_token
    if token is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="TELEGRAM_BOT_TOKEN no esta configurado en .env.",
        )
    return TelegramBotSender(token.get_secret_value())


def get_optional_telegram_sender() -> TelegramSender | None:
    token = get_settings().telegram_bot_token
    if token is None:
        return None
    return TelegramBotSender(token.get_secret_value())
