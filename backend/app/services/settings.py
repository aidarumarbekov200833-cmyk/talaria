"""Settings read models and Telegram ticket application service."""

from __future__ import annotations

import hashlib
import hmac
import time
from collections.abc import Callable
from datetime import UTC, datetime

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .. import plans
from ..config import settings as app_config
from ..models import AppSettings, User, WarmupAccount
from ..repositories.settings import SettingsRepository
from ..schemas import (
    NotifyTargetOut,
    NotifyTargetTestOut,
    SettingsOut,
    TelegramAccountOut,
    TelegramAccountTicketOut,
)
from ._legacy import ai_provider_ready
from .errors import DomainError

TG_TICKET_TTL = 300
_MISSING_TG_SECRET = (
    "Telegram-gateway не настроен: задайте TG_CLIENT_SECRET в .env и перезапустите."
)


def get_settings(db: Session, owner_id: int) -> AppSettings:
    """Return settings, creating the row safely when the owner first needs it."""
    repository = SettingsRepository(db)
    row = repository.get_settings(owner_id)
    if row is not None:
        return row

    plan = plans.get_plan(plans.effective_plan_slug(db, owner_id))
    row = AppSettings(owner_id=owner_id, monthly_limit=int(plan.get("monthly_messages", 0)))
    repository.stage_settings(row)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        winner = repository.get_settings(owner_id)
        if winner is None:
            raise
        return winner
    db.refresh(row)
    return row


def current_period() -> str:
    return datetime.now(UTC).strftime("%Y-%m")


def roll_month(db: Session, settings: AppSettings) -> AppSettings:
    """Reset the persisted monthly usage only after a UTC calendar rollover."""
    period = current_period()
    if settings.monthly_period != period:
        settings.monthly_period = period
        settings.monthly_used = 0
        db.commit()
        db.refresh(settings)
    return settings


def read_settings(
    db: Session,
    owner_id: int,
    *,
    get_settings_fn: Callable[[Session, int], AppSettings] = get_settings,
    roll_month_fn: Callable[[Session, AppSettings], AppSettings] = roll_month,
    ai_allowed_fn: Callable[[Session, int], bool] = plans.ai_allowed,
    ai_provider_ready_fn: Callable[[Session], bool] = ai_provider_ready,
) -> SettingsOut:
    """Build the existing settings response from the owner's current state."""
    settings = roll_month_fn(db, get_settings_fn(db, owner_id))
    return SettingsOut(
        project_name="Talaria",
        monthly_limit=-1,
        monthly_used=int(settings.monthly_used or 0),
        monthly_left=-1,
        low_limit_threshold=int(settings.low_limit_threshold or 0),
        low_limit_reached=False,
        ai_available=ai_allowed_fn(db, owner_id),
        ai_ready=ai_provider_ready_fn(db),
        reply_notify=bool(settings.reply_notify),
        notify_target_tg_id=settings.notify_target_tg_id,
        notify_target_ok=settings.notify_target_ok,
        send_window_enabled=bool(settings.send_window_enabled),
        send_window_start=int(settings.send_window_start or 10),
        send_window_end=int(settings.send_window_end or 20),
    )


def notify_target_of(db: Session, user: User) -> int | None:
    """Куда слать уведомления этому человеку.

    Пусто — значит выбор не делали, и остаётся аккаунт, которым вошли.
    """
    row = db.query(AppSettings.notify_target_tg_id).filter_by(owner_id=user.id).one_or_none()
    chosen = row[0] if row else None
    return int(chosen) if chosen else user.telegram_id


def list_notify_targets(db: Session, user: User) -> list[NotifyTargetOut]:
    """Аккаунты, между которыми есть смысл выбирать.

    Это аккаунт входа и подключённые аккаунты-отправители. Чужие chat_id сюда
    не попадают: иначе уведомления о чужих лидах можно было бы направить
    постороннему человеку, просто подставив номер.
    """
    settings = get_settings(db, user.id)
    selected = settings.notify_target_tg_id or user.telegram_id
    targets: list[NotifyTargetOut] = []
    seen: set[int] = set()

    if user.telegram_id:
        seen.add(int(user.telegram_id))
        targets.append(
            NotifyTargetOut(
                tg_id=int(user.telegram_id),
                label=user.display_name or (user.username or "Аккаунт входа"),
                kind="login",
                selected=int(user.telegram_id) == int(selected or 0),
            )
        )

    accounts = (
        db.query(WarmupAccount.tg_id, WarmupAccount.label, WarmupAccount.tg_username)
        .filter(WarmupAccount.owner_id == user.id, WarmupAccount.tg_id.isnot(None))
        .order_by(WarmupAccount.id)
        .all()
    )
    for tg_id, label, username in accounts:
        try:
            numeric = int(tg_id)
        except (TypeError, ValueError):
            continue
        if numeric in seen:
            continue
        seen.add(numeric)
        targets.append(
            NotifyTargetOut(
                tg_id=numeric,
                label=label or (f"@{username}" if username else str(numeric)),
                kind="sender",
                selected=numeric == int(selected or 0),
            )
        )
    return targets


def update_settings(
    db: Session,
    owner_id: int,
    *,
    reply_notify: bool | None = None,
    notify_target_tg_id: int | None = None,
    send_window_enabled: bool | None = None,
    send_window_start: int | None = None,
    send_window_end: int | None = None,
    user: User | None = None,
) -> SettingsOut:
    """Изменить пользовательские настройки уведомлений.

    None означает «не трогать»: интерфейс меняет поля по одному.
    """
    row = get_settings(db, owner_id)
    if reply_notify is not None:
        row.reply_notify = bool(reply_notify)
    if notify_target_tg_id is not None:
        owner = user or db.get(User, owner_id)
        allowed = {t.tg_id for t in list_notify_targets(db, owner)} if owner else set()
        if int(notify_target_tg_id) not in allowed:
            raise DomainError(
                400,
                "Этот аккаунт вам не принадлежит. Выберите аккаунт входа или "
                "один из подключённых.",
            )
        # Выбор сменился — прежняя отметка о доставке к новому адресу
        # отношения не имеет, и оставлять её значило бы врать интерфейсу.
        if row.notify_target_tg_id != int(notify_target_tg_id):
            row.notify_target_ok = None
        row.notify_target_tg_id = int(notify_target_tg_id)
    if send_window_enabled is not None:
        row.send_window_enabled = bool(send_window_enabled)
    if send_window_start is not None or send_window_end is not None:
        # Границы проверяем вместе: пришло одно поле — вторым берём текущее,
        # иначе «начало 22» временно оказалось бы больше конца и настройка
        # молча откатилась бы к умолчанию.
        start = int(send_window_start if send_window_start is not None else row.send_window_start)
        end = int(send_window_end if send_window_end is not None else row.send_window_end)
        if start >= end:
            raise DomainError(
                400,
                "Начало окна должно быть раньше конца. Окно через полночь не "
                "поддерживается: ночная рассылка — то, от чего окно защищает.",
            )
        row.send_window_start = start
        row.send_window_end = end
    db.commit()
    return read_settings(db, owner_id)


def test_notify_target(
    db: Session,
    user: User,
    *,
    send_fn: Callable[..., bool] | None = None,
) -> NotifyTargetTestOut:
    """Отправить проверочное сообщение туда, куда пойдут уведомления.

    Единственный честный способ узнать, дойдёт ли: бот не может писать первым,
    и без нажатого Start Telegram молча отвечает отказом.
    """
    if send_fn is None:
        from ..alerts import notify_user as send_fn  # локально: избегаем цикла импорта

    target = notify_target_of(db, user)
    row = get_settings(db, user.id)
    if not target:
        row.notify_target_ok = False
        db.commit()
        return NotifyTargetTestOut(
            delivered=False,
            detail="Не к чему привязать уведомления: у аккаунта нет Telegram ID.",
        )

    delivered = bool(
        send_fn(
            target,
            "Проверка Talaria: сюда будут приходить уведомления об ответах лидов.",
            dedupe_key=f"notify-test:{user.id}:{target}",
        )
    )
    row.notify_target_ok = delivered
    db.commit()
    return NotifyTargetTestOut(
        delivered=delivered,
        detail=(
            "Сообщение доставлено — уведомления будут приходить сюда."
            if delivered
            else "Telegram не пропустил сообщение. Откройте бота с этого аккаунта "
            "и нажмите Start, затем проверьте ещё раз."
        ),
    )



def read_telegram_account(
    db: Session,
    user: User,
    *,
    get_subscription_fn: Callable[[Session, int], object] = plans.get_subscription,
    effective_plan_slug_fn: Callable[[Session, int], str] = plans.effective_plan_slug,
    get_plan_fn: Callable[[str | None], dict] = plans.get_plan,
) -> TelegramAccountOut:
    """Serialize the current user's Telegram card and owned warmup counts."""
    sub = get_subscription_fn(db, user.id)
    slug = effective_plan_slug_fn(db, user.id)
    plan = get_plan_fn(slug)
    repository = SettingsRepository(db)
    return TelegramAccountOut(
        telegram_id=user.telegram_id,
        username=user.username or "",
        name=user.display_name,
        photo_url=user.photo_url or "",
        joined_at=user.created_at,
        is_admin=app_config.is_admin(user.username, user.telegram_id),
        plan=slug,
        plan_name=plan["name"],
        plan_status=(sub.status if sub else "none") or "none",
        blocked=bool(sub.blocked) if sub else False,
        paid_until=sub.paid_until if sub else None,
        warmup_total=repository.count_warmup_accounts(user.id),
        warmup_active=repository.count_warmup_accounts(user.id, active_only=True),
    )


def telegram_account_ticket(
    db: Session,
    user: User,
    account_id: int | None,
    *,
    now: Callable[[], float] = time.time,
) -> TelegramAccountTicketOut:
    """Issue the existing signed, short-lived ticket for one owned warmup slot."""
    secret = (app_config.tg_client_secret or "").strip()
    if not secret:
        raise DomainError(503, _MISSING_TG_SECRET)

    owned = SettingsRepository(db).list_warmup_ids(user.id)
    if account_id is not None and account_id not in owned:
        raise DomainError(404, "Аккаунт не найден")
    chosen = account_id if account_id is not None else (owned[0] if owned else 0)
    primary = 1 if not owned or chosen == owned[0] else 0
    exp = int(now()) + TG_TICKET_TTL
    payload = f"{user.id}.{chosen}.{primary}.{exp}"
    signature = hmac.new(secret.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).hexdigest()
    return TelegramAccountTicketOut(
        ticket=f"{payload}.{signature}",
        account_id=chosen or None,
        expires_in=TG_TICKET_TTL,
    )
