"""ORM-модели: импорт здесь нужен, чтобы Base.metadata видел таблицы до create_all/alembic."""

from app.models.campaign_record import CampaignRecord

__all__ = ["CampaignRecord"]
