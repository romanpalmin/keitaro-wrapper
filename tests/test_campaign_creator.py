"""Тесты создаватора: порядок запросов к Keitaro, тела потоков, откат."""

from __future__ import annotations

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.models import CampaignRecord
from app.services.campaign_creator import (
    CampaignCreatorError,
    build_alias,
    create_campaign_with_streams,
    with_prefix,
)
from app.services.keitaro_client import KeitaroApiError
from tests.conftest import json_routes_ok, make_client, run


def test_build_alias_and_prefix() -> None:
    assert with_prefix("demo") == "test_palmin_demo"
    assert with_prefix("test_palmin_demo") == "test_palmin_demo"
    assert build_alias("test_palmin_My Offer!") == "test_palmin_my_offer"
    assert build_alias("!!!") == "campaign"


def test_full_success_sends_expected_bodies(isolated_db) -> None:
    _, session_factory = isolated_db
    handler = json_routes_ok()
    client = make_client(handler)

    async def scenario():
        async with session_factory() as session:
            result = await create_campaign_with_streams(
                name="demo",
                geo_code="mx",
                offer_id=42,
                session=session,
                client=client,
            )
            rows = (await session.execute(select(CampaignRecord))).scalars().all()
        return result, rows

    result, rows = run(scenario())

    assert result.status == "created"
    assert result.keitaro_campaign_id == 100
    assert len(rows) == 1
    assert rows[0].status == "created"
    assert rows[0].keitaro_campaign_id == 100
    assert rows[0].name == "test_palmin_demo"
    assert rows[0].geo_code == "MX"

    posts = [c for c in handler.calls if c["method"] == "POST"]
    assert posts[0]["path"].endswith("/campaigns")
    campaign_body = posts[0]["body"]
    assert campaign_body["name"] == "test_palmin_demo"
    assert campaign_body["alias"] == "test_palmin_demo"
    assert campaign_body["domain_id"] == 7
    assert campaign_body["group_id"] == "3"
    assert campaign_body["traffic_source_id"] == 5

    assert posts[1]["path"].endswith("/streams")
    geo_stream = posts[1]["body"]
    assert geo_stream["campaign_id"] == 100
    assert geo_stream["type"] == "forced"
    assert geo_stream["schema"] == "redirect"
    assert geo_stream["action_type"] == "http"
    assert geo_stream["action_options"] == {"url": "https://www.google.com"}
    assert geo_stream["filters"] == [
        {"name": "country", "mode": "accept", "payload": ["MX"]}
    ]

    assert posts[2]["path"].endswith("/streams")
    offer_stream = posts[2]["body"]
    assert offer_stream["type"] == "default"
    assert offer_stream["offers"] == [
        {"offer_id": 42, "share": 100, "state": "active"}
    ]


def test_offer_not_found_is_404_without_campaign_create(isolated_db) -> None:
    _, session_factory = isolated_db
    handler = json_routes_ok()
    handler.route("GET", "/offers", [{"id": 42, "name": "A"}])
    client = make_client(handler)

    async def scenario():
        async with session_factory() as session:
            with pytest.raises(CampaignCreatorError) as exc:
                await create_campaign_with_streams(
                    name="x",
                    geo_code="RU",
                    offer_id=999,
                    session=session,
                    client=client,
                )
            rows = (await session.execute(select(CampaignRecord))).scalars().all()
        return exc.value, rows

    err, rows = run(scenario())
    assert err.status_code == 404
    assert rows == []
    assert not any(c["method"] == "POST" for c in handler.calls)


def test_stream_failure_rolls_back_campaign(isolated_db) -> None:
    _, session_factory = isolated_db
    handler = json_routes_ok()
    handler.route(
        "POST",
        "/streams",
        httpx.Response(500, json={"error": "boom"}),
    )
    handler.route("DELETE", "/campaigns/100", {"ok": True})
    client = make_client(handler)

    async def scenario():
        async with session_factory() as session:
            with pytest.raises(CampaignCreatorError) as exc:
                await create_campaign_with_streams(
                    name="fail",
                    geo_code="RU",
                    offer_id=42,
                    session=session,
                    client=client,
                )
            rows = (await session.execute(select(CampaignRecord))).scalars().all()
        return exc.value, rows

    err, rows = run(scenario())
    assert err.status_code == 502
    assert rows[0].status == "failed"
    assert rows[0].keitaro_campaign_id == 100
    deletes = [c for c in handler.calls if c["method"] == "DELETE"]
    assert len(deletes) == 1
    assert deletes[0]["path"].endswith("/campaigns/100")


def test_stream_failure_without_rollback_is_partial(isolated_db) -> None:
    _, session_factory = isolated_db
    handler = json_routes_ok()
    handler.route(
        "POST",
        "/streams",
        httpx.Response(500, json={"error": "boom"}),
    )
    handler.route(
        "DELETE",
        "/campaigns/100",
        httpx.Response(500, json={"error": "nope"}),
    )
    client = make_client(handler)

    async def scenario():
        async with session_factory() as session:
            with pytest.raises(CampaignCreatorError) as exc:
                await create_campaign_with_streams(
                    name="fail2",
                    geo_code="RU",
                    offer_id=42,
                    session=session,
                    client=client,
                )
            rows = (await session.execute(select(CampaignRecord))).scalars().all()
        return exc.value, rows

    err, rows = run(scenario())
    assert err.status_code == 502
    assert rows[0].status == "partial"
    assert rows[0].keitaro_campaign_id == 100


def test_timeout_kind_on_list_offers(isolated_db) -> None:
    _, session_factory = isolated_db
    handler = json_routes_ok()
    handler.route("GET", "/offers", httpx.ReadTimeout("t"))
    client = make_client(handler)

    async def scenario():
        async with session_factory() as session:
            with pytest.raises(KeitaroApiError) as exc:
                await create_campaign_with_streams(
                    name="t",
                    geo_code="RU",
                    offer_id=42,
                    session=session,
                    client=client,
                )
        return exc.value

    err = run(scenario())
    assert err.kind == "timeout"


def test_stream_response_without_id_rolls_back(isolated_db) -> None:
    """Ответ потока без id → contract → откат, а не stream_ids=[-1]."""
    _, session_factory = isolated_db
    handler = json_routes_ok()
    handler.route("POST", "/streams", {"name": "no-id"})
    handler.route("DELETE", "/campaigns/100", {"ok": True})
    client = make_client(handler)

    async def scenario():
        async with session_factory() as session:
            with pytest.raises(CampaignCreatorError) as exc:
                await create_campaign_with_streams(
                    name="noid",
                    geo_code="RU",
                    offer_id=42,
                    session=session,
                    client=client,
                )
            rows = (await session.execute(select(CampaignRecord))).scalars().all()
        return exc.value, rows

    err, rows = run(scenario())
    assert err.status_code == 502
    assert rows[0].status == "failed"
    assert any(c["method"] == "DELETE" for c in handler.calls)


def test_campaign_create_without_id_finds_by_alias_and_deletes(isolated_db) -> None:
    """POST /campaigns без id → ищем по alias, удаляем, без записи в БД."""
    _, session_factory = isolated_db
    handler = json_routes_ok()
    handler.route("POST", "/campaigns", {"name": "x"})  # без id
    handler.route(
        "GET",
        "/campaigns",
        [{"id": 55, "alias": "test_palmin_orphan", "name": "test_palmin_orphan"}],
    )
    handler.route("DELETE", "/campaigns/55", {"ok": True})
    client = make_client(handler)

    async def scenario():
        async with session_factory() as session:
            with pytest.raises(CampaignCreatorError) as exc:
                await create_campaign_with_streams(
                    name="orphan",
                    geo_code="RU",
                    offer_id=42,
                    session=session,
                    client=client,
                )
            rows = (await session.execute(select(CampaignRecord))).scalars().all()
        return exc.value, rows

    err, rows = run(scenario())
    assert "удалена" in str(err)
    assert "DELETE не удался" not in str(err)
    assert rows == []
    deletes = [c for c in handler.calls if c["method"] == "DELETE"]
    assert deletes and deletes[0]["path"].endswith("/campaigns/55")


def test_campaign_no_id_delete_fails_message_is_honest(isolated_db) -> None:
    """DELETE после find-by-alias упал → не врём «удалена»."""
    _, session_factory = isolated_db
    handler = json_routes_ok()
    handler.route("POST", "/campaigns", {"name": "x"})
    handler.route(
        "GET",
        "/campaigns",
        [{"id": 55, "alias": "test_palmin_stuck", "name": "test_palmin_stuck"}],
    )
    handler.route(
        "DELETE",
        "/campaigns/55",
        httpx.Response(500, json={"error": "nope"}),
    )
    client = make_client(handler)

    async def scenario():
        async with session_factory() as session:
            with pytest.raises(CampaignCreatorError) as exc:
                await create_campaign_with_streams(
                    name="stuck",
                    geo_code="RU",
                    offer_id=42,
                    session=session,
                    client=client,
                )
            rows = (await session.execute(select(CampaignRecord))).scalars().all()
        return exc.value, rows

    err, rows = run(scenario())
    assert "DELETE не удался" in str(err)
    assert "и удалена" not in str(err)
    assert rows == []


def test_unique_keitaro_id_in_db(isolated_db) -> None:
    _, session_factory = isolated_db

    async def scenario() -> None:
        async with session_factory() as session:
            session.add(
                CampaignRecord(
                    keitaro_campaign_id=1,
                    name="a",
                    alias="a",
                    geo_code="RU",
                    offer_id=1,
                    status="created",
                )
            )
            await session.commit()
            session.add(
                CampaignRecord(
                    keitaro_campaign_id=1,
                    name="b",
                    alias="b",
                    geo_code="RU",
                    offer_id=1,
                    status="created",
                )
            )
            with pytest.raises(IntegrityError):
                await session.commit()

    run(scenario())
