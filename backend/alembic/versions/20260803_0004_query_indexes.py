"""Составные индексы под реальные запросы списков.

Миграция только добавляет индексы — данные не трогаются, поведение приложения
не меняется. Каждый индекс соответствует конкретному запросу в коде:

* ix_campaign_logs_campaign_ts — GET /api/campaigns/{id}/logs
  (фильтр campaign_id + ORDER BY timestamp DESC + пагинация).
* ix_leads_database_status — GET /api/leads с фильтром по статусу внутри базы.
* ix_event_logs_owner_created — лента событий владельца, новые сверху.

Одиночные индексы по campaign_id, database_id, owner_id и created_at намеренно
НЕ удаляются: они используются другими запросами (проверка владения,
каскадное удаление, внешние ключи), а экономия от их удаления не стоит риска.

Revision ID: 0004
Revises: 0003
"""

from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "ix_campaign_logs_campaign_ts",
        "campaign_logs",
        ["campaign_id", "timestamp"],
    )
    op.create_index(
        "ix_leads_database_status",
        "leads",
        ["database_id", "status"],
    )
    op.create_index(
        "ix_event_logs_owner_created",
        "event_logs",
        ["owner_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_event_logs_owner_created", table_name="event_logs")
    op.drop_index("ix_leads_database_status", table_name="leads")
    op.drop_index("ix_campaign_logs_campaign_ts", table_name="campaign_logs")