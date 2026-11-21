"""Настройки приложения из окружения (.env)."""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Читает переменные из .env / окружения; значения по умолчанию — для локальной разработки."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Плейсхолдер: реальный хост трекера — только в локальном .env, не в git
    keitaro_base_url: str = "https://tracker.example.com"
    keitaro_api_key: str = ""
    database_url: str = "sqlite+aiosqlite:///./keitaro_wrapper.db"
    app_host: str = "127.0.0.1"
    app_port: int = 8000
    # dev → lifespan может create_all; production → только alembic upgrade head
    app_env: str = "dev"


settings = Settings()
