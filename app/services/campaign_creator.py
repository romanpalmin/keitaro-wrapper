"""Создаватор кампаний: имя + geo + offer_id → кампания с двумя потоками в Keitaro.

Порядок (см. docs/plan-blocks.md, блок 3):
1. Справочники: первый домен, группа (campaigns), источник трафика.
2. Проверка, что offer_id есть в трекере — иначе 404 без создания.
3. POST /campaigns (префикс test_palmin_) → сразу CampaignRecord status=partial.
4. Поток 1: forced, фильтр accept на geo → redirect на Google.
5. Поток 2: default → оффер share 100.
6. Сбой на потоках → DELETE кампании (откат); ушёл — failed, нет — partial.

Известные ограничения (см. README):
- timeout/кривой ответ на POST /campaigns: ищем созданную кампанию по alias
  через GET /campaigns и пытаемся удалить; если поиск тоже упал — в трекере
  может остаться мусор без записи в БД.
- Повторный одинаковый name → alias занят → трекер 400 (без авто-переименования).
- Константы потоков (action_type/schema/filter name) — до живого probe
  GET /streams_actions + /stream_filters.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import CampaignRecord
from app.services.keitaro_client import KeitaroApiError, KeitaroClient

# Все кампании, созданные обёрткой, — с префиксом (правило тестового)
NAME_PREFIX = "test_palmin_"

# Константы контракта трекера — сверить живым probe (GET /streams_actions, /stream_filters),
# когда доступ к админке вернётся; правка в одном месте.
GEO_FILTER_NAME = "country"
REDIRECT_ACTION_TYPE = "http"
OFFER_ACTION_TYPE = "offer"
REDIRECT_SCHEMA = "redirect"
OFFER_SCHEMA = "action"
GOOGLE_URL = "https://www.google.com"


class CampaignCreatorError(Exception):
    """Ошибка создания на стороне обёртки (не ответ трекера как такового)."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int = 502,
        keitaro_error: KeitaroApiError | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.keitaro_error = keitaro_error


@dataclass
class CreatorResult:
    """Итог одной попытки — то, что отдаём в HTTP-ответе."""

    status: str  # created | partial | failed
    name: str
    alias: str
    geo_code: str
    offer_id: int
    keitaro_campaign_id: int | None = None
    stream_ids: list[int] = field(default_factory=list)
    message: str = ""
    record_id: int | None = None


def build_alias(name: str) -> str:
    """Alias для URL: латиница/цифры/подчёркивание, без завершающего _."""
    cleaned = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")
    return cleaned or "campaign"


def with_prefix(name: str) -> str:
    """Гарантирует префикс test_palmin_ в имени кампании."""
    name = name.strip()
    if name.startswith(NAME_PREFIX):
        return name
    return f"{NAME_PREFIX}{name}"


def _pick_dict_id(
    items: list[dict[str, Any]],
    *,
    label: str,
    prefer_active: bool = True,
) -> int:
    """Первый id из справочника (active, если есть) — иначе понятная ошибка."""
    if not items:
        raise CampaignCreatorError(
            f"В трекере пуст справочник: {label} — нечего подставлять в кампанию."
        )

    def _state(item: dict[str, Any]) -> str:
        return str(item.get("state") or "").lower()

    if prefer_active:
        active = [i for i in items if _state(i) in ("", "active", "enabled")]
        pool = active or items
    else:
        pool = items

    first = pool[0]
    raw_id = first.get("id")
    if not isinstance(raw_id, int):
        raise CampaignCreatorError(f"Справочник {label}: у записи нет числового id.")
    return raw_id


async def create_campaign_with_streams(
    *,
    name: str,
    geo_code: str,
    offer_id: int,
    session: AsyncSession,
    client: KeitaroClient,
) -> CreatorResult:
    """Полный сценарий создаватора. Ошибки — CampaignCreatorError / KeitaroApiError."""
    final_name = with_prefix(name)
    alias = build_alias(final_name)
    geo = geo_code.strip().upper()

    # 1) Оффер должен существовать — до создания кампании
    offers = await client.list_offers()
    offer_ids = {o.get("id") for o in offers}
    if offer_id not in offer_ids:
        raise CampaignCreatorError(
            f"Оффер id={offer_id} не найден в трекере.",
            status_code=404,
        )

    # 2) Справочники для домена/группы/источника
    domain_id = _pick_dict_id(await client.list_domains(), label="домены")
    group_id = _pick_dict_id(
        await client.list_groups(group_type="campaigns"), label="группы кампаний"
    )
    source_id = _pick_dict_id(
        await client.list_traffic_sources(), label="источники трафика"
    )

    # 3) Кампания
    try:
        campaign = await client.create_campaign(
            {
                "name": final_name,
                "alias": alias,
                "domain_id": domain_id,
                "group_id": str(group_id),
                "traffic_source_id": source_id,
                "state": "active",
                "type": "position",
            }
        )
    except KeitaroApiError as exc:
        # timeout/network: запрос мог дойти и создать кампанию — пробуем убрать
        if exc.kind in ("timeout", "network"):
            found_id = await _find_campaign_id_by_alias(client, alias)
            if found_id is not None:
                removed = await _try_delete_campaign(client, found_id)
                fate = (
                    f"id={found_id} и удалена (best-effort откат)"
                    if removed
                    else (
                        f"id={found_id}, но DELETE не удался — "
                        "возможен мусор в трекере"
                    )
                )
                raise CampaignCreatorError(
                    f"Создание кампании: {exc}; кампания по alias найдена {fate}.",
                    status_code=504 if exc.kind == "timeout" else 502,
                    keitaro_error=exc,
                ) from exc
        raise _wrap_keitaro(exc, step="создание кампании") from exc

    keitaro_id = campaign.get("id")
    if not isinstance(keitaro_id, int):
        # Ответ без id: ищем по alias — если кампания висит, убираем
        found_id = await _find_campaign_id_by_alias(client, alias)
        if found_id is not None:
            removed = await _try_delete_campaign(client, found_id)
            fate = (
                f"id={found_id} и удалена (best-effort откат)"
                if removed
                else (
                    f"id={found_id}, но DELETE не удался — "
                    "возможен мусор в трекере без записи в БД"
                )
            )
            raise CampaignCreatorError(
                f"Keitaro не вернул id; кампания по alias найдена {fate}.",
                status_code=502,
            )
        raise CampaignCreatorError(
            "Keitaro создал кампанию, но не вернул id, и поиск по alias "
            "ничего не нашёл — возможен мусор в трекере без записи в БД.",
            status_code=502,
        )

    # Журнал сразу: даже если потоки упадут, в базе будет partial с id
    record = CampaignRecord(
        keitaro_campaign_id=keitaro_id,
        name=final_name,
        alias=alias,
        geo_code=geo,
        offer_id=offer_id,
        status="partial",
    )
    session.add(record)
    await session.commit()
    await session.refresh(record)

    result = CreatorResult(
        status="partial",
        name=final_name,
        alias=alias,
        geo_code=geo,
        offer_id=offer_id,
        keitaro_campaign_id=keitaro_id,
        message="Кампания создана, потоки — нет.",
        record_id=record.id,
    )

    # 4–5) Два потока
    geo_stream_payload = {
        "campaign_id": keitaro_id,
        "type": "forced",
        "name": f"geo_{geo}_to_google",
        "position": 1,
        "schema": REDIRECT_SCHEMA,
        "action_type": REDIRECT_ACTION_TYPE,
        "action_options": {"url": GOOGLE_URL},
        "state": "active",
        "filters": [
            {
                "name": GEO_FILTER_NAME,
                "mode": "accept",
                "payload": [geo],
            }
        ],
    }
    offer_stream_payload = {
        "campaign_id": keitaro_id,
        "type": "default",
        "name": "default_to_offer",
        "position": 2,
        "schema": OFFER_SCHEMA,
        "action_type": OFFER_ACTION_TYPE,
        "state": "active",
        "offers": [
            {"offer_id": offer_id, "share": 100, "state": "active"},
        ],
    }

    try:
        geo_stream = await client.create_stream(geo_stream_payload)
        result.stream_ids.append(_stream_id(geo_stream))
        offer_stream = await client.create_stream(offer_stream_payload)
        result.stream_ids.append(_stream_id(offer_stream))
    except KeitaroApiError as exc:
        # 6) Откат: удаляем кампанию, чтобы не осталась полукампания без потоков
        rolled_back = await _try_delete_campaign(client, keitaro_id)
        if rolled_back:
            record.status = "failed"
            result.status = "failed"
            result.message = (
                f"Сбой на потоках ({exc}); кампания id={keitaro_id} удалена (откат)."
            )
        else:
            record.status = "partial"
            result.status = "partial"
            result.message = (
                f"Сбой на потоках ({exc}); откат не удался — "
                f"кампания id={keitaro_id} осталась в трекере."
            )
        await session.commit()
        raise CampaignCreatorError(
            result.message,
            status_code=502,
            keitaro_error=exc,
        ) from exc

    # Успех
    record.status = "created"
    result.status = "created"
    result.message = f"Кампания id={keitaro_id} создана с двумя потоками."
    await session.commit()
    return result


def _stream_id(stream: dict[str, Any]) -> int:
    """id потока из ответа; без него — contract-ошибка (путь отката, не -1)."""
    raw = stream.get("id")
    if isinstance(raw, int):
        return raw
    raise KeitaroApiError(
        "Ответ потока без id — не подтверждаем created.",
        kind="contract",
        body=str(stream)[:200],
    )


async def _find_campaign_id_by_alias(
    client: KeitaroClient, alias: str
) -> int | None:
    """Best-effort: есть ли в трекере кампания с нашим alias (после timeout/кривого ответа)."""
    try:
        campaigns = await client.list_campaigns(limit=100)
    except KeitaroApiError:
        return None
    for item in campaigns:
        if item.get("alias") == alias:
            raw = item.get("id")
            if isinstance(raw, int):
                return raw
    return None


async def _try_delete_campaign(client: KeitaroClient, campaign_id: int) -> bool:
    """Откат; False — не смогли удалить (тогда статус partial, id сохраняем)."""
    try:
        await client.delete_campaign(campaign_id)
        return True
    except KeitaroApiError:
        return False


def _wrap_keitaro(exc: KeitaroApiError, *, step: str) -> CampaignCreatorError:
    """Маппинг ошибок трекера → HTTP-коды обёртки (timeout → 504)."""
    if exc.kind == "timeout":
        status = 504
    elif exc.kind == "config":
        status = 503
    else:
        status = 502
    return CampaignCreatorError(
        f"{step}: {exc}",
        status_code=status,
        keitaro_error=exc,
    )


def map_keitaro_for_http(exc: KeitaroApiError) -> int:
    """kind → HTTP-код для ошибок, пойманных вне create_campaign_with_streams."""
    if exc.kind == "timeout":
        return 504
    if exc.kind == "config":
        return 503
    return 502
