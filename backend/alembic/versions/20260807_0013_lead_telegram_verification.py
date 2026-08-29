"""Статус и источник Telegram-верификации лида.

revision: 0013
"""

from alembic import op
import sqlalchemy as sa

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def _columns(table: str) -> set[str]:
    return {column["name"] for column in sa.inspect(op.get_bind()).get_columns(table)}


def upgrade() -> None:
    columns = _columns("leads")
    additions = {
        "tg_id": sa.Column("tg_id", sa.String(length=40), server_default=""),
        "tg_status": sa.Column(
            "tg_status", sa.String(length=20), nullable=False, server_default="unknown"
        ),
        "tg_kind": sa.Column("tg_kind", sa.String(length=20), server_default=""),
        "tg_error": sa.Column("tg_error", sa.String(length=500), server_default=""),
        "tg_verified_by_account_id": sa.Column("tg_verified_by_account_id", sa.Integer()),
    }
    for name, column in additions.items():
        if name not in columns:
            op.add_column("leads", column)

    # Ранее найденный личный username считаем legacy-верифицированным: это
    # сохраняет поведение уже собранных баз. Новые записи проходят новый поток.
    op.execute(
        "UPDATE leads SET tg_status = 'verified' "
        "WHERE COALESCE(tg_username, '') <> '' AND tg_status = 'unknown'"
    )
    op.execute(
        "UPDATE leads SET tg_status = 'missing' "
        "WHERE COALESCE(tg_username, '') = '' AND COALESCE(telegram_channel, '') = '' "
        "AND tg_status = 'unknown'"
    )

    bind = op.get_bind()
    indexes = {index["name"] for index in sa.inspect(bind).get_indexes("leads")}
    if "ix_leads_tg_id" not in indexes:
        op.create_index("ix_leads_tg_id", "leads", ["tg_id"])
    if "ix_leads_tg_username" not in indexes:
        op.create_index("ix_leads_tg_username", "leads", ["tg_username"])
    if "ix_leads_tg_status" not in indexes:
        op.create_index("ix_leads_tg_status", "leads", ["tg_status"])


def downgrade() -> None:
    columns = _columns("leads")
    bind = op.get_bind()
    indexes = {index["name"] for index in sa.inspect(bind).get_indexes("leads")}
    for name in ("ix_leads_tg_status", "ix_leads_tg_username", "ix_leads_tg_id"):
        if name in indexes:
            op.drop_index(name, table_name="leads")
    for name in (
        "tg_verified_by_account_id", "tg_error", "tg_kind", "tg_status", "tg_id"
    ):
        if name in columns:
            op.drop_column("leads", name)
