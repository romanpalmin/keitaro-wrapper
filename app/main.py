"""Точка входа FastAPI: приложение и корневой health-эндпоинт."""

from fastapi import FastAPI

app = FastAPI(
    title="Keitaro Wrapper",
    description="Тестовое задание: веб-обёртка над API трекера",
    version="0.1.0",
)


@app.get("/health")
def health() -> dict[str, str]:
    """Простая проверка, что сервис жив и отвечает."""
    return {"status": "ok"}
