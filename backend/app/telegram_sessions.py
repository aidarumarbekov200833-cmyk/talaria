"""Живая проверка Telegram-сессий перед любым действием аккаунта."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session

from .account_health import apply_error, apply_safety_snapshot
from .models import WarmupAccount
from .telegram_gateway import TelegramGatewayError, gateway


@dataclass(frozen=True)
class SessionProbe:
    authorized: bool
    reason: str = ""
    unavailable: bool = False
    changed: bool = False


def recorded_connected(account: WarmupAccount) -> bool:
    """Есть признак сессии, которую надо подтвердить у gateway.

    У старых дополнительных слотов до появления connected_at сохранился только
    tg_id. Их не ломаем: один раз проверяем gateway и при успехе мигрируем поле.
    Основной Telegram Login без MTProto-сессии таким исключением не является.
    """
    return bool(account.tg_id and (account.connected_at or not account.is_primary))


def probe_account_session(db: Session, account: WarmupAccount) -> SessionProbe:
    """Спросить gateway о живой авторизации и зафиксировать отзыв сессии.

    Сетевой сбой gateway не переименовывается в отзыв Telegram-сессии: это
    разные причины. Действия при таком сбое всё равно fail-closed у вызывающей
    стороны, но карточка не заставляет человека зря входить заново.
    """
    if not recorded_connected(account):
        return SessionProbe(False, "Сначала подключите Telegram-аккаунт")
    try:
        body = gateway.session_status(account.owner_id, account.id)
    except TelegramGatewayError as exc:
        return SessionProbe(False, str(exc), unavailable=True)
    except RuntimeError as exc:
        return SessionProbe(False, str(exc), unavailable=True)

    if bool(body.get("authorized")):
        if account.connected_at is None:
            account.connected_at = datetime.now(UTC)
            db.flush()
            return SessionProbe(True, changed=True)
        return SessionProbe(True)

    code = str(body.get("error_code") or "AUTH_KEY_UNREGISTERED")[:64]
    reason = str(
        body.get("reason")
        or "Сессия завершена в Telegram. Подключите аккаунт заново."
    )[:500]
    apply_error(account, code=code, error=reason)
    account.connected_at = None
    account.status = "paused"
    account.cooldown_until = None
    account.last_tg_error_at = datetime.now(UTC)
    db.flush()
    return SessionProbe(False, reason, changed=True)


def live_accounts(
    db: Session,
    rows: list[WarmupAccount],
    *,
    fail_closed: bool = True,
) -> list[WarmupAccount]:
    """Оставить только аккаунты с подтверждённой прямо сейчас сессией."""
    active: list[WarmupAccount] = []
    changed = False
    for account in rows:
        probe = probe_account_session(db, account)
        changed = changed or probe.changed
        if probe.authorized or (probe.unavailable and not fail_closed):
            active.append(account)
    if changed:
        db.commit()
    return active


def probe_account_safety(
    db: Session,
    account: WarmupAccount,
    *,
    force: bool = False,
) -> SessionProbe:
    """Обновить unread-снимок напрямую из Telegram, максимум раз в 2 минуты."""
    checked = account.safety_checked_at
    if checked and checked.tzinfo is None:
        checked = checked.replace(tzinfo=UTC)
    if not force and checked and checked >= datetime.now(UTC) - timedelta(minutes=2):
        return SessionProbe(account.operational_status == "ready")
    try:
        body = gateway.account_safety(account.owner_id, account.id)
    except (TelegramGatewayError, RuntimeError) as exc:
        return SessionProbe(False, str(exc), unavailable=True)
    decision = apply_safety_snapshot(
        account,
        unread_count=int(body.get("unread_count") or 0),
        unread_dialogs=int(body.get("unread_dialogs") or 0),
    )
    db.flush()
    return SessionProbe(not decision.quarantine, changed=True)


def live_safe_accounts(
    db: Session,
    rows: list[WarmupAccount],
    *,
    fail_closed: bool = True,
) -> list[WarmupAccount]:
    """Живая сессия плюс свежий safety-снимок перед запуском кампании."""
    result: list[WarmupAccount] = []
    changed = False
    for account in live_accounts(db, rows, fail_closed=fail_closed):
        probe = probe_account_safety(db, account)
        changed = changed or probe.changed
        if probe.authorized or (probe.unavailable and not fail_closed):
            result.append(account)
    if changed:
        db.commit()
    return result
