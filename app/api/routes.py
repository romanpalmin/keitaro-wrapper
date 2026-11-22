"""HTTP-роуты: HTML-форма и JSON API создаватора кампаний."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas import CampaignCreateIn, CampaignCreateOut
from app.db import get_session
from app.services.campaign_creator import (
    CampaignCreatorError,
    CreatorResult,
    create_campaign_with_streams,
    map_keitaro_for_http,
)
from app.services.keitaro_client import KeitaroApiError, KeitaroClient

router = APIRouter()

_STATIC_DIR = Path(__file__).resolve().parent.parent / "static"


def get_keitaro_client() -> KeitaroClient:
    """Зависимость: клиент трекера на один запрос (закрывается в finally роута)."""
    return KeitaroClient()


@router.get("/", include_in_schema=False)
async def index() -> FileResponse:
    """Главная: HTML-форма создаватора (FileResponse — без read_text в loop)."""
    return FileResponse(_STATIC_DIR / "create_campaign.html")


@router.post(
    "/api/campaigns",
    response_model=CampaignCreateOut,
    status_code=201,
    responses={
        404: {"description": "Оффер не найден"},
        502: {"description": "Ошибка трекера"},
        503: {"description": "Не настроен ключ/окружение"},
        504: {"description": "Таймаут трекера"},
    },
)
async def post_campaign(
    body: CampaignCreateIn,
    session: Annotated[AsyncSession, Depends(get_session)],
    client: Annotated[KeitaroClient, Depends(get_keitaro_client)],
) -> CampaignCreateOut:
    """Создать кампанию с двумя потоками (создаватор)."""
    try:
        result: CreatorResult = await create_campaign_with_streams(
            name=body.name,
            geo_code=body.geo_code,
            offer_id=body.offer_id,
            session=session,
            client=client,
        )
    except CampaignCreatorError as exc:
        detail = str(exc)
        if exc.keitaro_error is not None:
            detail = f"{detail} [{exc.keitaro_error.kind}]"
        raise HTTPException(status_code=exc.status_code, detail=detail) from exc
    except KeitaroApiError as exc:
        raise HTTPException(
            status_code=map_keitaro_for_http(exc),
            detail=f"{exc} [{exc.kind}]",
        ) from exc
    finally:
        await client.aclose()

    return CampaignCreateOut(
        status=result.status,
        name=result.name,
        alias=result.alias,
        geo_code=result.geo_code,
        offer_id=result.offer_id,
        keitaro_campaign_id=result.keitaro_campaign_id,
        stream_ids=result.stream_ids,
        message=result.message,
        record_id=result.record_id,
    )
