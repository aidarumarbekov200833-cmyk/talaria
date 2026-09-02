"""Ключи YouTube Data API: колонка под шифртекст.

До этого `api_keys.key` был VARCHAR(200) с открытым ключом. Теперь приложение
пишет туда `enc1:<fernet-token>` (см. app/secretbox.py) — это ~150 символов
для обычного ключа Google, и VARCHAR(200) впритык. Расширяем до TEXT.

Сами данные здесь не перешифровываются: миграции не должны зависеть от
DATA_ENCRYPTION_KEY из окружения приложения. Перешифровка старых открытых
строк идёт при старте бэкенда (services.api_keys.reencrypt_legacy) и
идемпотентна — открытый текст читается как есть, пока не перезаписан.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0041"
down_revision = "0040"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.alter_column(
            "api_keys",
            "key",
            existing_type=sa.String(length=200),
            type_=sa.Text(),
            existing_nullable=False,
        )
    # SQLite (тесты/локалка): VARCHAR и TEXT — одна affinity, длина не
    # проверяется, менять нечего.


def downgrade() -> None:
    # Обратно в VARCHAR(200) не сжимаем: зашифрованные значения длиннее и
    # ALTER упал бы на данных. Откат кода читает TEXT без проблем.
    pass
