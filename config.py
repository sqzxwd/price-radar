from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Конфигурация приложения через переменные окружения и .env файл."""

    min_profit_percent: float = 15.0
    market_fee_percent: float = 15.0
    telegram_bot_token: str | None = None
    telegram_chat_id: str | None = None
    check_interval_seconds: float = 2.0

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    @field_validator("telegram_bot_token", "telegram_chat_id", mode="before")
    @classmethod
    def empty_str_to_none(cls, v: str | None) -> str | None:
        """Преобразует пустые строки переменных окружения в None."""
        if isinstance(v, str) and not v.strip():
            return None
        return v


def get_settings() -> Settings:
    """Возвращает экземпляр настроек."""
    return Settings()
