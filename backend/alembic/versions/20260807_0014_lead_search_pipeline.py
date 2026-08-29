"""Persist lead-search filters and pipeline state.

Revision ID: 0014
Revises: 0013
"""

from alembic import op
import sqlalchemy as sa


revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "lead_databases",
        sa.Column("filters_json", sa.Text(), server_default="{}", nullable=False),
    )
    op.add_column(
        "lead_databases",
        sa.Column(
            "pipeline_status",
            sa.String(length=30),
            server_default="youtube_ready",
            nullable=False,
        ),
    )
    op.create_index(
        "ix_lead_databases_owner_pipeline",
        "lead_databases",
        ["owner_id", "pipeline_status"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_lead_databases_owner_pipeline", table_name="lead_databases")
    op.drop_column("lead_databases", "pipeline_status")
    op.drop_column("lead_databases", "filters_json")
