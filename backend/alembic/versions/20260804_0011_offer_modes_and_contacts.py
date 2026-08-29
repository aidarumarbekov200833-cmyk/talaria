"""Волна 14: типы офферов, кросс-контакты, ответы лидов

Что меняется и почему:

* campaigns — рассылка теперы запускается сразу и бывает двух типов (обычные
  офферы со спинтаксом или AI-оффер по шаблону). Запуск по расписанию убран,
  вместе с ним уходит колонка scheduled_at и её индекс.
* campaign_sends — без offer_id/offer_kind невозможна статистика «какой оффер
  даёт ответы», а без replied_at — сам факт ответа.
* leads — кросс-контакты: нашли YouTube-канал → достроили Telegram.

Почему удаление scheduled_at сделано аккуратно (через inspector): на SQLite
удаление колонки и индекса, которого нет, роняет миграцию, а базы у нас
бывают разного возраста (тесты разворачивают схему из models без миграций).

revision: 0011
"""

from alembic import op
import sqlalchemy as sa

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def _columns(table: str) -> set[str]:
    bind = op.get_bind()
    return {c["name"] for c in sa.inspect(bind).get_columns(table)}


def _indexes(table: str) -> set[str]:
    bind = op.get_bind()
    return {i["name"] for i in sa.inspect(bind).get_indexes(table)}


def upgrade() -> None:
    # ------------------------------- campaigns -------------------------------
    cols = _columns("campaigns")
    if "offer_mode" not in cols:
        op.add_column(
            "campaigns",
            sa.Column(
                "offer_mode",
                sa.String(length=20),
                nullable=False,
                server_default="regular",
            ),
        )
    if "ai_template" not in cols:
        op.add_column("campaigns", sa.Column("ai_template", sa.Text(), server_default=""))
    if "spintax" not in cols:
        op.add_column(
            "campaigns",
            sa.Column("spintax", sa.Boolean(), nullable=False, server_default="0"),
        )
    if "account_ids" not in cols:
        op.add_column(
            "campaigns", sa.Column("account_ids", sa.Text(), server_default="[]")
        )
    if "reply_count" not in cols:
        op.add_column(
            "campaigns",
            sa.Column("reply_count", sa.Integer(), nullable=False, server_default="0"),
        )

    # Запуск по расписанию больше не нужен. Сначала переводим отложенные
    # кампании в завершённые: иначе они останутся висеть в статусе
    # "scheduled" навсегда — тот, кто их запускал, уже удалён.
    op.execute(
        "UPDATE campaigns SET status = 'completed' WHERE status = 'scheduled'"
    )
    if "ix_campaigns_scheduled_at" in _indexes("campaigns"):
        op.drop_index("ix_campaigns_scheduled_at", table_name="campaigns")
    if "scheduled_at" in _columns("campaigns"):
        op.drop_column("campaigns", "scheduled_at")

    # ----------------------------- campaign_sends ----------------------------
    send_cols = _columns("campaign_sends")
    if "offer_id" not in send_cols:
        op.add_column("campaign_sends", sa.Column("offer_id", sa.Integer()))
        op.create_index(
            "ix_campaign_sends_offer_id", "campaign_sends", ["offer_id"], unique=False
        )
    if "offer_kind" not in send_cols:
        op.add_column(
            "campaign_sends",
            sa.Column(
                "offer_kind",
                sa.String(length=20),
                nullable=False,
                server_default="regular",
            ),
        )
    if "rendered_text" not in send_cols:
        op.add_column(
            "campaign_sends", sa.Column("rendered_text", sa.Text(), server_default="")
        )
    if "replied_at" not in send_cols:
        op.add_column(
            "campaign_sends", sa.Column("replied_at", sa.DateTime(timezone=True))
        )
    if "account_id" not in send_cols:
        op.add_column("campaign_sends", sa.Column("account_id", sa.Integer()))
        op.create_index(
            "ix_campaign_sends_account_id",
            "campaign_sends",
            ["account_id"],
            unique=False,
        )

    # --------------------------------- leads ---------------------------------
    lead_cols = _columns("leads")
    if "telegram_channel" not in lead_cols:
        op.add_column("leads", sa.Column("telegram_channel", sa.String(length=64), server_default=""))
    if "telegram_url" not in lead_cols:
        op.add_column("leads", sa.Column("telegram_url", sa.String(length=256), server_default=""))
    if "contact_email" not in lead_cols:
        op.add_column("leads", sa.Column("contact_email", sa.String(length=200), server_default=""))
    if "contacts_checked_at" not in lead_cols:
        op.add_column("leads", sa.Column("contacts_checked_at", sa.DateTime(timezone=True)))


def downgrade() -> None:
    for col in ("contacts_checked_at", "contact_email", "telegram_url", "telegram_channel"):
        if col in _columns("leads"):
            op.drop_column("leads", col)

    send_indexes = _indexes("campaign_sends")
    if "ix_campaign_sends_account_id" in send_indexes:
        op.drop_index("ix_campaign_sends_account_id", table_name="campaign_sends")
    if "ix_campaign_sends_offer_id" in send_indexes:
        op.drop_index("ix_campaign_sends_offer_id", table_name="campaign_sends")
    for col in ("account_id", "replied_at", "rendered_text", "offer_kind", "offer_id"):
        if col in _columns("campaign_sends"):
            op.drop_column("campaign_sends", col)

    if "scheduled_at" not in _columns("campaigns"):
        op.add_column("campaigns", sa.Column("scheduled_at", sa.DateTime(timezone=True)))
        op.create_index(
            "ix_campaigns_scheduled_at", "campaigns", ["scheduled_at"], unique=False
        )
    for col in ("reply_count", "account_ids", "spintax", "ai_template", "offer_mode"):
        if col in _columns("campaigns"):
            op.drop_column("campaigns", col)
