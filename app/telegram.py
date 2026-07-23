import json
from typing import Any, Protocol
from urllib import error, request

from fastapi import HTTPException, status

from app.config import get_settings


class TelegramSender(Protocol):
    def send_message(self, chat_id: int, text: str) -> dict[str, Any]:
        pass

    def send_poll(self, chat_id: int, question: str, options: list[str]) -> dict[str, Any]:
        pass


class TelegramDeliveryError(RuntimeError):
    pass


class TelegramBotSender:
    def __init__(self, token: str, timeout: int = 10) -> None:
        self._token = token
        self._timeout = timeout

    def _post(self, method: str, payload: dict[str, Any]) -> dict[str, Any]:
        body = json.dumps(payload).encode("utf-8")
        http_request = request.Request(
            f"https://api.telegram.org/bot{self._token}/{method}",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        try:
            with request.urlopen(http_request, timeout=self._timeout) as response:
                response_body = response.read().decode("utf-8")
        except error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise TelegramDeliveryError(f"Telegram rechazo el mensaje: {detail}") from exc
        except OSError as exc:
            raise TelegramDeliveryError(f"No se pudo conectar con Telegram: {exc}") from exc

        data = json.loads(response_body)
        if not data.get("ok"):
            raise TelegramDeliveryError(f"Telegram respondio con error: {data}")
        return data

    def send_message(self, chat_id: int, text: str) -> dict[str, Any]:
        return self._post("sendMessage", {"chat_id": chat_id, "text": text})

    def send_poll(self, chat_id: int, question: str, options: list[str]) -> dict[str, Any]:
        return self._post(
            "sendPoll",
            {
                "chat_id": chat_id,
                "question": question,
                "options": [{"text": option} for option in options],
                "is_anonymous": False,
                "type": "regular",
                "allows_multiple_answers": False,
                "allows_revoting": False,
            },
        )


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
