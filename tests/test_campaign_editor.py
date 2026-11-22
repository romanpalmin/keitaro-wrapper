"""Тесты редактора офферов: share-пересчёт, PUT, add/remove, HTTP API."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.db as db_module
from app.api.routes import get_keitaro_client
from app.db import get_session, init_db
from app.main import app
from app.services.campaign_editor import (
    CampaignEditorError,
    add_offer,
    equal_shares,
    get_editor_state,
    pick_offer_stream,
    remove_offer,
)
from tests.conftest import RecordingHandler, make_client, run


def test_equal_shares_video_style() -> None:
    assert equal_shares(1) == [100]
    assert equal_shares(2) == [50, 50]
    assert equal_shares(3) == [33, 33, 34]
    assert equal_shares(4) == [25, 25, 25, 25]
    assert equal_shares(0) == []
    assert sum(equal_shares(7)) == 100


def test_offers_payload_keeps_disabled_and_sums_active_100() -> None:
    from app.services.campaign_editor import _offers_payload

    payload = _offers_payload(
        [
            {"offer_id": 1, "state": "active"},
            {"offer_id": 2, "state": "disabled", "share": 0},
            {"offer_id": 3, "state": "active"},
            {"offer_id": 4, "state": "deleted", "share": 0},
        ]
    )
    assert payload == [
        {"offer_id": 1, "share": 50, "state": "active"},
        {"offer_id": 2, "share": 0, "state": "disabled"},
        {"offer_id": 3, "share": 50, "state": "active"},
    ]
    active = [o for o in payload if o["state"] == "active"]
    assert sum(o["share"] for o in active) == 100


def test_add_offer_to_empty_stream_puts_100() -> None:
    handler = _editor_routes()
    handler.route(
        "GET",
        "/campaigns/100/streams",
        [
            {
                "id": 502,
                "type": "default",
                "schema": "action",
                "name": "empty",
                "offers": [],
            }
        ],
    )
    client = make_client(handler)
    stream_id, offers = run(add_offer(campaign_id=100, offer_id=77, client=client))
    puts = [c for c in handler.calls if c["method"] == "PUT"]
    assert puts[0]["body"]["offers"] == [
        {"offer_id": 77, "share": 100, "state": "active"}
    ]
    assert stream_id == 502
    assert offers == [{"offer_id": 77, "share": 100, "state": "active"}]


def test_get_editor_state_preserves_disabled_row() -> None:
    handler = _editor_routes()
    handler.route(
        "GET",
        "/campaigns/100/streams",
        [
            {
                "id": 502,
                "type": "default",
                "schema": "action",
                "offers": [
                    {"offer_id": 42, "share": 100, "state": "active"},
                    {"offer_id": 99, "share": 0, "state": "disabled"},
                ],
            }
        ],
    )
    client = make_client(handler)
    state = run(get_editor_state(campaign_id=100, client=client))
    states = {o.offer_id: o.state for o in state.offers}
    assert states == {42: "active", 99: "disabled"}
    assert len(state.active_offers) == 1


def test_pick_offer_stream_prefers_nonempty_offers() -> None:
    streams = [
        {"id": 1, "type": "forced", "schema": "redirect", "offers": []},
        {"id": 2, "type": "default", "schema": "action",
         "offers": [{"offer_id": 9, "share": 100}]},
    ]
    assert pick_offer_stream(streams)["id"] == 2


def test_pick_offer_stream_fallback_action_schema() -> None:
    streams = [
        {"id": 1, "type": "forced", "schema": "redirect"},
        {"id": 2, "type": "regular", "schema": "action"},
    ]
    assert pick_offer_stream(streams)["id"] == 2


def _editor_routes() -> RecordingHandler:
    h = RecordingHandler()
    h.route("GET", "/campaigns/100", {"id": 100, "name": "c"})
    h.route(
        "GET",
        "/campaigns/100/streams",
        [
            {
                "id": 501,
                "type": "forced",
                "schema": "redirect",
                "name": "geo",
                "offers": [],
            },
            {
                "id": 502,
                "type": "default",
                "schema": "action",
                "name": "default_to_offer",
                "offers": [
                    {"offer_id": 42, "share": 100, "state": "active"},
                ],
            },
        ],
    )
    h.route(
        "GET",
        "/offers",
        [
            {"id": 42, "name": "A"},
            {"id": 77, "name": "B"},
        ],
    )
    h.route("PUT", "/streams/502", {"id": 502, "offers": []})
    return h


def test_get_editor_state_reads_offer_stream() -> None:
    client = make_client(_editor_routes())
    state = run(get_editor_state(campaign_id=100, client=client))
    assert state.stream_id == 502
    assert state.offers[0].offer_id == 42
    assert state.offers[0].share == 100
    assert state.active_offers[0].share == 100


def test_add_offer_puts_recaculated_shares() -> None:
    handler = _editor_routes()
    client = make_client(handler)
    stream_id, offers = run(
        add_offer(campaign_id=100, offer_id=77, client=client)
    )
    puts = [c for c in handler.calls if c["method"] == "PUT"]
    assert puts and puts[0]["path"].endswith("/streams/502")
    body = puts[0]["body"]
    assert body == {
        "offers": [
            {"offer_id": 42, "share": 50, "state": "active"},
            {"offer_id": 77, "share": 50, "state": "active"},
        ]
    }
    assert stream_id == 502
    assert [o["share"] for o in offers] == [50, 50]


def test_remove_offer_puts_remaining_share_100() -> None:
    handler = _editor_routes()
    # два оффера → remove → один со 100
    handler.route(
        "GET",
        "/campaigns/100/streams",
        [
            {
                "id": 502,
                "type": "default",
                "schema": "action",
                "offers": [
                    {"offer_id": 42, "share": 50, "state": "active"},
                    {"offer_id": 77, "share": 50, "state": "active"},
                ],
            }
        ],
    )
    client = make_client(handler)
    stream_id, offers = run(
        remove_offer(campaign_id=100, offer_id=42, client=client)
    )
    puts = [c for c in handler.calls if c["method"] == "PUT"]
    assert puts[0]["body"]["offers"] == [
        {"offer_id": 77, "share": 100, "state": "active"}
    ]
    assert stream_id == 502
    assert offers[0]["share"] == 100


def test_add_duplicate_offer_is_409() -> None:
    client = make_client(_editor_routes())
    with pytest.raises(CampaignEditorError) as exc:
        run(add_offer(campaign_id=100, offer_id=42, client=client))
    assert exc.value.status_code == 409


def test_add_unknown_offer_is_404() -> None:
    client = make_client(_editor_routes())
    with pytest.raises(CampaignEditorError) as exc:
        run(add_offer(campaign_id=100, offer_id=999, client=client))
    assert exc.value.status_code == 404


def test_remove_missing_offer_is_404() -> None:
    client = make_client(_editor_routes())
    with pytest.raises(CampaignEditorError) as exc:
        run(remove_offer(campaign_id=100, offer_id=999, client=client))
    assert exc.value.status_code == 404


def test_update_stream_client_sends_put_body() -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["method"] = request.method
        seen["path"] = request.url.path
        seen["body"] = (
            request.content and __import__("json").loads(request.content)
        )
        return httpx.Response(200, json={"id": 502})

    client = make_client(handler)
    run(client.update_stream(502, {"offers": [{"offer_id": 1, "share": 100}]}))
    assert seen["method"] == "PUT"
    assert seen["path"].endswith("/streams/502")
    assert seen["body"]["offers"][0]["offer_id"] == 1


@pytest.fixture
def api_client(tmp_path) -> Iterator[tuple[Any, Any]]:
    """TestClient + изолированная БД + общий handler редактора."""
    url = f"sqlite+aiosqlite:///{tmp_path}/editor_api.db"
    engine = create_async_engine(url)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    old_engine, old_factory = db_module.engine, db_module.SessionLocal
    db_module.engine = engine
    db_module.SessionLocal = session_factory
    run(init_db())

    handler = _editor_routes()

    async def override_session():
        async with session_factory() as session:
            yield session

    app.dependency_overrides[get_session] = override_session
    app.dependency_overrides[get_keitaro_client] = lambda: make_client(handler)

    with TestClient(app) as client:
        yield client, handler

    app.dependency_overrides.clear()
    db_module.engine = old_engine
    db_module.SessionLocal = old_factory
    run(engine.dispose())


def test_get_offers_api(api_client) -> None:
    client, _ = api_client
    resp = client.get("/api/campaigns/100/offers")
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["stream_id"] == 502
    assert data["share_sum"] == 100
    assert data["offers"][0]["offer_id"] == 42


def test_post_offer_api_201_and_sum(api_client) -> None:
    client, handler = api_client
    resp = client.post(
        "/api/campaigns/100/offers", json={"offer_id": 77}
    )
    assert resp.status_code == 201, resp.text
    data = resp.json()
    assert sum(o["share"] for o in data["offers"]) == 100
    assert any(c["method"] == "PUT" for c in handler.calls)


def test_delete_offer_api(api_client) -> None:
    client, _ = api_client
    resp = client.delete("/api/campaigns/100/offers/42")
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["offers"] == []


def test_edit_page_html(api_client) -> None:
    client, _ = api_client
    resp = client.get("/edit")
    assert resp.status_code == 200
    assert "Редактор офферов" in resp.text
    assert 'id="offers-table"' in resp.text


def test_offers_catalog_api(api_client) -> None:
    client, _ = api_client
    resp = client.get("/api/offers")
    assert resp.status_code == 200
    ids = {o["id"] for o in resp.json()}
    assert {42, 77} <= ids


def test_get_offers_api_campaign_404(api_client) -> None:
    client, handler = api_client
    handler.route(
        "GET",
        "/campaigns/994",
        {"error": "not found"},
        status=404,
    )
    resp = client.get("/api/campaigns/994/offers")
    assert resp.status_code == 404, resp.text
