"""Выключатель уведомлений об ответе лида.

Revision ID: 0023
Revises: 0022
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0023"
down_revision = "0022"
branch_labels = None
depends_on = None

TABLE = "app_settings"
COLUMN = "reply_notify"


def _columns(table: str) -> set[str]:
    return {column["name"] for column in sa.inspect(op.get_bind()).get_columns(table)}


def upgrade() -> None:
    # По умолчанию включено: ответ лида — главное событие сервиса, и человек,
    # который ничего не настраивал, должен о нём узнавать. Выключение —
    # осознанный шаг в настройках.
    if COLUMN not in _columns(TABLE):
        op.add_column(
            TABLE,
            sa.Column(
                COLUMN,
                sa.Boolean(),
                nullable=False,
                server_default=sa.true(),
            ),
        )


def downgrade() -> None:
    if COLUMN in _columns(TABLE):
        op.drop_column(TABLE, COLUMN)
