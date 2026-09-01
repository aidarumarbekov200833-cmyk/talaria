"""История отправок переживает удаление кампании.

campaign_sends — единственный источник дедупликации skip_contacted: «этому
человеку уже писали». Удаление кампании стирало её строки каскадом, и те же
лиды получали повторное первое сообщение в следующей рассылке — жалоба и бан
аккаунта. Теперь campaign_id допускает NULL: при удалении кампании строки
отвязываются, а не удаляются, и факт «уже писали» сохраняется навсегда.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0039"
down_revision = "0038"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # batch_alter_table: SQLite не умеет ALTER COLUMN, alembic пересоздаёт
    # таблицу. На PostgreSQL это обычный ALTER.
    with op.batch_alter_table("campaign_sends") as batch:
        batch.alter_column(
            "campaign_id",
            existing_type=sa.Integer(),
            nullable=True,
        )


def downgrade() -> None:
    # Отвязанные строки не могут получить NOT NULL обратно — их кампаний
    # больше нет. Удаляем осознанно: это возврат к старому поведению,
    # при котором история этих кампаний и так была бы стёрта.
    op.execute("DELETE FROM campaign_sends WHERE campaign_id IS NULL")
    with op.batch_alter_table("campaign_sends") as batch:
        batch.alter_column(
            "campaign_id",
            existing_type=sa.Integer(),
            nullable=False,
        )
