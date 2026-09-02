"""Ночная уборка: token_blacklist и login_attempts не растут вечно."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.models import LoginAttempt, TokenBlacklist
from app.services.maintenance import sweep_expired


def test_sweep_removes_only_expired(db):
    now = datetime.now(UTC)
    db.add_all(
        [
            TokenBlacklist(jti="old", expires_at=now - timedelta(hours=1)),
            TokenBlacklist(jti="live", expires_at=now + timedelta(days=1)),
            TokenBlacklist(jti="unknown", expires_at=None),
            LoginAttempt(telegram_id=1, created_at=now - timedelta(days=200)),
            LoginAttempt(telegram_id=2, created_at=now - timedelta(days=2)),
        ]
    )
    db.commit()
    removed = sweep_expired(db, now=now)
    assert removed == {"token_blacklist": 1, "login_attempts": 1}
    assert {r.jti for r in db.query(TokenBlacklist)} == {"live", "unknown"}
    assert [r.telegram_id for r in db.query(LoginAttempt)] == [2]
    assert sweep_expired(db, now=now) == {"token_blacklist": 0, "login_attempts": 0}
