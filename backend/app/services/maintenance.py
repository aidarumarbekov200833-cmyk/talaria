"""Ночная уборка таблиц, которые иначе растут вечно.

* token_blacklist — отозванные JWT. После `expires_at` запись бесполезна:
  токен и так не пройдёт проверку срока. Читается на каждый запрос, так что
  мусор здесь — лишняя работа индексу на каждом вызове API.
* login_attempts — журнал входов. Нужен для разбора инцидентов и поиска
  мультиаккаунтов, но не за всю жизнь проекта: держим LOGIN_ATTEMPTS_RETENTION_DAYS.

Одна функция, один вызов из фонового цикла в main.py. Без отдельного cron:
задача выполняется за миллисекунды и не заслуживает своего сервиса.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import delete
from sqlalchemy.orm import Session

from ..config import settings
from ..models import LoginAttempt, TokenBlacklist


def sweep_expired(db: Session, *, now: datetime | None = None) -> dict[str, int]:
    """Удалить протухшие записи. Возвращает счётчики по таблицам."""
    moment = now or datetime.now(UTC)
    retention = timedelta(days=max(1, settings.login_attempts_retention_days))
    try:
        revoked = db.execute(
            delete(TokenBlacklist).where(
                TokenBlacklist.expires_at.is_not(None), TokenBlacklist.expires_at < moment
            )
        ).rowcount
        attempts = db.execute(
            delete(LoginAttempt).where(LoginAttempt.created_at < moment - retention)
        ).rowcount
        db.commit()
    except Exception:
        db.rollback()
        raise
    return {"token_blacklist": int(revoked or 0), "login_attempts": int(attempts or 0)}
