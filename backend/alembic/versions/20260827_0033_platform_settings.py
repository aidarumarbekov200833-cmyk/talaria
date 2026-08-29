"""Глобальные настройки площадки: рубильник открытой регистрации.

Revision ID: 0033
Revises: 0032

Одна строка на всю установку. Отдельная таблица, а не переменная окружения,
потому что закрывать регистрацию нужно немедленно и из админки: если пойдёт
волна регистраций, перезапускать контейнер ради одной переменной — плохой план.

Значение по умолчанию берётся из окружения (settings.open_registration), пока
строки нет: так поведение свежей установки не зависит от миграции.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0033"
down_revision = "0032"
branch_labels = None
depends_on = None


def _tables() -> set[str]:
    bind = op.get_bind()
    return set(sa.inspect(bind).get_table_names())


def upgrade() -> None:
    if "platform_settings" not in _tables():
        op.create_table(
            "platform_settings",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("open_registration", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("updated_by", sa.Integer()),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        )


def downgrade() -> None:
    if "platform_settings" in _tables():
        op.drop_table("platform_settings")
