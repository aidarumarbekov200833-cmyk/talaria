"""Persist Telegram account runtime health and SpamBot checks.

Revision ID: 0018
Revises: 0017
"""

from alembic import op
import sqlalchemy as sa


revision = "0018"
down_revision = "0017"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "warmup_accounts",
        sa.Column("operational_status", sa.String(length=30), server_default="ready", nullable=False),
    )
    op.add_column("warmup_accounts", sa.Column("cooldown_until", sa.DateTime(timezone=True)))
    op.add_column(
        "warmup_accounts",
        sa.Column("last_tg_error", sa.String(length=500), server_default="", nullable=False),
    )
    op.add_column(
        "warmup_accounts",
        sa.Column("last_tg_error_code", sa.String(length=64), server_default="", nullable=False),
    )
    op.add_column("warmup_accounts", sa.Column("last_tg_error_at", sa.DateTime(timezone=True)))
    op.add_column("warmup_accounts", sa.Column("last_success_at", sa.DateTime(timezone=True)))
    op.add_column(
        "warmup_accounts",
        sa.Column("risk_score", sa.Integer(), server_default="0", nullable=False),
    )
    op.add_column(
        "warmup_accounts",
        sa.Column("consecutive_errors", sa.Integer(), server_default="0", nullable=False),
    )
    op.add_column(
        "warmup_accounts",
        sa.Column("spambot_status", sa.String(length=30), server_default="unknown", nullable=False),
    )
    op.add_column(
        "warmup_accounts",
        sa.Column("spambot_detail", sa.Text(), server_default="", nullable=False),
    )
    op.add_column("warmup_accounts", sa.Column("spambot_checked_at", sa.DateTime(timezone=True)))
    op.add_column("warmup_accounts", sa.Column("spambot_appealed_at", sa.DateTime(timezone=True)))
    op.create_index("ix_warmup_accounts_operational_status", "warmup_accounts", ["operational_status"])
    op.create_index("ix_warmup_accounts_cooldown_until", "warmup_accounts", ["cooldown_until"])


def downgrade() -> None:
    op.drop_index("ix_warmup_accounts_cooldown_until", table_name="warmup_accounts")
    op.drop_index("ix_warmup_accounts_operational_status", table_name="warmup_accounts")
    for column in (
        "spambot_appealed_at",
        "spambot_checked_at",
        "spambot_detail",
        "spambot_status",
        "consecutive_errors",
        "risk_score",
        "last_success_at",
        "last_tg_error_at",
        "last_tg_error_code",
        "last_tg_error",
        "cooldown_until",
        "operational_status",
    ):
        op.drop_column("warmup_accounts", column)
