"""Нейрокомментинг: площадки, прогоны и журнал комментариев.

Revision ID: 0032
Revises: 0031

Три таблицы вместо одной, потому что у них разное время жизни: площадка живёт
месяцами, настройка прогона одна на пользователя, а журнал комментариев растёт
на каждое действие и по нему же считается дневной расход аккаунта.

Индекс (account_id, created_at) в neuro_comments — не украшение: по нему на
каждый комментарий считается общий дневной котёл аккаунта вместе с личными
отправками, и без индекса это превратится в полный проход по журналу.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0032"
down_revision = "0031"
branch_labels = None
depends_on = None


def _tables() -> set[str]:
    return set(sa.inspect(op.get_bind()).get_table_names())


def upgrade() -> None:
    tables = _tables()

    if "neuro_targets" not in tables:
        op.create_table(
            "neuro_targets",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column(
                "owner_id",
                sa.Integer(),
                sa.ForeignKey("users.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("username", sa.String(64), nullable=False),
            sa.Column("tg_id", sa.String(40), nullable=False, server_default=""),
            sa.Column("title", sa.String(300), nullable=False, server_default=""),
            sa.Column("kind", sa.String(20), nullable=False, server_default="channel"),
            sa.Column("members", sa.Integer()),
            sa.Column("topic", sa.String(200), nullable=False, server_default=""),
            sa.Column("source", sa.String(20), nullable=False, server_default="manual"),
            sa.Column("status", sa.String(20), nullable=False, server_default="candidate"),
            sa.Column("joined", sa.Boolean(), nullable=False, server_default="0"),
            sa.Column("joined_at", sa.DateTime(timezone=True)),
            sa.Column("slowmode_seconds", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("linked_chat_id", sa.String(40), nullable=False, server_default=""),
            sa.Column("last_post_id", sa.Integer()),
            sa.Column("last_comment_at", sa.DateTime(timezone=True)),
            sa.Column("comments_sent", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("last_error", sa.String(500), nullable=False, server_default=""),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
            sa.UniqueConstraint("owner_id", "username", name="uq_neuro_target_owner_username"),
        )
        op.create_index("ix_neuro_targets_owner_id", "neuro_targets", ["owner_id"])
        op.create_index(
            "ix_neuro_targets_owner_status", "neuro_targets", ["owner_id", "status"]
        )

    if "neuro_runs" not in tables:
        op.create_table(
            "neuro_runs",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column(
                "owner_id",
                sa.Integer(),
                sa.ForeignKey("users.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("name", sa.String(200), nullable=False, server_default="Нейрокомментинг"),
            sa.Column("mode", sa.String(20), nullable=False, server_default="targets"),
            sa.Column("topic", sa.String(200), nullable=False, server_default=""),
            sa.Column("account_id", sa.Integer()),
            sa.Column("persona", sa.Text(), nullable=False, server_default=""),
            sa.Column("link", sa.String(256), nullable=False, server_default=""),
            sa.Column("daily_limit", sa.Integer(), nullable=False, server_default="8"),
            sa.Column("status", sa.String(20), nullable=False, server_default="idle"),
            sa.Column("last_started_at", sa.DateTime(timezone=True)),
            sa.Column("last_finished_at", sa.DateTime(timezone=True)),
            sa.Column("last_note", sa.String(500), nullable=False, server_default=""),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        )
        op.create_index("ix_neuro_runs_owner_id", "neuro_runs", ["owner_id"])
        op.create_index("ix_neuro_runs_account_id", "neuro_runs", ["account_id"])

    if "neuro_comments" not in tables:
        op.create_table(
            "neuro_comments",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column(
                "owner_id",
                sa.Integer(),
                sa.ForeignKey("users.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("run_id", sa.Integer()),
            sa.Column("target_id", sa.Integer()),
            sa.Column("account_id", sa.Integer(), nullable=False),
            sa.Column("post_id", sa.Integer()),
            sa.Column("message_id", sa.Integer()),
            sa.Column("text", sa.Text(), nullable=False, server_default=""),
            sa.Column("status", sa.String(20), nullable=False, server_default="sent"),
            sa.Column("error", sa.String(500), nullable=False, server_default=""),
            sa.Column("error_code", sa.String(64), nullable=False, server_default=""),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        )
        op.create_index("ix_neuro_comments_owner_id", "neuro_comments", ["owner_id"])
        op.create_index("ix_neuro_comments_run_id", "neuro_comments", ["run_id"])
        op.create_index("ix_neuro_comments_target_id", "neuro_comments", ["target_id"])
        op.create_index("ix_neuro_comments_account_id", "neuro_comments", ["account_id"])
        op.create_index("ix_neuro_comments_created_at", "neuro_comments", ["created_at"])
        op.create_index(
            "ix_neuro_comments_account_created",
            "neuro_comments",
            ["account_id", "created_at"],
        )
        op.create_index(
            "ix_neuro_comments_owner_created", "neuro_comments", ["owner_id", "created_at"]
        )


def downgrade() -> None:
    tables = _tables()
    for name in ("neuro_comments", "neuro_runs", "neuro_targets"):
        if name in tables:
            op.drop_table(name)
