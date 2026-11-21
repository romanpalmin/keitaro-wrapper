"""Точка входа FastAPI: приложение, lifespan и health-эндпоинт."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.config import settings
from app.db import init_db


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """При старте: в dev — create_all; в production — только alembic (см. APP_ENV)."""
    if settings.app_env != "production":
        await init_db()
    yield


app = FastAPI(
    title="Keitaro Wrapper",
    description="Тестовое задание: веб-обёртка над API трекера",
    version="0.1.0",
    lifespan=lifespan,
)


@app.get("/health")
def health() -> dict[str, str]:
    """Простая проверка, что сервис жив и отвечает."""
    return {"status": "ok"}
