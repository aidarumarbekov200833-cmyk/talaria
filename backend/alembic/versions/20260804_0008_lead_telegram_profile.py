"""Telegram profile fields on leads.

Плейсхолдеры {first_name}/{last_name} раньше были отключены: YouTube отдаёт
название канала, а не ФИО. Но пишем мы в Telegram, и там имя с фамилией есть.
Сохраняем разобранный профиль рядом с лидом, чтобы не ходить в MTProto на
каждый предпросмотр текста.

Revision ID: 0008
Revises: 0007
"""

import sqlalchemy as sa
from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("leads", sa.Column("tg_username", sa.String(length=64), nullable=True))
    op.add_column("leads", sa.Column("tg_first_name", sa.String(length=128), nullable=True))
    op.add_column("leads", sa.Column("tg_last_name", sa.String(length=128), nullable=True))
    op.add_column("leads", sa.Column("tg_checked_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("leads", "tg_checked_at")
    op.drop_column("leads", "tg_last_name")
    op.drop_column("leads", "tg_first_name")
    op.drop_column("leads", "tg_username")
