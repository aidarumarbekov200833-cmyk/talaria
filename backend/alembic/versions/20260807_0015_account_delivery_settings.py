"""Per-account verification and delivery controls.

Revision ID: 0015
Revises: 0014
"""

from alembic import op
import sqlalchemy as sa


revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("warmup_accounts", sa.Column("daily_send_limit", sa.Integer(), server_default="30", nullable=False))
    op.add_column("warmup_accounts", sa.Column("delay_min_seconds", sa.Integer(), server_default="20", nullable=False))
    op.add_column("warmup_accounts", sa.Column("delay_max_seconds", sa.Integer(), server_default="60", nullable=False))
    op.add_column("warmup_accounts", sa.Column("sending_enabled", sa.Boolean(), server_default=sa.true(), nullable=False))
    op.add_column("warmup_accounts", sa.Column("verification_enabled", sa.Boolean(), server_default=sa.true(), nullable=False))


def downgrade() -> None:
    op.drop_column("warmup_accounts", "verification_enabled")
    op.drop_column("warmup_accounts", "sending_enabled")
    op.drop_column("warmup_accounts", "delay_max_seconds")
    op.drop_column("warmup_accounts", "delay_min_seconds")
    op.drop_column("warmup_accounts", "daily_send_limit")
