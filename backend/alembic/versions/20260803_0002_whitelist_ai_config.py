"""Белый список, журнал входов, общий ИИ-конфиг; чистка app_settings.

Revision ID: 0002
Revises: 0001
Create Date: 2026-08-03

Что делает эта ревизия (ровно то же, что описывают модели после отказа от
бесплатного тарифа и переноса настроек ИИ на уровень установки):

* создаёт `access_whitelist` — кто вообще допущен ко входу (тарифа «Старт»/free
  больше нет, войти может только добавленный админом);
* создаёт `login_attempts` — журнал попыток входа для админки и поиска
  мультиаккаунтов;
* создаёт `ai_config` — единственная строка (id=1) с настройками ИИ-провайдера;
  раньше они лежали у каждого пользователя в `app_settings`;
* удаляет из `app_settings` пять ai_*-колонок, которые переехали в `ai_config`.

Оговорки те же, что в baseline: `default=` и `onupdate=func.now()` из моделей —
client-side, в DDL их нет; единственный server-side дефолт — now() для
created_at. Имена индексов — ix_<table>_<column>, один в один из метаданных ORM.

subscriptions.plan в схеме остаётся String(20) без server-side default (в
baseline его тоже не было — дефолт "none" client-side). Старые строки со
значением "free" читаются как "none" в коде (plans.effective_plan_slug), данные
трогать не нужно.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # --- access_whitelist ---
    op.create_table(
        "access_whitelist",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("telegram_id", sa.BigInteger(), nullable=True),
        sa.Column("username", sa.String(length=64), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("added_by", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=True,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_access_whitelist_telegram_id",
        "access_whitelist",
        ["telegram_id"],
        unique=True,
    )
    op.create_index(
        "ix_access_whitelist_username", "access_whitelist", ["username"]
    )

    # --- login_attempts ---
    op.create_table(
        "login_attempts",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("owner_id", sa.Integer(), nullable=True),
        sa.Column("telegram_id", sa.BigInteger(), nullable=True),
        sa.Column("username", sa.String(length=64), nullable=True),
        sa.Column("first_name", sa.String(length=128), nullable=True),
        sa.Column("last_name", sa.String(length=128), nullable=True),
        sa.Column("ok", sa.Boolean(), nullable=False),
        sa.Column("reason", sa.String(length=40), nullable=True),
        sa.Column("ip", sa.String(length=64), nullable=True),
        sa.Column("user_agent", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=True,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_login_attempts_owner_id", "login_attempts", ["owner_id"])
    op.create_index(
        "ix_login_attempts_telegram_id", "login_attempts", ["telegram_id"]
    )
    op.create_index("ix_login_attempts_ok", "login_attempts", ["ok"])
    op.create_index("ix_login_attempts_reason", "login_attempts", ["reason"])
    op.create_index("ix_login_attempts_ip", "login_attempts", ["ip"])
    op.create_index(
        "ix_login_attempts_created_at", "login_attempts", ["created_at"]
    )

    # --- ai_config (одна строка на установку, id=1) ---
    op.create_table(
        "ai_config",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("base_url", sa.String(length=300), nullable=True),
        sa.Column("api_key", sa.String(length=300), nullable=True),
        sa.Column("model", sa.String(length=120), nullable=True),
        sa.Column("system_prompt", sa.Text(), nullable=True),
        sa.Column("tools_enabled", sa.Boolean(), nullable=True),
        sa.Column("updated_by", sa.Integer(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=True,
        ),
        sa.PrimaryKeyConstraint("id"),
    )

    # --- app_settings: ai_* переехали в ai_config ---
    op.drop_column("app_settings", "ai_tools_enabled")
    op.drop_column("app_settings", "ai_system_prompt")
    op.drop_column("app_settings", "ai_model")
    op.drop_column("app_settings", "ai_api_key")
    op.drop_column("app_settings", "ai_base_url")


def downgrade() -> None:
    # Возвращаем ai_*-колонки в app_settings (значения не восстанавливаем —
    # они переезжали в ai_config; для сырого отката структуры этого хватает).
    op.add_column(
        "app_settings",
        sa.Column("ai_base_url", sa.String(length=300), nullable=True),
    )
    op.add_column(
        "app_settings",
        sa.Column("ai_api_key", sa.String(length=300), nullable=True),
    )
    op.add_column(
        "app_settings",
        sa.Column("ai_model", sa.String(length=120), nullable=True),
    )
    op.add_column(
        "app_settings",
        sa.Column("ai_system_prompt", sa.Text(), nullable=True),
    )
    op.add_column(
        "app_settings",
        sa.Column("ai_tools_enabled", sa.Boolean(), nullable=True),
    )

    op.drop_table("ai_config")

    op.drop_index("ix_login_attempts_created_at", table_name="login_attempts")
    op.drop_index("ix_login_attempts_ip", table_name="login_attempts")
    op.drop_index("ix_login_attempts_reason", table_name="login_attempts")
    op.drop_index("ix_login_attempts_ok", table_name="login_attempts")
    op.drop_index("ix_login_attempts_telegram_id", table_name="login_attempts")
    op.drop_index("ix_login_attempts_owner_id", table_name="login_attempts")
    op.drop_table("login_attempts")

    op.drop_index(
        "ix_access_whitelist_username", table_name="access_whitelist"
    )
    op.drop_index(
        "ix_access_whitelist_telegram_id", table_name="access_whitelist"
    )
    op.drop_table("access_whitelist")
