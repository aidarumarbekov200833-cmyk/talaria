"""Таблица отправок campaign_sends — дедупликация рассылки.

Миграция только создаёт новую таблицу, старые данные не трогает. Бэкфилла нет
сознательно: в старых данных нет информации о том, кому именно отправляли —
есть только счётчик campaigns.sent_count. Восстанавливать из него список лидов
значило бы выдумать данные, а ошибка здесь стоит дорого: либо повторное письмо
человеку, либо молчаливо пропущенный лид, которому никогда не писали.
Дедупликация честно начинает вести историю с момента установки обновления.

Revision ID: 0006
Revises: 0005
"""

import sqlalchemy as sa
from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "campaign_sends",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("owner_id", sa.Integer(), nullable=False),
        sa.Column("campaign_id", sa.Integer(), nullable=False),
        sa.Column("lead_id", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="sent"),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column(
            "sent_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=True,
        ),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["campaign_id"], ["campaigns.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["lead_id"], ["leads.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("campaign_id", "lead_id", name="uq_campaign_send_lead"),
    )
    op.create_index(
        "ix_campaign_sends_owner_id", "campaign_sends", ["owner_id"]
    )
    op.create_index(
        "ix_campaign_sends_campaign_id", "campaign_sends", ["campaign_id"]
    )
    op.create_index(
        "ix_campaign_sends_lead_id", "campaign_sends", ["lead_id"]
    )
    op.create_index(
        "ix_campaign_sends_owner_lead", "campaign_sends", ["owner_id", "lead_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_campaign_sends_owner_lead", table_name="campaign_sends")
    op.drop_index("ix_campaign_sends_lead_id", table_name="campaign_sends")
    op.drop_index("ix_campaign_sends_campaign_id", table_name="campaign_sends")
    op.drop_index("ix_campaign_sends_owner_id", table_name="campaign_sends")
    op.drop_table("campaign_sends")
