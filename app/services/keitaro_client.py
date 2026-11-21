"""HTTP-клиент Keitaro Admin API.

Говорит с трекером: список кампаний/офферов/справочников, потоки кампании.
Ошибки API прячем за KeitaroApiError — наружу не сырые исключения httpx.
"""

from __future__ import annotations

from typing import Any

import httpx

from app.config import settings

# Префикс Admin API относительно хоста трекера (см. docs/api-notes.md)
API_PREFIX = "/admin_api/v1"


class KeitaroApiError(Exception):
    """Любая проблема обращения к Keitaro: нет ключа, сеть, таймаут, не-2xx."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        body: str | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.body = body


class KeitaroClient:
    """Асинхронный клиент Admin API.

    base_url / api_key по умолчанию берутся из Settings (.env).
    transport можно подставить в тестах (httpx.MockTransport) — без сети.
    """

    def __init__(
        self,
        base_url: str | None = None,
        api_key: str | None = None,
        *,
        timeout: float = 15.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        resolved_base = settings.keitaro_base_url if base_url is None else base_url
        resolved_key = settings.keitaro_api_key if api_key is None else api_key
        self._base_url = resolved_base.rstrip("/")
        self._api_key = resolved_key
        self._timeout = timeout
        self._transport = transport
        self._client: httpx.AsyncClient | None = None

    async def __aenter__(self) -> KeitaroClient:
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        """Закрыть внутренний httpx-клиент (если уже открывали)."""
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    def _ensure_client(self) -> httpx.AsyncClient:
        """Лениво создать httpx.AsyncClient; без ключа — сразу ошибка."""
        if not self._api_key:
            raise KeitaroApiError(
                "KEITARO_API_KEY пуст — заполни .env (ключ из админки трекера)."
            )
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=self._base_url + API_PREFIX,
                headers={"Api-Key": self._api_key},
                timeout=self._timeout,
                transport=self._transport,
            )
        return self._client

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: Any | None = None,
    ) -> Any:
        """Один вызов API: сеть → статус → JSON; всё кривое → KeitaroApiError."""
        client = self._ensure_client()
        try:
            response = await client.request(method, path, params=params, json=json)
        except httpx.TimeoutException as exc:
            raise KeitaroApiError(f"Таймаут Keitaro: {method} {path}") from exc
        except httpx.HTTPError as exc:
            raise KeitaroApiError(f"Сетевая ошибка Keitaro: {exc}") from exc

        if response.status_code >= 400:
            raise KeitaroApiError(
                f"Keitaro {method} {path} → HTTP {response.status_code}",
                status_code=response.status_code,
                body=response.text[:500],
            )

        if not response.content:
            return None
        try:
            return response.json()
        except ValueError as exc:
            # 2xx, но тело не JSON (HTML-страница, обрезанный ответ) — не пускаем наружу
            raise KeitaroApiError(
                f"Keitaro {method} {path}: ожидается JSON, получен не-JSON",
                status_code=response.status_code,
                body=response.text[:500],
            ) from exc

    async def list_campaigns(
        self, *, offset: int = 0, limit: int = 100
    ) -> list[dict[str, Any]]:
        """Список кампаний (GET /campaigns)."""
        data = await self._request(
            "GET", "/campaigns", params={"offset": offset, "limit": limit}
        )
        return self._as_list(data)

    async def get_campaign(self, campaign_id: int) -> dict[str, Any]:
        """Одна кампания (GET /campaigns/{id})."""
        data = await self._request("GET", f"/campaigns/{campaign_id}")
        return self._as_dict(data)

    async def get_campaign_streams(self, campaign_id: int) -> list[dict[str, Any]]:
        """Потоки кампании — вместе с offers и filters (GET /campaigns/{id}/streams)."""
        data = await self._request("GET", f"/campaigns/{campaign_id}/streams")
        return self._as_list(data)

    async def list_offers(self) -> list[dict[str, Any]]:
        """Все офферы (GET /offers)."""
        data = await self._request("GET", "/offers")
        return self._as_list(data)

    async def list_domains(self) -> list[dict[str, Any]]:
        """Домены трекера (GET /domains)."""
        data = await self._request("GET", "/domains")
        return self._as_list(data)

    async def list_groups(self, *, group_type: str = "campaigns") -> list[dict[str, Any]]:
        """Группы; group_type: campaigns | offers | landings | domains (GET /groups?type=)."""
        data = await self._request("GET", "/groups", params={"type": group_type})
        return self._as_list(data)

    async def list_traffic_sources(self) -> list[dict[str, Any]]:
        """Источники трафика (GET /traffic_sources)."""
        data = await self._request("GET", "/traffic_sources")
        return self._as_list(data)

    async def list_stream_actions(self) -> list[dict[str, Any]]:
        """Доступные action_type для потоков (GET /streams_actions)."""
        data = await self._request("GET", "/streams_actions")
        return self._as_list(data)

    async def list_stream_filters(self) -> list[dict[str, Any]]:
        """Доступные фильтры потоков — имя geo-фильтра берём отсюда (GET /stream_filters)."""
        data = await self._request("GET", "/stream_filters")
        return self._as_list(data)

    @staticmethod
    def _as_list(data: Any) -> list[dict[str, Any]]:
        """Список из ответа; не список — ошибка контракта API."""
        if not isinstance(data, list):
            raise KeitaroApiError(
                "Неожиданный формат ответа: ожидался JSON-массив",
                body=str(data)[:200],
            )
        return data

    @staticmethod
    def _as_dict(data: Any) -> dict[str, Any]:
        if not isinstance(data, dict):
            raise KeitaroApiError(
                "Неожиданный формат ответа: ожидался JSON-объект",
                body=str(data)[:200],
            )
        return data
