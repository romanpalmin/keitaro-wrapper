"""Локальная история кампаний, созданных через обёртку.

В трекере живут сами кампании; здесь — журнал «что мы создали/правили»,
чтобы в редакторе показывать свои кампании и видеть статус без опроса API.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class CampaignRecord(Base):
    """Одна созданная (или попытка создания) кампания в Keitaro."""

    __tablename__ = "campaign_records"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # id кампании в Keitaro; None — создание не дошло до конца
    keitaro_campaign_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    alias: Mapped[str | None] = mapped_column(String(255), nullable=True)
    geo_code: Mapped[str | None] = mapped_column(String(8), nullable=True)
    offer_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # created | partial | failed — для идемпотентности при частичном отказе (см. план блока 3)
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="created", server_default="created"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now(), onupdate=func.now()
    )
