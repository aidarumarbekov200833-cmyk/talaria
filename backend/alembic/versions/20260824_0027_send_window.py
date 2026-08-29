"""Окно отправки по местному времени получателя.

Revision ID: 0027
Revises: 0026
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0027"
down_revision = "0026"
branch_labels = None
depends_on = None


def _columns(table: str) -> set[str]:
    return {column["name"] for column in sa.inspect(op.get_bind()).get_columns(table)}


def upgrade() -> None:
    columns = _columns("app_settings")
    # Включено по умолчанию и для тех, кто уже пользуется сервисом: ночная
    # рассылка вредит именно им, а не новичкам, и «не менять поведение молча»
    # здесь означало бы оставить действующие аккаунты под риском жалоб.
    if "send_window_enabled" not in columns:
        op.add_column(
            "app_settings",
            sa.Column(
                "send_window_enabled",
                sa.Boolean(),
                nullable=False,
                server_default=sa.true(),
            ),
        )
    if "send_window_start" not in columns:
        op.add_column(
            "app_settings",
            sa.Column("send_window_start", sa.Integer(), nullable=False, server_default="10"),
        )
    if "send_window_end" not in columns:
        op.add_column(
            "app_settings",
            sa.Column("send_window_end", sa.Integer(), nullable=False, server_default="20"),
        )


def downgrade() -> None:
    columns = _columns("app_settings")
    for name in ("send_window_end", "send_window_start", "send_window_enabled"):
        if name in columns:
            op.drop_column("app_settings", name)
