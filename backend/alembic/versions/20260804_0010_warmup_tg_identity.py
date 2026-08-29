"""warmup_accounts: кто именно подключён в веб-Telegram

Прогрев перестал быть ручным списком: карточка аккаунта в настройках — это тот
же аккаунт, который подключён во встроенном веб-Telegram. Ник, телефон и id
заполняет сам tg-mini-client после входа, поэтому вводить их руками не нужно.

revision: 0010
"""

from alembic import op
import sqlalchemy as sa

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("warmup_accounts", sa.Column("tg_id", sa.String(length=40)))
    op.add_column("warmup_accounts", sa.Column("tg_username", sa.String(length=64)))
    op.add_column("warmup_accounts", sa.Column("tg_phone", sa.String(length=40)))
    op.add_column(
        "warmup_accounts",
        sa.Column("connected_at", sa.DateTime(timezone=True)),
    )
    op.create_index(
        "ix_warmup_accounts_tg_id", "warmup_accounts", ["tg_id"], unique=False
    )


def downgrade() -> None:
    op.drop_index("ix_warmup_accounts_tg_id", table_name="warmup_accounts")
    op.drop_column("warmup_accounts", "connected_at")
    op.drop_column("warmup_accounts", "tg_phone")
    op.drop_column("warmup_accounts", "tg_username")
    op.drop_column("warmup_accounts", "tg_id")
