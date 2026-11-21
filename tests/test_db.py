"""Тесты БД: lifespan создаёт таблицу, в неё пишется CampaignRecord.

Каждый тест работает в отдельной sqlite-базе (tmp_path), а не в dev-файле.
"""

from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import inspect, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.db as db_module
from app.db import init_db
from app.main import app
from app.models import CampaignRecord


@pytest.fixture
def isolated_db(tmp_path):
    """Подменяет engine/SessionLocal на свежую sqlite в tmp_path."""
    url = f"sqlite+aiosqlite:///{tmp_path}/test.db"
    engine = create_async_engine(url)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    old_engine, old_factory = db_module.engine, db_module.SessionLocal
    db_module.engine = engine
    db_module.SessionLocal = session_factory
    try:
        yield engine, session_factory
    finally:
        # dispose синхронно через asyncio.run — тестовые движки, event loop один
        asyncio.run(engine.dispose())
        db_module.engine = old_engine
        db_module.SessionLocal = old_factory


def test_init_db_creates_table_and_inserts(isolated_db) -> None:
    engine, session_factory = isolated_db

    async def scenario() -> None:
        await init_db()
        async with session_factory() as session:
            session.add(
                CampaignRecord(
                    name="test_palmin_demo",
                    alias="test_palmin_demo",
                    geo_code="MX",
                    offer_id=42,
                    status="created",
                )
            )
            await session.commit()

            result = await session.execute(select(CampaignRecord))
            rows = result.scalars().all()

        assert len(rows) == 1
        row = rows[0]
        assert row.name == "test_palmin_demo"
        assert row.geo_code == "MX"
        assert row.offer_id == 42
        assert row.status == "created"
        assert row.id is not None

    asyncio.run(scenario())


def test_lifespan_creates_campaign_table(isolated_db) -> None:
    """Таблица появляется именно из lifespan (без предварительного import app.models)."""
    engine, _ = isolated_db

    with TestClient(app) as client:
        response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}

    async def has_table() -> bool:
        async with engine.connect() as conn:
            return await conn.run_sync(
                lambda connection: inspect(connection).has_table("campaign_records")
            )

    assert asyncio.run(has_table()) is True


def test_production_lifespan_skips_create_all(isolated_db, monkeypatch) -> None:
    """APP_ENV=production: create_all не вызывается — только alembic."""
    from app.config import settings

    engine, _ = isolated_db
    monkeypatch.setattr(settings, "app_env", "production")

    with TestClient(app) as client:
        assert client.get("/health").status_code == 200

    async def has_table() -> bool:
        async with engine.connect() as conn:
            return await conn.run_sync(
                lambda connection: inspect(connection).has_table("campaign_records")
            )

    assert asyncio.run(has_table()) is False
