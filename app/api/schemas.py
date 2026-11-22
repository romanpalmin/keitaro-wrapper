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
