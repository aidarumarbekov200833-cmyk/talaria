"""Индекс под запрос дедупликации «кому уже писали».

Запрос skip_contacted фильтрует campaign_sends по (owner_id, status) и
выбирает lead_id — на каждый запуск рассылки и каждый предпросмотр.
Существующие индексы не подходят: ix_campaign_sends_owner_lead без status,
а ix_campaign_sends_owner_account_status_sent_at держит account_id раньше
status. После миграции 0039 таблица бессмертна (строки переживают удаление
кампаний), то есть только растёт — без индекса запрос дорожал бы с каждой
завершённой рассылкой.
"""

from __future__ import annotations

from alembic import op

revision = "0040"
down_revision = "0039"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "ix_campaign_sends_owner_status_lead",
        "campaign_sends",
        ["owner_id", "status", "lead_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_campaign_sends_owner_status_lead", table_name="campaign_sends")
