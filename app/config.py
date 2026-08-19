from functools import lru_cache

from pydantic import SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "Riesgos Ecuador Bot API"
    app_debug: bool = False
    app_root_path: str = ""
    database_url: str
    telegram_bot_token: SecretStr | None = None
    telegram_admin_user_ids: str = ""

    @field_validator("app_root_path")
    @classmethod
    def normalizar_app_root_path(cls, valor: str) -> str:
        valor = valor.strip()
        if not valor or valor == "/":
            return ""
        return f"/{valor.strip('/')}"

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
