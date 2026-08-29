"""Count the Telegram login profile as the owner's primary account.

Revision ID: 0017
Revises: 0016
"""

from alembic import op
import sqlalchemy as sa


revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "warmup_accounts",
        sa.Column("is_primary", sa.Boolean(), server_default=sa.false(), nullable=False),
    )
    op.create_index(
        "uq_warmup_primary_owner",
        "warmup_accounts",
        ["owner_id"],
        unique=True,
        postgresql_where=sa.text("is_primary = true"),
        sqlite_where=sa.text("is_primary = 1"),
    )


def downgrade() -> None:
    op.drop_index("uq_warmup_primary_owner", table_name="warmup_accounts")
    op.drop_column("warmup_accounts", "is_primary")
