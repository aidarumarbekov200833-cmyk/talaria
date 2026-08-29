"""Track factual delivery/read/interest and Telegram unread safety snapshots.

Revision ID: 0019
Revises: 0018
"""

from alembic import op
import sqlalchemy as sa


revision = "0019"
down_revision = "0018"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "warmup_accounts",
        sa.Column("unread_count", sa.Integer(), server_default="0", nullable=False),
    )
    op.add_column(
        "warmup_accounts",
        sa.Column("unread_dialogs", sa.Integer(), server_default="0", nullable=False),
    )
    op.add_column(
        "warmup_accounts",
        sa.Column("unread_growth", sa.Integer(), server_default="0", nullable=False),
    )
    op.add_column("warmup_accounts", sa.Column("safety_checked_at", sa.DateTime(timezone=True)))

    op.add_column("campaign_sends", sa.Column("telegram_message_id", sa.Integer()))
    op.add_column("campaign_sends", sa.Column("delivered_at", sa.DateTime(timezone=True)))
    op.add_column("campaign_sends", sa.Column("read_at", sa.DateTime(timezone=True)))
    op.add_column("campaign_sends", sa.Column("interested_at", sa.DateTime(timezone=True)))
    op.create_index(
        "ix_campaign_sends_telegram_message_id",
        "campaign_sends",
        ["telegram_message_id"],
    )

    # Исторические успешные отправки уже были подтверждены Telegram-шлюзом.
    # Заполняем доставку их реальным sent_at; прочтение/интерес не додумываем.
    op.execute(
        "UPDATE campaign_sends SET delivered_at = sent_at "
        "WHERE status = 'sent' AND delivered_at IS NULL"
    )


def downgrade() -> None:
    op.drop_index("ix_campaign_sends_telegram_message_id", table_name="campaign_sends")
    for column in ("interested_at", "read_at", "delivered_at", "telegram_message_id"):
        op.drop_column("campaign_sends", column)
    for column in ("safety_checked_at", "unread_growth", "unread_dialogs", "unread_count"):
        op.drop_column("warmup_accounts", column)
