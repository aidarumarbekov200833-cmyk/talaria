"""Главный аккаунт для уведомлений.

Revision ID: 0024
Revises: 0023
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0024"
down_revision = "0023"
branch_labels = None
depends_on = None

TABLE = "app_settings"
TARGET = "notify_target_tg_id"
CHECKED = "notify_target_ok"


def _columns(table: str) -> set[str]:
    return {column["name"] for column in sa.inspect(op.get_bind()).get_columns(table)}


def upgrade() -> None:
    existing = _columns(TABLE)
    if TARGET not in existing:
        # NULL — «аккаунт, которым вошли». Так у всех, кто ничего не выбирал,
        # поведение остаётся прежним, и нам не нужно проставлять им telegram_id
        # задним числом (он может смениться при повторной привязке).
        op.add_column(TABLE, sa.Column(TARGET, sa.BigInteger(), nullable=True))
    if CHECKED not in existing:
        # NULL — «не проверяли». Отличать это от «проверили, не доходит» важно:
        # бот не может писать первым, и молчание из-за 403 иначе выглядит как
        # исправная работа.
        op.add_column(TABLE, sa.Column(CHECKED, sa.Boolean(), nullable=True))


def downgrade() -> None:
    existing = _columns(TABLE)
    if CHECKED in existing:
        op.drop_column(TABLE, CHECKED)
    if TARGET in existing:
        op.drop_column(TABLE, TARGET)
