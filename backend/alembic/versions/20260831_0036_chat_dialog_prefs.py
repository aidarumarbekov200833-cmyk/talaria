"""Зеркало «без звука» для диалогов переписки.

Revision ID: 0036
Revises: 0035

Чат в кабинете получил закрепление и mute — оба действия уходят в настоящий
Telegram через шлюз. Но у Talaria есть и своё уведомление «лид ответил» в
личку бота, и оно обязано уважать mute. Ходить за настройкой в Telegram при
каждом ответе лида дорого, поэтому факт mute зеркалится в свою таблицу.

Храним только замьюченные диалоги: снятие mute удаляет строку. Понижение
удаляет таблицу — потеряется лишь зеркало, сам mute в Telegram останется.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0036"
down_revision = "0035"
branch_labels = None
depends_on = None

TABLE = "chat_dialog_prefs"


def _tables() -> set[str]:
    return set(sa.inspect(op.get_bind()).get_table_names())


def upgrade() -> None:
    if TABLE in _tables():
        return
    op.create_table(
        TABLE,
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "owner_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("account_id", sa.Integer(), nullable=False),
        sa.Column("peer_id", sa.String(length=32), nullable=False),
        sa.Column("muted", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_chat_dialog_prefs_owner_id", TABLE, ["owner_id"])
    op.create_index(
        "ix_chat_dialog_prefs_owner_account_peer",
        TABLE,
        ["owner_id", "account_id", "peer_id"],
        unique=True,
    )


def downgrade() -> None:
    if TABLE not in _tables():
        return
    op.drop_table(TABLE)
