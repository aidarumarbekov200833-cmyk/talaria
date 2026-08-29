"""Clear unread autopause rows created before post-send baselining.

Revision ID: 0020
Revises: 0019
"""

from alembic import op


revision = "0020"
down_revision = "0019"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 0019 treated any unread delta as campaign risk, including the first real
    # dialog snapshot after account connection. The runtime rule is corrected
    # in 0020; remove only this synthetic status, preserving sessions and data.
    op.execute(
        "UPDATE warmup_accounts SET "
        "operational_status = 'ready', cooldown_until = NULL, "
        "last_tg_error = '', last_tg_error_code = '', unread_growth = 0, "
        "risk_score = CASE WHEN risk_score > 20 THEN 20 ELSE risk_score END "
        "WHERE last_tg_error_code = 'UNREAD_SPIKE'"
    )


def downgrade() -> None:
    # A false runtime state cannot be reconstructed from factual account data.
    pass
