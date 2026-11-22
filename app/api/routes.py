"""HTTP-роуты: HTML-формы и JSON API создаватора + редактора офферов."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas import (
    CampaignCreateIn,
    CampaignCreateOut,
    EditorStateOut,
    OfferAddIn,
    OfferRowOut,
    OffersAfterOut,
)
from app.db import get_session
from app.services.campaign_creator import (
    CampaignCreatorError,
    CreatorResult,
    create_campaign_with_streams,
    map_keitaro_for_http,
)
from app.services.campaign_editor import (
    CampaignEditorError,
    add_offer,
    get_editor_state,
    list_offers_catalog,
    remove_offer,
    state_to_dict,
)
from app.services.keitaro_client import KeitaroApiError, KeitaroClient

router = APIRouter()

_STATIC_DIR = Path(__file__).resolve().parent.parent / "static"


def get_keitaro_client() -> KeitaroClient:
    """Зависимость: клиент трекера на один запрос (закрывается в finally роута)."""
    return KeitaroClient()


def _map_editor_error(exc: CampaignEditorError) -> HTTPException:
    detail = str(exc)
    if exc.keitaro_error is not None:
        detail = f"{detail} [{exc.keitaro_error.kind}]"
    return HTTPException(status_code=exc.status_code, detail=detail)


def _map_keitaro(exc: KeitaroApiError) -> HTTPException:
    return HTTPException(
        status_code=map_keitaro_for_http(exc),
        detail=f"{exc} [{exc.kind}]",
    )


@router.get("/", include_in_schema=False)
async def index() -> FileResponse:
    """Главная: HTML-форма создаватора (FileResponse — без read_text в loop)."""
    return FileResponse(_STATIC_DIR / "create_campaign.html")


@router.get("/edit", include_in_schema=False)
async def edit_page() -> FileResponse:
    """Редактор офферов кампании (HTML)."""
    return FileResponse(_STATIC_DIR / "edit_offers.html")


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
        raise _map_keitaro(exc) from exc
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


@router.get(
    "/api/campaigns/{campaign_id}/offers",
    response_model=EditorStateOut,
    responses={
        404: {"description": "Кампания или поток не найдены"},
        502: {"description": "Ошибка трекера"},
        504: {"description": "Таймаут трекера"},
    },
)
async def get_offers(
    campaign_id: int,
    client: Annotated[KeitaroClient, Depends(get_keitaro_client)],
) -> EditorStateOut:
    """Текущие офферы потока кампании (таблица редактора)."""
    try:
        state = await get_editor_state(campaign_id=campaign_id, client=client)
    except CampaignEditorError as exc:
        raise _map_editor_error(exc) from exc
    except KeitaroApiError as exc:
        raise _map_keitaro(exc) from exc
    finally:
        await client.aclose()

    data = state_to_dict(state)
    return EditorStateOut(
        campaign_id=data["campaign_id"],
        stream_id=data["stream_id"],
        stream_name=data["stream_name"],
        offers=[OfferRowOut(**o) for o in data["offers"]],
        active_count=data["active_count"],
        share_sum=data["share_sum"],
    )


@router.get(
    "/api/offers",
    responses={
        502: {"description": "Ошибка трекера"},
        504: {"description": "Таймаут трекера"},
    },
)
async def offers_catalog(
    client: Annotated[KeitaroClient, Depends(get_keitaro_client)],
) -> list[dict]:
    """Справочник офферов для автокомплита: [{id, name}]."""
    try:
        return await list_offers_catalog(client=client)
    except CampaignEditorError as exc:
        raise _map_editor_error(exc) from exc
    except KeitaroApiError as exc:
        raise _map_keitaro(exc) from exc
    finally:
        await client.aclose()


@router.post(
    "/api/campaigns/{campaign_id}/offers",
    response_model=OffersAfterOut,
    status_code=201,
    responses={
        404: {"description": "Кампания/оффер не найдены"},
        409: {"description": "Оффер уже в потоке"},
        502: {"description": "Ошибка трекера"},
        504: {"description": "Таймаут трекера"},
    },
)
async def post_offer(
    campaign_id: int,
    body: OfferAddIn,
    client: Annotated[KeitaroClient, Depends(get_keitaro_client)],
) -> OffersAfterOut:
    """Добавить оффер в поток и сразу PUT с пересчётом share."""
    try:
        stream_id, offers = await add_offer(
            campaign_id=campaign_id, offer_id=body.offer_id, client=client
        )
    except CampaignEditorError as exc:
        raise _map_editor_error(exc) from exc
    except KeitaroApiError as exc:
        raise _map_keitaro(exc) from exc
    finally:
        await client.aclose()

    return OffersAfterOut(
        offers=[
            OfferRowOut(
                offer_id=int(o["offer_id"]),
                share=int(o["share"]),
                state=str(o.get("state") or "active"),
            )
            for o in offers
        ],
        stream_id=stream_id,
        message=f"Добавлен оффер id={body.offer_id}, share пересчитаны.",
    )


@router.delete(
    "/api/campaigns/{campaign_id}/offers/{offer_id}",
    response_model=OffersAfterOut,
    responses={
        404: {"description": "Кампания/оффер не найдены"},
        502: {"description": "Ошибка трекера"},
        504: {"description": "Таймаут трекера"},
    },
)
async def delete_offer(
    campaign_id: int,
    offer_id: int,
    client: Annotated[KeitaroClient, Depends(get_keitaro_client)],
) -> OffersAfterOut:
    """Убрать оффер из потока и сразу PUT с пересчётом share."""
    try:
        stream_id, offers = await remove_offer(
            campaign_id=campaign_id, offer_id=offer_id, client=client
        )
    except CampaignEditorError as exc:
        raise _map_editor_error(exc) from exc
    except KeitaroApiError as exc:
        raise _map_keitaro(exc) from exc
    finally:
        await client.aclose()

    return OffersAfterOut(
        offers=[
            OfferRowOut(
                offer_id=int(o["offer_id"]),
                share=int(o["share"]),
                state=str(o.get("state") or "active"),
            )
            for o in offers
        ],
        stream_id=stream_id,
        message=f"Оффер id={offer_id} удалён, share пересчитаны.",
    )
