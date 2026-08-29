"""Заметка у лида и состояние уведомлений (тариф, квота).

Revision ID: 0026
Revises: 0025
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0026"
down_revision = "0025"
branch_labels = None
depends_on = None


def _columns(table: str) -> set[str]:
    return {column["name"] for column in sa.inspect(op.get_bind()).get_columns(table)}


def upgrade() -> None:
    if "note" not in _columns("leads"):
        op.add_column(
            "leads",
            sa.Column("note", sa.Text(), nullable=False, server_default=""),
        )

    settings_columns = _columns("app_settings")
    if "expiry_notice_stage" not in settings_columns:
        # Какое напоминание об окончании тарифа уже отправлено: 0 — ни одного,
        # 3 — «осталось три дня», 1 — «остался день». Числом, а не флагами:
        # этапы идут по убыванию и сравниваются одним «меньше».
        op.add_column(
            "app_settings",
            sa.Column("expiry_notice_stage", sa.Integer(), nullable=False, server_default="0"),
        )
    if "expiry_notice_for" not in settings_columns:
        # К какой именно дате окончания относится отправленное напоминание.
        # Без неё продление тарифа не сбрасывало бы этап, и о следующем
        # окончании человек не узнал бы вообще.
        op.add_column(
            "app_settings",
            sa.Column("expiry_notice_for", sa.DateTime(timezone=True)),
        )

    if "notified_pct" not in _columns("quota_usage"):
        # Порог расхода квоты, о котором уже сообщили в этот квотный день.
        # Хранится рядом с расходом: квота сбрасывается по тихоокеанской
        # полуночи вместе со строкой, и отдельная чистка не нужна.
        op.add_column(
            "quota_usage",
            sa.Column("notified_pct", sa.Integer(), nullable=False, server_default="0"),
        )


def downgrade() -> None:
    if "notified_pct" in _columns("quota_usage"):
        op.drop_column("quota_usage", "notified_pct")
    settings_columns = _columns("app_settings")
    if "expiry_notice_for" in settings_columns:
        op.drop_column("app_settings", "expiry_notice_for")
    if "expiry_notice_stage" in settings_columns:
        op.drop_column("app_settings", "expiry_notice_stage")
    if "note" in _columns("leads"):
        op.drop_column("leads", "note")
