"""Persist exact short-lived watches for test Telegram replies.

Revision ID: 0021
Revises: 0020
"""

import sqlalchemy as sa

from alembic import op

revision = "0021"
down_revision = "0020"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "test_reply_watches",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "owner_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "account_id",
            sa.Integer(),
            sa.ForeignKey("warmup_accounts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("recipient_tg_id", sa.String(length=64), server_default="", nullable=False),
        sa.Column("recipient_username", sa.String(length=64), server_default="", nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_test_reply_watches_owner_id", "test_reply_watches", ["owner_id"])
    op.create_index("ix_test_reply_watches_account_id", "test_reply_watches", ["account_id"])
    op.create_index("ix_test_reply_watches_expires_at", "test_reply_watches", ["expires_at"])
    op.create_index(
        "ix_test_reply_watches_owner_account_tg",
        "test_reply_watches",
        ["owner_id", "account_id", "recipient_tg_id"],
    )
    op.create_index(
        "ix_test_reply_watches_owner_account_username",
        "test_reply_watches",
        ["owner_id", "account_id", "recipient_username"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_test_reply_watches_owner_account_username",
        table_name="test_reply_watches",
    )
    op.drop_index("ix_test_reply_watches_owner_account_tg", table_name="test_reply_watches")
    op.drop_index("ix_test_reply_watches_expires_at", table_name="test_reply_watches")
    op.drop_index("ix_test_reply_watches_account_id", table_name="test_reply_watches")
    op.drop_index("ix_test_reply_watches_owner_id", table_name="test_reply_watches")
    op.drop_table("test_reply_watches")
