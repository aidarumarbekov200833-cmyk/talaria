"""Add the approved delivery and Telegram-verification query indexes.

Revision ID: 0022
Revises: 0021
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0022"
down_revision = "0021"
branch_labels = None
depends_on = None

CAMPAIGN_INDEX = "ix_campaign_sends_owner_account_status_sent_at"
LEAD_INDEX = "ix_leads_tg_verified_account_checked_at"


def _columns(table: str) -> set[str]:
    return {column["name"] for column in sa.inspect(op.get_bind()).get_columns(table)}


def _indexes(table: str) -> set[str]:
    return {index["name"] for index in sa.inspect(op.get_bind()).get_indexes(table)}


def upgrade() -> None:
    # Correct linear installs already own this column through revision 0008.
    # Keep the guard for deployed databases whose historical schema drifted.
    if "tg_checked_at" not in _columns("leads"):
        op.add_column(
            "leads",
            sa.Column("tg_checked_at", sa.DateTime(timezone=True), nullable=True),
        )

    if CAMPAIGN_INDEX not in _indexes("campaign_sends"):
        op.create_index(
            "ix_campaign_sends_owner_account_status_sent_at",
            "campaign_sends",
            ["owner_id", "account_id", "status", "sent_at"],
        )
    if LEAD_INDEX not in _indexes("leads"):
        op.create_index(
            "ix_leads_tg_verified_account_checked_at",
            "leads",
            ["tg_verified_by_account_id", "tg_checked_at"],
        )


def downgrade() -> None:
    campaign_indexes = _indexes("campaign_sends")
    lead_indexes = _indexes("leads")
    if CAMPAIGN_INDEX in campaign_indexes:
        op.drop_index(CAMPAIGN_INDEX, table_name="campaign_sends")
    if LEAD_INDEX in lead_indexes:
        op.drop_index(LEAD_INDEX, table_name="leads")
    # tg_checked_at belongs to revision 0008. Dropping it here would make the
    # resulting 0021 schema disagree with its own migration history.
