"""Редактор офферов в существующей кампании: список, add, remove, share.

Логика по видео AdRobot (упрощённая — README «Отличия от референса»):
- каждая кнопка сразу PUT в трекер (без FETCH/PUSH-буфера);
- hard-remove из массива offers (без BRING BACK);
- после add/remove share пересчитывается у активных, сумма всегда 100;
- disabled-офферы сохраняются в payload (state=disabled, share=0) — не теряем.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.services.keitaro_client import KeitaroApiError, KeitaroClient

# После каждой операции — равные доли среди активных; остаток 100%n на хвост
# (3 шт → 33/33/34, как в референсном видео).
SHARE_SUM = 100


class CampaignEditorError(Exception):
    """Ошибка редактора (не ответ трекера как такового)."""

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
class OfferRow:
    """Один оффер в потоке — то, что рисуем в таблице."""

    offer_id: int
    share: int
    state: str = "active"


@dataclass
class StreamSnapshot:
    """Срез потока: stream_id + сырой offers (порядок трекера)."""

    campaign_id: int
    stream_id: int
    stream_name: str
    raw_offers: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class EditorState:
    """Отображаемое состояние (таблица UI)."""

    campaign_id: int
    stream_id: int
    stream_name: str
    offers: list[OfferRow] = field(default_factory=list)

    @property
    def active_offers(self) -> list[OfferRow]:
        return [o for o in self.offers if o.state == "active"]


def equal_shares(count: int) -> list[int]:
    """count чисел, сумма SHARE_SUM; остаток — на хвост (3 → 33,33,34)."""
    if count <= 0:
        return []
    base = SHARE_SUM // count
    rest = SHARE_SUM % count
    return [base + (1 if i >= count - rest else 0) for i in range(count)]


def _is_active(state: Any) -> bool:
    return str(state or "active") == "active"


def _offers_payload(offers: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Массив offers для PUT: disabled сохраняем (share=0), active — равные доли.

    Порядок входного списка не меняем. deleted-строки выкидываем (это и есть remove).
    """
    payload: list[dict[str, Any]] = []
    active_idx: list[int] = []
    for o in offers:
        raw_id = o.get("offer_id")
        if not isinstance(raw_id, int):
            continue
        state = str(o.get("state") or "active")
        if state == "deleted":
            continue
        if _is_active(state):
            payload.append({"offer_id": raw_id, "share": 0, "state": "active"})
            active_idx.append(len(payload) - 1)
        else:
            payload.append({"offer_id": raw_id, "share": 0, "state": "disabled"})
    shares = equal_shares(len(active_idx))
    for i, share in zip(active_idx, shares, strict=True):
        payload[i]["share"] = share
    return payload


def _row_from_raw(o: dict[str, Any]) -> OfferRow | None:
    raw_id = o.get("offer_id")
    if not isinstance(raw_id, int):
        return None
    share = o.get("share")
    return OfferRow(
        offer_id=raw_id,
        share=int(share) if isinstance(share, (int, float)) else 0,
        state=str(o.get("state") or "active"),
    )


def pick_offer_stream(streams: list[dict[str, Any]]) -> dict[str, Any]:
    """Поток, в котором живут офферы: непустой offers → schema action → default."""
    with_offers = [s for s in streams if s.get("offers")]
    if with_offers:
        return with_offers[0]
    by_schema = [s for s in streams if s.get("schema") == "action"]
    if by_schema:
        return by_schema[0]
    by_type = [s for s in streams if s.get("type") == "default"]
    if by_type:
        return by_type[0]
    raise CampaignEditorError(
        "В кампании нет потока под офферы (ни offers, ни schema=action, ни default).",
        status_code=404,
    )


async def _require_campaign(
    client: KeitaroClient, campaign_id: int
) -> dict[str, Any]:
    try:
        return await client.get_campaign(campaign_id)
    except KeitaroApiError as exc:
        if exc.status_code == 404:
            raise CampaignEditorError(
                f"Кампания id={campaign_id} не найдена в трекере.",
                status_code=404,
                keitaro_error=exc,
            ) from exc
        raise CampaignEditorError(
            f"Чтение кампании: {exc}",
            status_code=504 if exc.kind == "timeout" else 502,
            keitaro_error=exc,
        ) from exc


async def load_snapshot(
    *, campaign_id: int, client: KeitaroClient
) -> StreamSnapshot:
    """Один проход: кампания → поток → сырой offers (без повторных GET)."""
    await _require_campaign(client, campaign_id)
    try:
        streams = await client.get_campaign_streams(campaign_id)
    except KeitaroApiError as exc:
        raise CampaignEditorError(
            f"Чтение потоков: {exc}",
            status_code=504 if exc.kind == "timeout" else 502,
            keitaro_error=exc,
        ) from exc

    stream = pick_offer_stream(streams)
    raw_id = stream.get("id")
    if not isinstance(raw_id, int):
        raise CampaignEditorError(
            "Ответ потока без id — контракт трекера нарушен.",
            status_code=502,
        )
    return StreamSnapshot(
        campaign_id=campaign_id,
        stream_id=raw_id,
        stream_name=str(stream.get("name") or ""),
        raw_offers=list(stream.get("offers") or []),
    )


async def get_editor_state(
    *, campaign_id: int, client: KeitaroClient
) -> EditorState:
    """Текущие офферы потока кампании (для таблицы UI)."""
    snap = await load_snapshot(campaign_id=campaign_id, client=client)
    rows: list[OfferRow] = []
    for o in snap.raw_offers:
        row = _row_from_raw(o)
        if row is not None:
            rows.append(row)
    return EditorState(
        campaign_id=snap.campaign_id,
        stream_id=snap.stream_id,
        stream_name=snap.stream_name,
        offers=rows,
    )


async def _put_offers(
    *,
    client: KeitaroClient,
    snap: StreamSnapshot,
    mutate: str,
    offer_id: int,
) -> tuple[int, list[dict[str, Any]]]:
    """mutate=add|remove → новый массив → PUT → (stream_id, payload)."""
    # ключ: offer_id; value — сырой объект; deleted-строки не участвуют в add-check
    by_id: dict[int, dict[str, Any]] = {}
    for o in snap.raw_offers:
        raw = o.get("offer_id")
        if isinstance(raw, int):
            by_id[raw] = o

    if mutate == "add":
        existing = by_id.get(offer_id)
        if existing is not None and _is_active(existing.get("state")):
            raise CampaignEditorError(
                f"Оффер id={offer_id} уже есть в потоке.",
                status_code=409,
            )
        # re-add disabled → active; новый оффер уходит в хвост
        ordered: list[dict[str, Any]] = []
        seen: set[int] = set()
        for o in snap.raw_offers:
            oid = o.get("offer_id")
            if not isinstance(oid, int) or oid in seen:
                continue
            if oid == offer_id:
                ordered.append({"offer_id": offer_id, "state": "active"})
            else:
                ordered.append(o)
            seen.add(oid)
        if offer_id not in seen:
            ordered.append({"offer_id": offer_id, "state": "active"})
    else:
        if offer_id not in by_id:
            raise CampaignEditorError(
                f"Оффер id={offer_id} не найден в потоке.",
                status_code=404,
            )
        # hard-remove: убираем строку целиком
        ordered = [o for o in snap.raw_offers if o.get("offer_id") != offer_id]

    payload_offers = _offers_payload(ordered)
    try:
        await client.update_stream(snap.stream_id, {"offers": payload_offers})
    except KeitaroApiError as exc:
        raise CampaignEditorError(
            f"Сохранение потока: {exc}",
            status_code=504 if exc.kind == "timeout" else 502,
            keitaro_error=exc,
        ) from exc
    return snap.stream_id, payload_offers


async def list_offers_catalog(*, client: KeitaroClient) -> list[dict[str, Any]]:
    """Справочник офферов для автокомплита (id + name)."""
    try:
        offers = await client.list_offers()
    except KeitaroApiError as exc:
        raise CampaignEditorError(
            f"Справочник офферов: {exc}",
            status_code=504 if exc.kind == "timeout" else 502,
            keitaro_error=exc,
        ) from exc
    out = []
    for o in offers:
        oid = o.get("id")
        if isinstance(oid, int):
            out.append({"id": oid, "name": str(o.get("name") or "")})
    return out


async def add_offer(
    *, campaign_id: int, offer_id: int, client: KeitaroClient
) -> tuple[int, list[dict[str, Any]]]:
    """Добавить оффер и пересчитать share (сразу PUT). → (stream_id, payload)."""
    snap = await load_snapshot(campaign_id=campaign_id, client=client)

    catalog = {o["id"] for o in await list_offers_catalog(client=client)}
    if offer_id not in catalog:
        raise CampaignEditorError(
            f"Оффер id={offer_id} не найден в трекере.",
            status_code=404,
        )

    return await _put_offers(
        client=client,
        snap=snap,
        mutate="add",
        offer_id=offer_id,
    )


async def remove_offer(
    *, campaign_id: int, offer_id: int, client: KeitaroClient
) -> tuple[int, list[dict[str, Any]]]:
    """Убрать оффер и пересчитать share (сразу PUT). → (stream_id, payload)."""
    snap = await load_snapshot(campaign_id=campaign_id, client=client)
    return await _put_offers(
        client=client,
        snap=snap,
        mutate="remove",
        offer_id=offer_id,
    )


def state_to_dict(state: EditorState) -> dict[str, Any]:
    """JSON-вид состояния редактора."""
    return {
        "campaign_id": state.campaign_id,
        "stream_id": state.stream_id,
        "stream_name": state.stream_name,
        "offers": [
            {"offer_id": o.offer_id, "share": o.share, "state": o.state}
            for o in state.offers
        ],
        "active_count": len(state.active_offers),
        "share_sum": sum(o.share for o in state.active_offers),
    }


def payload_to_rows(payload: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """PUT-payload → строки для OffersAfterOut (offer_id, share, state)."""
    out = []
    for o in payload:
        rid = o.get("offer_id")
        if not isinstance(rid, int):
            continue
        share = o.get("share")
        out.append(
            {
                "offer_id": rid,
                "share": int(share) if isinstance(share, (int, float)) else 0,
                "state": str(o.get("state") or "active"),
            }
        )
    return out
