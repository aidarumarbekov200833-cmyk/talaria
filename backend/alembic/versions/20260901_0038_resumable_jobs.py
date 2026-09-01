"""Возобновляемые поиск и рассылка после рестарта.

Рассылка: campaigns.lead_ids хранит полный список получателей запуска — после
рестарта остаток вычисляется вычитанием campaign_sends, и отправка продолжается
сама. ai_materials хранится при кампании, чтобы возобновление шло с теми же
настройками ИИ-оффера.

Поиск: таблица search_jobs — прогон живёт в БД, а не в HTTP-запросе. Прогресс
(найденные каналы, пройденные слова) коммитится по ходу, рестарт продолжает с
первого непройденного слова.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0038"
down_revision = "0037"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "campaigns",
        sa.Column("lead_ids", sa.Text(), nullable=False, server_default="[]"),
    )
    op.add_column(
        "campaigns",
        sa.Column("ai_materials", sa.Integer(), nullable=False, server_default="5"),
    )
    op.create_table(
        "search_jobs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "owner_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("status", sa.String(length=30), nullable=False, server_default="queued"),
        sa.Column("payload", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("keywords_done", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("found_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("current_keyword", sa.String(length=200)),
        sa.Column("stop_reason", sa.String(length=500)),
        sa.Column("error", sa.String(length=1000)),
        sa.Column(
            "database_id",
            sa.Integer(),
            sa.ForeignKey("lead_databases.id", ondelete="SET NULL"),
        ),
        sa.Column("leads_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("limit_total", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("per_keyword", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_search_jobs_owner_status", "search_jobs", ["owner_id", "status"])


def downgrade() -> None:
    op.drop_index("ix_search_jobs_owner_status", table_name="search_jobs")
    op.drop_table("search_jobs")
    op.drop_column("campaigns", "ai_materials")
    op.drop_column("campaigns", "lead_ids")
