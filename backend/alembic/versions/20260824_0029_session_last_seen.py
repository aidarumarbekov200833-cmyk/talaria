"""Последняя активность сеанса входа.

Revision ID: 0029
Revises: 0028
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0029"
down_revision = "0028"
branch_labels = None
depends_on = None


def _columns(table: str) -> set[str]:
    inspector = sa.inspect(op.get_bind())
    if table not in inspector.get_table_names():
        return set()
    return {column["name"] for column in inspector.get_columns(table)}


def upgrade() -> None:
    columns = _columns("user_sessions")
    if not columns or "last_seen_at" in columns:
        return
    # Без времени последней активности список сеансов бесполезен: человек видит
    # пять строк «вход 3 августа» и не может понять, какая из них — его телефон,
    # а какая чужая. Дата входа этого не показывает: войти можно один раз и
    # пользоваться месяц.
    op.add_column("user_sessions", sa.Column("last_seen_at", sa.DateTime(timezone=True)))


def downgrade() -> None:
    if "last_seen_at" in _columns("user_sessions"):
        op.drop_column("user_sessions", "last_seen_at")
