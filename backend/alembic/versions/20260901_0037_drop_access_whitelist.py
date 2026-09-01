"""Убрать белый список доступа.

Пробный тариф выдаётся каждому при первом входе, поэтому «список допущенных»
стал вторым, ручным способом сделать то же самое. Рубильник открытой
регистрации остаётся: закрыл — новые не входят, старые продолжают работать.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0037"
down_revision = "0036"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_table("access_whitelist")


def downgrade() -> None:
    op.create_table(
        "access_whitelist",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("telegram_id", sa.BigInteger(), unique=True, index=True),
        sa.Column("username", sa.String(length=64), index=True),
        sa.Column("note", sa.Text()),
        sa.Column("added_by", sa.Integer()),
        sa.Column("created_at", sa.DateTime(timezone=True)),
    )
