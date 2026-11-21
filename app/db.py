"""Подключение к базе: async engine и сессии для SQLAlchemy."""

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.config import settings

engine = create_async_engine(settings.database_url, echo=False)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False)


class Base(DeclarativeBase):
    """Базовый класс моделей — от него наследуются все таблицы."""


async def get_session():
    """Зависимость FastAPI: открывает сессию, отдаёт в запрос, закрывает после."""
    async with SessionLocal() as session:
        yield session


async def init_db() -> None:
    """Создаёт таблицы из моделей (development; в проде — Alembic migrate).

    Импорт моделей здесь, а не в main: metadata заполняется только при
    импорте модулей моделей — без этого create_all молча создаёт пустую БД.
    """
    import app.models  # noqa: F401

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
