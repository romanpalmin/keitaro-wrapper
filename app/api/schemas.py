"""Pydantic-схемы HTTP API создаватора."""

from __future__ import annotations

from pydantic import BaseModel, Field


class CampaignCreateIn(BaseModel):
    """Тело POST /api/campaigns: три поля по ТЗ."""

    name: str = Field(min_length=1, max_length=200, description="Название кампании")
    geo_code: str = Field(
        min_length=2,
        max_length=8,
        pattern=r"^[A-Za-z]{2,8}$",
        description="Geo-код страны, например MX",
    )
    offer_id: int = Field(gt=0, description="ID оффера в Keitaro")


class CampaignCreateOut(BaseModel):
    """Ответ создаватора: статус и то, что получилось в трекере/журнале."""

    status: str
    name: str
    alias: str
    geo_code: str
    offer_id: int
    keitaro_campaign_id: int | None = None
    stream_ids: list[int] = Field(default_factory=list)
    message: str = ""
    record_id: int | None = None


class OfferRowOut(BaseModel):
    """Оффер в потоке кампании."""

    offer_id: int
    share: int
    state: str = "active"


class EditorStateOut(BaseModel):
    """Срез редактора: поток с офферами."""

    campaign_id: int
    stream_id: int
    stream_name: str
    offers: list[OfferRowOut] = Field(default_factory=list)
    active_count: int = 0
    share_sum: int = 0


class OfferAddIn(BaseModel):
    """Тело POST: добавить оффер в поток."""

    offer_id: int = Field(gt=0, description="ID оффера в Keitaro")


class OffersAfterOut(BaseModel):
    """Массив offers после add/remove (share пересчитаны, сумма 100)."""

    offers: list[OfferRowOut] = Field(default_factory=list)
    stream_id: int
    message: str = ""
