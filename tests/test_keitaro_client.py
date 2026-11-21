"""Тесты KeitaroClient на httpx.MockTransport — без живого трекера."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from app.services.keitaro_client import API_PREFIX, KeitaroApiError, KeitaroClient

BASE = "https://tracker.test"


def asyncio_run(coro: Any) -> Any:
    import asyncio

    return asyncio.run(coro)


def make_client(
    handler: Any,
    *,
    api_key: str = "test-key",
) -> KeitaroClient:
    """Клиент с мок-транспортом: handler(request) -> httpx.Response."""
    transport = httpx.MockTransport(handler)
    return KeitaroClient(base_url=BASE, api_key=api_key, transport=transport)


def test_missing_api_key_raises() -> None:
    client = KeitaroClient(base_url=BASE, api_key="", transport=httpx.MockTransport(
        lambda _: httpx.Response(200, json=[])
    ))

    with pytest.raises(KeitaroApiError, match="KEITARO_API_KEY"):
        # ключ проверяем до сетевого вызова — handler не должен найтись
        asyncio_run(client.list_offers())


def test_list_offers_ok() -> None:
    payload = [{"id": 7, "name": "Offer A"}]

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == f"{API_PREFIX}/offers"
        assert request.headers["Api-Key"] == "test-key"
        return httpx.Response(200, json=payload)

    client = make_client(handler)
    result = asyncio_run(client.list_offers())
    assert result == payload


def test_list_campaigns_sends_offset_limit() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == f"{API_PREFIX}/campaigns"
        assert request.url.params["offset"] == "20"
        assert request.url.params["limit"] == "5"
        return httpx.Response(200, json=[])

    client = make_client(handler)
    result = asyncio_run(client.list_campaigns(offset=20, limit=5))
    assert result == []


def test_get_campaign_streams() -> None:
    payload = [
        {
            "id": 1,
            "offers": [{"offer_id": 9, "share": 100, "state": "active"}],
            "filters": [{"name": "country", "mode": "accept", "payload": "RU"}],
        }
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == f"{API_PREFIX}/campaigns/42/streams"
        return httpx.Response(200, json=payload)

    client = make_client(handler)
    result = asyncio_run(client.get_campaign_streams(42))
    assert result[0]["offers"][0]["offer_id"] == 9


def test_http_error_becomes_keitaro_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": "invalid api key"})

    client = make_client(handler)
    with pytest.raises(KeitaroApiError) as exc_info:
        asyncio_run(client.list_domains())

    assert exc_info.value.status_code == 401
    assert "invalid api key" in (exc_info.value.body or "")


def test_timeout_becomes_keitaro_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("boom", request=request)

    client = make_client(handler)
    with pytest.raises(KeitaroApiError, match="Таймаут"):
        asyncio_run(client.list_offers())


def test_non_list_response_is_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"unexpected": True})

    client = make_client(handler)
    with pytest.raises(KeitaroApiError, match="массив"):
        asyncio_run(client.list_offers())


def test_key_never_in_body_of_success_path_headers_only() -> None:
    """Ключ уходит только в заголовке, не в URL и не в query."""

    def handler(request: httpx.Request) -> httpx.Response:
        assert "Api-Key" in request.headers
        assert "test-key" not in str(request.url)
        return httpx.Response(200, json=[])

    client = make_client(handler)
    asyncio_run(client.list_groups(group_type="campaigns"))


def test_non_json_body_on_200_is_keitaro_error() -> None:
    """2xx с HTML/мусором — KeitaroApiError, не сырой JSONDecodeError."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            text="<html>oops</html>",
            headers={"content-type": "text/html"},
        )

    client = make_client(handler)
    with pytest.raises(KeitaroApiError, match="не-JSON") as exc_info:
        asyncio_run(client.list_offers())

    assert exc_info.value.status_code == 200
    assert "oops" in (exc_info.value.body or "")


def test_error_body_is_truncated() -> None:
    long_body = "x" * 2000

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text=long_body)

    client = make_client(handler)
    with pytest.raises(KeitaroApiError) as exc_info:
        asyncio_run(client.list_offers())

    assert exc_info.value.status_code == 500
    assert exc_info.value.body is not None
    assert len(exc_info.value.body) <= 500


def test_request_json_is_sent_for_post() -> None:
    """Смоук: POST с телом (для будущего создаватора) доходит до транспорта."""
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["method"] = request.method
        seen["path"] = request.url.path
        seen["body"] = json.loads(request.content.decode())
        return httpx.Response(200, json={"id": 1})

    client = make_client(handler)
    result = asyncio_run(client._request("POST", "/campaigns", json={"name": "x"}))
    assert seen["method"] == "POST"
    assert seen["path"] == f"{API_PREFIX}/campaigns"
    assert seen["body"] == {"name": "x"}
    assert result == {"id": 1}
