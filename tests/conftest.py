"""Общие fixtures: изолированная sqlite на тест + утилиты для мок-клиента."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable, Iterator
from typing import Any

import httpx
import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.db as db_module
from app.db import init_db
from app.services.keitaro_client import KeitaroClient

BASE = "https://tracker.test"


def _swap_db(tmp_path: Any, *, create_tables: bool) -> Any:
    """Подменяет engine/SessionLocal; create_tables=False — пустая схема (для lifespan)."""
    url = f"sqlite+aiosqlite:///{tmp_path}/test.db"
    engine = create_async_engine(url)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    old_engine, old_factory = db_module.engine, db_module.SessionLocal
    db_module.engine = engine
    db_module.SessionLocal = session_factory

    if create_tables:
        asyncio.run(init_db())

    def _restore() -> None:
        asyncio.run(engine.dispose())
        db_module.engine = old_engine
        db_module.SessionLocal = old_factory

    return engine, session_factory, _restore


@pytest.fixture
def isolated_db(tmp_path) -> Iterator[tuple[Any, Any]]:
    """Свежая sqlite + create_all (таблицы сразу есть)."""
    engine, session_factory, restore = _swap_db(tmp_path, create_tables=True)
    try:
        yield engine, session_factory
    finally:
        restore()


@pytest.fixture
def isolated_db_no_tables(tmp_path) -> Iterator[tuple[Any, Any]]:
    """Свежая sqlite БЕЗ create_all — для тестов lifespan (dev/production)."""
    engine, session_factory, restore = _swap_db(tmp_path, create_tables=False)
    try:
        yield engine, session_factory
    finally:
        restore()


def make_client(
    handler: Callable[[httpx.Request], httpx.Response],
    *,
    api_key: str = "test-key",
) -> KeitaroClient:
    """Клиент с MockTransport — без сети."""
    transport = httpx.MockTransport(handler)
    return KeitaroClient(base_url=BASE, api_key=api_key, transport=transport)


def request_json(request: httpx.Request) -> Any:
    """Тело запроса как dict/list (пустое → None)."""
    if not request.content:
        return None
    return json.loads(request.content.decode())


class RecordingHandler:
    """Маршруты → ответы; пишет порядок и тела запросов.

    route(method, suffix, response):
      - dict/list → JSON с status;
      - httpx.Response → как есть;
      - Exception → raise в handler (для таймаутов).
    """

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self._routes: dict[tuple[str, str], tuple[Any, int]] = {}

    def route(
        self,
        method: str,
        path_suffix: str,
        response: Any,
        *,
        status: int = 200,
    ) -> None:
        self._routes[(method.upper(), path_suffix)] = (response, status)

    def __call__(self, request: httpx.Request) -> httpx.Response:
        method = request.method.upper()
        path = request.url.path
        body = request_json(request)
        self.calls.append({"method": method, "path": path, "body": body})

        for (_m, suffix), (response, status) in self._routes.items():
            if method == _m and path.endswith(suffix):
                if isinstance(response, Exception):
                    raise response
                if isinstance(response, httpx.Response):
                    return response
                return httpx.Response(status, json=response)

        return httpx.Response(
            404, json={"error": f"no mock for {method} {path}"}
        )

    @property
    def paths(self) -> list[str]:
        return [c["path"] for c in self.calls]


def json_routes_ok() -> RecordingHandler:
    """Стандартный успех: справочники, оффер, кампания, потоки."""
    h = RecordingHandler()
    h.route("GET", "/offers", [{"id": 42, "name": "Offer A"}])
    h.route("GET", "/domains", [{"id": 7, "name": "x.example", "state": "active"}])
    h.route("GET", "/groups", [{"id": 3, "name": "g", "type": "campaigns"}])
    h.route("GET", "/traffic_sources", [{"id": 5, "name": "ts", "state": "active"}])
    h.route("POST", "/campaigns", {"id": 100, "name": "test_palmin_x", "alias": "x"})
    h.route("POST", "/streams", {"id": 200, "name": "s"})
    return h


def run(coro: Any) -> Any:
    return asyncio.run(coro)
