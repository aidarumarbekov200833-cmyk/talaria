"""Кампании: колонка scheduled_at для автозапуска планировщиком.

Revision ID: 0003
Revises: 0002
Create Date: 2026-08-03

До этой ревизии статус "scheduled" был фикцией: кампания создавалась в нём и
висела там, пока человек вручную не переводил её в "running" — хранить
время запуска было негде. Колонка закрывает этот пробел.

nullable=True сознательно: NULL означает «запускается вручную». Благодаря этому
уже существующие строки не меняют поведение: планировщик явно фильтрует
`scheduled_at IS NOT NULL`, то есть старые кампании он сам не трогает.

Индекс нужен потому, что фоновый цикл опрашивает таблицу раз в минуту именно по
этой колонке; без индекса это seq scan каждые 60 секунд.

Имя индекса — ix_<table>_<column>, как и во всех предыдущих ревизиях.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "0003"
down_revision: Union[str, None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "campaigns",
        sa.Column("scheduled_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_campaigns_scheduled_at", "campaigns", ["scheduled_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_campaigns_scheduled_at", table_name="campaigns")
    op.drop_column("campaigns", "scheduled_at")
