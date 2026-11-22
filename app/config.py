"""Настройки приложения из окружения (.env)."""

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# Корень проекта = родитель app/ — .env и SQLite-файл ищем отсюда, а не от cwd.
_ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    """Читает переменные из .env / окружения; значения по умолчанию — для локальной разработки."""

    # env_file с путём от __file__: работает при запуске uvicorn/pytest из любой директории
    model_config = SettingsConfigDict(env_file=_ROOT / ".env", extra="ignore")

    # Плейсхолдер: реальный хост трекера — только в локальном .env, не в git
    keitaro_base_url: str = "https://tracker.example.com"
    keitaro_api_key: str = ""
    # Относительный путь в .env всё равно cwd-зависим — default берём абсолютным от корня
    database_url: str = f"sqlite+aiosqlite:///{_ROOT / 'keitaro_wrapper.db'}"
    app_host: str = "127.0.0.1"
    app_port: int = 8000
    # dev → lifespan может create_all; production → только alembic upgrade head
    app_env: str = "dev"


settings = Settings()
