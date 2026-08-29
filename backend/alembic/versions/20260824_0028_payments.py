"""Учёт полученных денег: таблица платежей.

Revision ID: 0028
Revises: 0027
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0028"
down_revision = "0027"
branch_labels = None
depends_on = None


def _tables() -> set[str]:
    return set(sa.inspect(op.get_bind()).get_table_names())


def upgrade() -> None:
    if "payments" in _tables():
        return
    # Отдельная таблица, а не колонка «сумма» в подписке. Подписка одна на
    # человека и хранит текущее состояние: записав сумму туда, мы видели бы
    # только последний платёж, а вопрос «сколько получено за месяц» остался бы
    # без ответа. История платежей нужна именно как история.
    op.create_table(
        "payments",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "owner_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        # Сумма в минорных единицах (копейки, тиын). Целым числом, а не
        # дробным: 0.1 + 0.2 в плавающей точке не равно 0.3, и на суммировании
        # выручки это вылезло бы расхождением в отчёте.
        sa.Column("amount_minor", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("currency", sa.String(length=8), nullable=False, server_default="RUB"),
        sa.Column("plan", sa.String(length=20), nullable=False, server_default=""),
        sa.Column("months", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("promo_code", sa.String(length=40), nullable=False, server_default=""),
        sa.Column("note", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_by", sa.Integer()),
        # Когда деньги реально получены. Отдельно от created_at: платёж можно
        # завести задним числом, и для отчёта важна дата оплаты, а не ввода.
        sa.Column("paid_at", sa.DateTime(timezone=True), nullable=False, index=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_payments_owner_paid", "payments", ["owner_id", "paid_at"])


def downgrade() -> None:
    if "payments" in _tables():
        op.drop_index("ix_payments_owner_paid", table_name="payments")
        op.drop_table("payments")
