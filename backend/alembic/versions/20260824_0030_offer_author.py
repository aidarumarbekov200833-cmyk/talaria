"""Кто создал оффер: человек или ИИ-помощник.

Revision ID: 0030
Revises: 0029
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0030"
down_revision = "0029"
branch_labels = None
depends_on = None


def _columns(table: str) -> set[str]:
    inspector = sa.inspect(op.get_bind())
    if table not in inspector.get_table_names():
        return set()
    return {column["name"] for column in inspector.get_columns(table)}


def upgrade() -> None:
    columns = _columns("offers")
    if not columns or "created_by" in columns:
        return
    # Помощник теперь сохраняет офферы сам, и отличить их от написанных руками
    # нужно по двум причинам: человек должен видеть, что текст не его, а
    # суточное ограничение на автосоздание нечем считать без этой отметки.
    op.add_column(
        "offers",
        sa.Column("created_by", sa.String(20), nullable=False, server_default="user"),
    )


def downgrade() -> None:
    if "created_by" in _columns("offers"):
        op.drop_column("offers", "created_by")
