"""Persistent Telegram account quarantine and risk accounting."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session

from .models import WarmupAccount

ACCOUNT_LIMIT_CODES = {
    "FLOOD_WAIT",
    "FLOOD_PREMIUM_WAIT",
    "PEER_FLOOD",
    "PHONE_NUMBER_BANNED",
    "USER_DEACTIVATED_BAN",
    "AUTH_KEY_UNREGISTERED",
    "AUTH_KEY_DUPLICATED",
}
RECIPIENT_CODES = {
    "USER_PRIVACY_RESTRICTED",
    "USER_IS_BLOCKED",
    "CHAT_WRITE_FORBIDDEN",
    "USERNAME_NOT_OCCUPIED",
    "USERNAME_INVALID",
}


@dataclass(frozen=True)
class AccountDecision:
    quarantine: bool
    status: str
    risk: int
    cooldown_until: datetime | None = None


def utcnow() -> datetime:
    return datetime.now(UTC)


def normalized_code(code: str | None, error: str = "") -> str:
    value = (code or "").strip().upper()
    if value:
        return value
    raw = (error or "").upper()
    for candidate in (*ACCOUNT_LIMIT_CODES, *RECIPIENT_CODES):
        if candidate in raw:
            return candidate
    if "FLOOD_WAIT_" in raw:
        return "FLOOD_WAIT"
    return ""


def classify_error(
    code: str | None,
    error: str = "",
    retry_after: int | None = None,
    *,
    now: datetime | None = None,
) -> AccountDecision:
    """Translate Telegram RPC errors into a deterministic account action."""
    moment = now or utcnow()
    value = normalized_code(code, error)
    if value in {"FLOOD_WAIT", "FLOOD_PREMIUM_WAIT"}:
        seconds = max(1, min(7 * 24 * 3600, int(retry_after or 60)))
        risk = 70 if seconds < 3600 else 82
        return AccountDecision(True, "cooldown", risk, moment + timedelta(seconds=seconds))
    if value == "PEER_FLOOD":
        # Telegram не сообщает срок для PEER_FLOOD. Сутки — защитная пауза Talaria,
        # а не обещание точной даты снятия ограничения.
        return AccountDecision(True, "limited", 95, moment + timedelta(hours=24))
    if value in {"PHONE_NUMBER_BANNED", "USER_DEACTIVATED_BAN"}:
        return AccountDecision(True, "banned", 100)
    if value in {"AUTH_KEY_UNREGISTERED", "AUTH_KEY_DUPLICATED"}:
        return AccountDecision(True, "reauth", 85)
    if value in RECIPIENT_CODES:
        return AccountDecision(False, "ready", 0)
    return AccountDecision(False, "ready", 15)


def apply_error(
    account: WarmupAccount,
    *,
    code: str | None,
    error: str,
    retry_after: int | None = None,
    now: datetime | None = None,
) -> AccountDecision:
    moment = now or utcnow()
    decision = classify_error(code, error, retry_after, now=moment)
    account.last_tg_error = (error or "Ошибка Telegram")[:500]
    account.last_tg_error_code = normalized_code(code, error)[:64]
    account.last_tg_error_at = moment
    if decision.quarantine:
        account.operational_status = decision.status
        account.cooldown_until = decision.cooldown_until
        account.risk_score = max(int(account.risk_score or 0), decision.risk)
        account.consecutive_errors = int(account.consecutive_errors or 0) + 1
        if decision.status == "reauth":
            # Отозванная MTProto-сессия не должна оставаться «подключённой» ни
            # в карточке, ни в следующем выборе пула.
            account.connected_at = None
            account.status = "paused"
    elif decision.risk:
        account.risk_score = min(100, int(account.risk_score or 0) + decision.risk)
        account.consecutive_errors = int(account.consecutive_errors or 0) + 1
        # Три подряд транспортные/неизвестные ошибки — уже рост ошибок, а не
        # случайность. Делаем часовую защитную паузу и не ждём четвёртой.
        if int(account.consecutive_errors or 0) >= 3:
            decision = AccountDecision(True, "cooldown", 65, moment + timedelta(hours=1))
            account.operational_status = decision.status
            account.cooldown_until = decision.cooldown_until
            account.risk_score = max(int(account.risk_score or 0), decision.risk)
    return decision


def apply_safety_snapshot(
    account: WarmupAccount,
    *,
    unread_count: int,
    unread_dialogs: int,
    now: datetime | None = None,
) -> AccountDecision:
    """Сохранить реальный unread-снимок и остановить резкий скачок.

    Первый снимок — только база сравнения. Автопауза срабатывает при росте
    непрочитанных сообщений на 10+ между проверками; абсолютное число без
    предыдущего состояния ничего не доказывает и не используется.
    """
    moment = now or utcnow()
    current = max(0, int(unread_count or 0))
    dialogs = max(0, int(unread_dialogs or 0))
    previous_check = account.safety_checked_at
    had_baseline = previous_check is not None
    previous = max(0, int(account.unread_count or 0))
    growth = max(0, current - previous) if had_baseline else 0
    account.unread_count = current
    account.unread_dialogs = dialogs
    account.unread_growth = growth
    account.safety_checked_at = moment
    # SQLAlchemy column defaults are applied on INSERT. A freshly constructed
    # account can therefore still have ``None`` here even though its effective
    # state is "ready". Treat it the same way in the pure safety evaluator.
    # Непрочитанные могли накопиться задолго до Talaria. Считать их риском можно
    # только если после предыдущего снимка с этого аккаунта действительно ушло
    # сообщение. Это исключает ложную автопаузу сразу после подключения.
    last_success = account.last_success_at
    if last_success is not None and last_success.tzinfo is None:
        last_success = last_success.replace(tzinfo=UTC)
    if previous_check is not None and previous_check.tzinfo is None:
        previous_check = previous_check.replace(tzinfo=UTC)
    sent_since_snapshot = bool(
        had_baseline and last_success is not None and last_success > previous_check
    )
    if sent_since_snapshot and growth >= 10 and (account.operational_status or "ready") == "ready":
        account.operational_status = "cooldown"
        account.cooldown_until = moment + timedelta(minutes=30)
        account.last_tg_error_code = "UNREAD_SPIKE"
        account.last_tg_error = (
            f"Автопауза Talaria: непрочитанных сообщений стало на {growth} больше"
        )
        account.last_tg_error_at = moment
        account.risk_score = max(65, int(account.risk_score or 0))
        return AccountDecision(True, "cooldown", 65, account.cooldown_until)
    return AccountDecision(
        False, account.operational_status or "ready", int(account.risk_score or 0)
    )


def mark_success(account: WarmupAccount, *, now: datetime | None = None) -> None:
    moment = now or utcnow()
    account.last_success_at = moment
    account.consecutive_errors = 0
    account.risk_score = max(0, int(account.risk_score or 0) - 5)
    if account.operational_status == "cooldown" and is_expired(account.cooldown_until, moment):
        account.operational_status = "ready"
        account.cooldown_until = None


def is_expired(value: datetime | None, now: datetime | None = None) -> bool:
    if value is None:
        return True
    moment = now or utcnow()
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value <= moment


def release_expired(
    db: Session,
    owner_id: int,
    *,
    now: datetime | None = None,
    rows: list[WarmupAccount] | None = None,
) -> int:
    """Return timed cooldowns to the pool; permanent states stay quarantined."""
    moment = now or utcnow()
    candidates = (
        rows
        if rows is not None
        else (
            db.query(WarmupAccount)
            .filter(
                WarmupAccount.owner_id == owner_id,
                WarmupAccount.operational_status.in_(("cooldown", "limited")),
                WarmupAccount.cooldown_until.isnot(None),
            )
            .all()
        )
    )
    released = 0
    for row in candidates:
        if (
            row.owner_id == owner_id
            and row.operational_status in ("cooldown", "limited")
            and row.cooldown_until is not None
            and is_expired(row.cooldown_until, moment)
        ):
            row.operational_status = "ready"
            row.cooldown_until = None
            row.risk_score = max(0, int(row.risk_score or 0) - 20)
            released += 1
    if released:
        db.flush()
    return released
