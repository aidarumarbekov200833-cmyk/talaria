"""Persistent continuous Telegram verification jobs.

Revision ID: 0016
Revises: 0015
"""

from alembic import op
import sqlalchemy as sa


revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("leads", sa.Column("tg_verification_job_id", sa.Integer(), nullable=True))
    op.create_index(
        "ix_leads_tg_verification_job_id",
        "leads",
        ["tg_verification_job_id"],
        unique=False,
    )
    op.create_table(
        "lead_verification_jobs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("owner_id", sa.Integer(), nullable=False),
        sa.Column("database_id", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=30), server_default="queued", nullable=False),
        sa.Column("retry_errors", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("total", sa.Integer(), server_default="0", nullable=False),
        sa.Column("processed", sa.Integer(), server_default="0", nullable=False),
        sa.Column("verified", sa.Integer(), server_default="0", nullable=False),
        sa.Column("unresolved", sa.Integer(), server_default="0", nullable=False),
        sa.Column("errors", sa.Integer(), server_default="0", nullable=False),
        sa.Column("current_lead_id", sa.Integer(), nullable=True),
        sa.Column("current_title", sa.String(length=300), nullable=True),
        sa.Column("last_result", sa.String(length=500), nullable=True),
        sa.Column("last_error", sa.String(length=1000), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.ForeignKeyConstraint(["database_id"], ["lead_databases.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_lead_verification_owner_status",
        "lead_verification_jobs",
        ["owner_id", "status"],
        unique=False,
    )
    op.create_index(
        "ix_lead_verification_database_status",
        "lead_verification_jobs",
        ["database_id", "status"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_lead_verification_database_status",
        table_name="lead_verification_jobs",
    )
    op.drop_index(
        "ix_lead_verification_owner_status",
        table_name="lead_verification_jobs",
    )
    op.drop_table("lead_verification_jobs")
    op.drop_index("ix_leads_tg_verification_job_id", table_name="leads")
    op.drop_column("leads", "tg_verification_job_id")
