"""Переписка с поддержкой внутри сервиса.

Revision ID: 0035
Revises: 0034

Владелец попросил, чтобы «поддержка — то есть именно мне сообщение» жила в
самом кабинете, а не в отдельном чате Telegram: человек пишет из аккаунт-меню,
владелец отвечает из админки, а уведомление о новом обращении приходит боту.

Одна таблица на весь диалог: `author` различает, кто написал, `read_at` — момент
прочтения второй стороной. Составной индекс (owner_id, created_at) закрывает и
чтение переписки одного клиента, и список обращений в админке.

Понижение удаляет таблицу вместе с историей обращений.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0035"
down_revision = "0034"
branch_labels = None
depends_on = None

TABLE = "support_messages"


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
        sa.Column("author", sa.String(length=10), nullable=False, server_default="user"),
        sa.Column("admin_id", sa.Integer(), nullable=True),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("read_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_support_messages_owner_id", TABLE, ["owner_id"])
    op.create_index("ix_support_messages_created_at", TABLE, ["created_at"])
    op.create_index("ix_support_messages_owner_created", TABLE, ["owner_id", "created_at"])


def downgrade() -> None:
    if TABLE not in _tables():
        return
    op.drop_index("ix_support_messages_owner_created", table_name=TABLE)
    op.drop_index("ix_support_messages_created_at", table_name=TABLE)
    op.drop_index("ix_support_messages_owner_id", table_name=TABLE)
    op.drop_table(TABLE)
