"""Публичные методы клиента: create_campaign / create_stream / delete, kind ошибок."""

from __future__ import annotations

import httpx
import pytest

from app.services.keitaro_client import API_PREFIX, KeitaroApiError
from tests.conftest import make_client, request_json, run


def test_create_campaign_sends_required_fields() -> None:
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["method"] = request.method
        seen["body"] = request_json(request)
        return httpx.Response(200, json={"id": 9, "name": "n", "alias": "a"})

    client = make_client(handler)
    result = run(
        client.create_campaign({"name": "test_palmin_x", "alias": "test_palmin_x"})
    )
    assert seen["method"] == "POST"
    assert seen["path"] == f"{API_PREFIX}/campaigns"
    assert seen["body"] == {"name": "test_palmin_x", "alias": "test_palmin_x"}
    assert result["id"] == 9


def test_create_stream_sends_required_fields() -> None:
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["body"] = request_json(request)
        return httpx.Response(200, json={"id": 55})

    client = make_client(handler)
    payload = {
        "campaign_id": 1,
        "schema": "redirect",
        "type": "forced",
        "name": "geo",
        "action_type": "http",
    }
    result = run(client.create_stream(payload))
    assert seen["path"] == f"{API_PREFIX}/streams"
    assert seen["body"] == payload
    assert result["id"] == 55


def test_delete_campaign_uses_delete_method() -> None:
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["method"] = request.method
        seen["path"] = request.url.path
        return httpx.Response(200, json={"ok": True})

    client = make_client(handler)
    run(client.delete_campaign(77))
    assert seen["method"] == "DELETE"
    assert seen["path"] == f"{API_PREFIX}/campaigns/77"


def test_http_error_kind_is_http() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"error": "bad"})

    client = make_client(handler)
    with pytest.raises(KeitaroApiError) as exc:
        run(client.create_campaign({"name": "x", "alias": "x"}))
    assert exc.value.kind == "http"
    assert exc.value.status_code == 400


def test_contract_error_kind() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=["not", "a", "dict"])

    client = make_client(handler)
    with pytest.raises(KeitaroApiError) as exc:
        run(client.create_campaign({"name": "x", "alias": "x"}))
    assert exc.value.kind == "contract"


def test_missing_key_kind_config() -> None:
    client = make_client(lambda _: httpx.Response(200, json={}), api_key="")
    with pytest.raises(KeitaroApiError) as exc:
        run(client.list_offers())
    assert exc.value.kind == "config"
