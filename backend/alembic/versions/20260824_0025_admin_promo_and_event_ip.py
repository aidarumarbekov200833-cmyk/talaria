"""Промокоды в базе и IP у событий журнала.

Revision ID: 0025
Revises: 0024
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0025"
down_revision = "0024"
branch_labels = None
depends_on = None

# Промокод, который до сих пор был зашит в код. Переносим его в таблицу, чтобы
# цена не изменилась ни для кого, кто им уже пользуется, а редактировать его
# стало можно из админки.
LEGACY_CODE = "ZEPHYR30"
LEGACY_DISCOUNT = 30


def _columns(table: str) -> set[str]:
    return {column["name"] for column in sa.inspect(op.get_bind()).get_columns(table)}


def _tables() -> set[str]:
    return set(sa.inspect(op.get_bind()).get_table_names())


def upgrade() -> None:
    if "ip" not in _columns("event_logs"):
        # Пусто для фоновых задач: у них нет запроса, а значит и адреса.
        op.add_column(
            "event_logs",
            sa.Column("ip", sa.String(length=64), nullable=False, server_default=""),
        )
        op.create_index("ix_event_logs_ip", "event_logs", ["ip"])

    if "promo_codes" not in _tables():
        op.create_table(
            "promo_codes",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("code", sa.String(length=40), nullable=False, unique=True, index=True),
            sa.Column("discount_pct", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("valid_until", sa.DateTime(timezone=True)),
            # 0 — без ограничения. Счётчик растёт, когда админ активирует тариф
            # и указывает, что покупка пришла по этому коду.
            sa.Column("max_uses", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("used_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("note", sa.Text(), nullable=False, server_default=""),
            sa.Column("created_by", sa.Integer()),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        )
        op.execute(
            sa.text(
                "INSERT INTO promo_codes (code, discount_pct, active, max_uses, used_count, note)"
                " VALUES (:code, :pct, true, 0, 0, :note)"
            ).bindparams(
                code=LEGACY_CODE,
                pct=LEGACY_DISCOUNT,
                note="Перенесён из кода при переходе на управляемые промокоды",
            )
        )


def downgrade() -> None:
    if "promo_codes" in _tables():
        op.drop_table("promo_codes")
    if "ip" in _columns("event_logs"):
        op.drop_index("ix_event_logs_ip", table_name="event_logs")
        op.drop_column("event_logs", "ip")
