"""Baseline: полная схема приложения.

Revision ID: 0001
Revises:
Create Date: 2026-08-03

Базовая ревизия — создаёт все таблицы в точности так, как их описывают модели
(app/models.py): имена индексов (ix_<table>_<column>), именованные уникальные
ограничения (uq_*) и каскады взяты один в один из метаданных ORM, чтобы схема
Алембика не разошлась со схемой, которую поднимает create_all.

Две важные оговорки про то, чего здесь НЕТ:
* `default=` в моделях — это client-side дефолты SQLAlchemy (выставляются при
  создании объекта), в DDL они не попадают. Server-side по умолчанию в схеме
  только одно — now() для created_at/timestamp.
* `onupdate=func.now()` (subscriptions.updated_at, app_settings.updated_at) —
  тоже client-side, в миграции его нет.

Для уже существующей базы старой версии (поднятой create_all) эту ревизию не
накатывают, а помечают как baseline: `alembic stamp 0001`, после чего обычный
`alembic upgrade head` применяет 0002 и все последующие изменения.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # --- users ---
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("telegram_id", sa.BigInteger(), nullable=False),
        sa.Column("username", sa.String(length=64), nullable=True),
        sa.Column("first_name", sa.String(length=128), nullable=True),
        sa.Column("last_name", sa.String(length=128), nullable=True),
        sa.Column("photo_url", sa.String(length=512), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=True,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_users_telegram_id", "users", ["telegram_id"], unique=True)

    # --- offers ---
    op.create_table(
        "offers",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("owner_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("text", sa.Text(), nullable=True),
        sa.Column("image", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=True,
        ),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_offers_owner_id", "offers", ["owner_id"])

    # --- campaigns ---
    op.create_table(
        "campaigns",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("owner_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("database_name", sa.String(length=200), nullable=False),
        sa.Column("channel", sa.String(length=50), nullable=True),
        sa.Column("status", sa.String(length=50), nullable=True),
        sa.Column("progress", sa.Float(), nullable=True),
        sa.Column("total_recipients", sa.Integer(), nullable=True),
        sa.Column("sent_count", sa.Integer(), nullable=True),
        sa.Column("error_count", sa.Integer(), nullable=True),
        sa.Column("offer_ids", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=True,
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_campaigns_owner_id", "campaigns", ["owner_id"])

    # --- campaign_logs ---
    op.create_table(
        "campaign_logs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("campaign_id", sa.Integer(), nullable=False),
        sa.Column(
            "timestamp",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=True,
        ),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("level", sa.String(length=20), nullable=True),
        sa.ForeignKeyConstraint(
            ["campaign_id"], ["campaigns.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_campaign_logs_campaign_id", "campaign_logs", ["campaign_id"])

    # --- api_keys ---
    op.create_table(
        "api_keys",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("owner_id", sa.Integer(), nullable=False),
        sa.Column("label", sa.String(length=120), nullable=True),
        sa.Column("key", sa.String(length=200), nullable=False),
        sa.Column("project_label", sa.String(length=120), nullable=True),
        sa.Column("daily_quota", sa.Integer(), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=True),
        sa.Column("status", sa.String(length=30), nullable=True),
        sa.Column("status_detail", sa.Text(), nullable=True),
        sa.Column("last_checked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=True,
        ),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_api_keys_owner_id", "api_keys", ["owner_id"])

    # --- quota_usage ---
    op.create_table(
        "quota_usage",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("api_key_id", sa.Integer(), nullable=False),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("units", sa.Integer(), nullable=True),
        sa.Column("calls", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(
            ["api_key_id"], ["api_keys.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("api_key_id", "day", name="uq_quota_key_day"),
    )
    op.create_index("ix_quota_usage_api_key_id", "quota_usage", ["api_key_id"])
    op.create_index("ix_quota_usage_day", "quota_usage", ["day"])

    # --- lead_databases ---
    op.create_table(
        "lead_databases",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("owner_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("source", sa.String(length=50), nullable=True),
        sa.Column("mode", sa.String(length=30), nullable=True),
        sa.Column("query", sa.Text(), nullable=True),
        sa.Column("units_spent", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=True,
        ),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_lead_databases_owner_id", "lead_databases", ["owner_id"])

    # --- leads ---
    op.create_table(
        "leads",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("database_id", sa.Integer(), nullable=False),
        sa.Column("channel_id", sa.String(length=64), nullable=False),
        sa.Column("title", sa.String(length=300), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("custom_url", sa.String(length=200), nullable=True),
        sa.Column("country", sa.String(length=8), nullable=True),
        sa.Column("thumbnail", sa.String(length=512), nullable=True),
        sa.Column("subscribers", sa.Integer(), nullable=True),
        sa.Column("video_count", sa.Integer(), nullable=True),
        sa.Column("view_count", sa.BigInteger(), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("matched_keyword", sa.String(length=200), nullable=True),
        sa.Column("status", sa.String(length=30), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=True,
        ),
        sa.ForeignKeyConstraint(
            ["database_id"], ["lead_databases.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("database_id", "channel_id", name="uq_lead_db_channel"),
    )
    op.create_index("ix_leads_database_id", "leads", ["database_id"])
    op.create_index("ix_leads_channel_id", "leads", ["channel_id"])
    op.create_index("ix_leads_status", "leads", ["status"])

    # --- topic_runs ---
    op.create_table(
        "topic_runs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("owner_id", sa.Integer(), nullable=False),
        sa.Column("mode", sa.String(length=30), nullable=True),
        sa.Column("region", sa.String(length=8), nullable=True),
        sa.Column("niches", sa.Text(), nullable=True),
        sa.Column("topics", sa.Text(), nullable=True),
        sa.Column("requested", sa.Integer(), nullable=True),
        sa.Column("units_spent", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=True,
        ),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_topic_runs_owner_id", "topic_runs", ["owner_id"])

    # --- warmup_accounts ---
    op.create_table(
        "warmup_accounts",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("owner_id", sa.Integer(), nullable=False),
        sa.Column("label", sa.String(length=120), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("started_on", sa.Date(), nullable=True),
        sa.Column("plan_days", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(length=30), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=True,
        ),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_warmup_accounts_owner_id", "warmup_accounts", ["owner_id"])
    op.create_index("ix_warmup_accounts_status", "warmup_accounts", ["status"])

    # --- warmup_logs ---
    op.create_table(
        "warmup_logs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("account_id", sa.Integer(), nullable=False),
        sa.Column("day", sa.Integer(), nullable=False),
        sa.Column("date", sa.Date(), nullable=True),
        sa.Column("target_messages", sa.Integer(), nullable=True),
        sa.Column("done_messages", sa.Integer(), nullable=True),
        sa.Column("target_actions", sa.Text(), nullable=True),
        sa.Column("done", sa.Boolean(), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["account_id"], ["warmup_accounts.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("account_id", "day", name="uq_warmup_account_day"),
    )
    op.create_index("ix_warmup_logs_account_id", "warmup_logs", ["account_id"])

    # --- event_logs ---
    op.create_table(
        "event_logs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("owner_id", sa.Integer(), nullable=False),
        sa.Column("scope", sa.String(length=30), nullable=True),
        sa.Column("level", sa.String(length=20), nullable=True),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("ref_type", sa.String(length=30), nullable=True),
        sa.Column("ref_id", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=True,
        ),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_event_logs_owner_id", "event_logs", ["owner_id"])
    op.create_index("ix_event_logs_scope", "event_logs", ["scope"])
    op.create_index("ix_event_logs_created_at", "event_logs", ["created_at"])

    # --- subscriptions ---
    op.create_table(
        "subscriptions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("owner_id", sa.Integer(), nullable=False),
        sa.Column("plan", sa.String(length=20), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=True),
        sa.Column("blocked", sa.Boolean(), nullable=False),
        sa.Column("paid_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("activated_by", sa.Integer(), nullable=True),
        sa.Column("activated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=True,
        ),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_subscriptions_owner_id", "subscriptions", ["owner_id"], unique=True
    )

    # --- token_blacklist ---
    op.create_table(
        "token_blacklist",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("jti", sa.String(length=64), nullable=False),
        sa.Column("owner_id", sa.Integer(), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=True,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_token_blacklist_jti", "token_blacklist", ["jti"], unique=True)
    op.create_index("ix_token_blacklist_owner_id", "token_blacklist", ["owner_id"])

    # --- app_settings ---
    op.create_table(
        "app_settings",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("owner_id", sa.Integer(), nullable=False),
        sa.Column("project_name", sa.String(length=120), nullable=True),
        sa.Column("monthly_limit", sa.Integer(), nullable=True),
        sa.Column("monthly_used", sa.Integer(), nullable=True),
        sa.Column("monthly_period", sa.String(length=7), nullable=True),
        sa.Column("low_limit_threshold", sa.Integer(), nullable=True),
        sa.Column("ai_base_url", sa.String(length=300), nullable=True),
        sa.Column("ai_api_key", sa.String(length=300), nullable=True),
        sa.Column("ai_model", sa.String(length=120), nullable=True),
        sa.Column("ai_system_prompt", sa.Text(), nullable=True),
        sa.Column("ai_tools_enabled", sa.Boolean(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=True,
        ),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_app_settings_owner_id", "app_settings", ["owner_id"], unique=True
    )


def downgrade() -> None:
    op.drop_table("app_settings")
    op.drop_table("token_blacklist")
    op.drop_table("subscriptions")
    op.drop_table("event_logs")
    op.drop_table("warmup_logs")
    op.drop_table("warmup_accounts")
    op.drop_table("topic_runs")
    op.drop_table("leads")
    op.drop_table("lead_databases")
    op.drop_table("quota_usage")
    op.drop_table("api_keys")
    op.drop_table("campaign_logs")
    op.drop_table("campaigns")
    op.drop_table("offers")
    op.drop_table("users")
