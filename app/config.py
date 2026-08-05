from functools import lru_cache
from typing import Any

from pydantic import SecretStr
from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "Riesgos Ecuador Bot API"
    app_debug: bool = False
    database_url: str
    telegram_bot_token: SecretStr | None = None
    telegram_admin_user_ids: set[int] = set()

    @field_validator("telegram_admin_user_ids", mode="before")
    @classmethod
    def normalizar_telegram_admin_user_ids(cls, value: Any) -> Any:
        if value is None or value == "":
            return set()
        if isinstance(value, str):
            return {int(item.strip()) for item in value.split(",") if item.strip()}
        return value

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
