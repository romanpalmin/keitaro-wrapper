"""Тесты HTTP API создаватора: валидация, успех, коды ошибок трекера."""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.db as db_module
from app.api.routes import get_keitaro_client
from app.db import get_session
from app.main import app
from app.models import CampaignRecord
from tests.conftest import json_routes_ok, make_client


@pytest.fixture
def api_client(tmp_path) -> Iterator[tuple[Any, Any, Any]]:
    """TestClient + изолированная БД + клиент трекера на общем handler."""
    url = f"sqlite+aiosqlite:///{tmp_path}/api.db"
    engine = create_async_engine(url)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    old_engine, old_factory = db_module.engine, db_module.SessionLocal
    db_module.engine = engine
    db_module.SessionLocal = session_factory

    handler = json_routes_ok()

    async def _bootstrap() -> None:
        from app.db import init_db

        await init_db()

    asyncio.run(_bootstrap())

    async def override_session():
        async with session_factory() as session:
            yield session

    # каждый запрос — свежий клиент, один handler (лог один на тест)
    app.dependency_overrides[get_session] = override_session
    app.dependency_overrides[get_keitaro_client] = lambda: make_client(handler)

    with TestClient(app) as client:
        yield client, handler, session_factory

    app.dependency_overrides.clear()
    db_module.engine = old_engine
    db_module.SessionLocal = old_factory
    asyncio.run(engine.dispose())


def test_index_serves_html_form(api_client) -> None:
    client, _, _ = api_client
    resp = client.get("/")
    assert resp.status_code == 200
    assert "Создаватор кампаний" in resp.text
    assert 'name="geo_code"' in resp.text


def test_post_campaign_success(api_client) -> None:
    client, handler, session_factory = api_client
    resp = client.post(
        "/api/campaigns",
        json={"name": "demo", "geo_code": "MX", "offer_id": 42},
    )
    assert resp.status_code == 201, resp.text
    data = resp.json()
    assert data["status"] == "created"
    assert data["keitaro_campaign_id"] == 100
    assert data["name"] == "test_palmin_demo"
    assert data["stream_ids"] == [200, 200]

    posts = [
        c
        for c in handler.calls
        if c["method"] == "POST" and c["path"].endswith("/campaigns")
    ]
    assert posts and posts[0]["body"]["name"] == "test_palmin_demo"

    async def count() -> int:
        async with session_factory() as session:
            rows = (await session.execute(select(CampaignRecord))).scalars().all()
            return len(rows)

    assert asyncio.run(count()) == 1


def test_validation_rejects_bad_geo(api_client) -> None:
    client, _, _ = api_client
    resp = client.post(
        "/api/campaigns",
        json={"name": "x", "geo_code": "1", "offer_id": 42},
    )
    assert resp.status_code == 422


def test_offer_not_found_404(api_client) -> None:
    client, handler, _ = api_client
    handler.route("GET", "/offers", [{"id": 1, "name": "other"}])
    resp = client.post(
        "/api/campaigns",
        json={"name": "x", "geo_code": "RU", "offer_id": 42},
    )
    assert resp.status_code == 404
    assert "42" in resp.json()["detail"]


def test_tracker_http_error_502(api_client) -> None:
    client, handler, _ = api_client
    handler.route(
        "POST",
        "/campaigns",
        httpx.Response(500, json={"error": "tracker down"}),
    )
    resp = client.post(
        "/api/campaigns",
        json={"name": "x", "geo_code": "RU", "offer_id": 42},
    )
    assert resp.status_code == 502


def test_timeout_maps_504(api_client) -> None:
    client, handler, _ = api_client
    # Exception в route → raise из MockTransport
    handler.route(
        "GET",
        "/offers",
        httpx.ReadTimeout("slow"),
    )
    resp = client.post(
        "/api/campaigns",
        json={"name": "x", "geo_code": "RU", "offer_id": 42},
    )
    assert resp.status_code == 504
