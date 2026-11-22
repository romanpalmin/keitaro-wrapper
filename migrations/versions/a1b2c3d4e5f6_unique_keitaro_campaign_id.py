"""unique keitaro_campaign_id on campaign_records

Revision ID: a1b2c3d4e5f6
Revises: 85095b3f71fb
Create Date: 2026-11-22 03:10:00.000000

"""
from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'a1b2c3d4e5f6'
down_revision: str | Sequence[str] | None = '85095b3f71fb'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table("campaign_records") as batch_op:
        batch_op.create_unique_constraint(
            "uq_campaign_records_keitaro_id",
            ["keitaro_campaign_id"],
        )


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("campaign_records") as batch_op:
        batch_op.drop_constraint(
            "uq_campaign_records_keitaro_id",
            type_="unique",
        )
