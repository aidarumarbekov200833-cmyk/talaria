"""Прогрев Telegram-аккаунтов.

Сервер не хранит учётные данные (телефон, session string, 2FA пароль) и не
выполняет действий в Telegram — это нарушает ToS Telegram и грозит баном.
Приложение ведёт план и журнал, пользователь действует вручную.
"""

from __future__ import annotations

import json
import re
import secrets
from datetime import UTC, datetime, timedelta
from datetime import date as date_type

from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..account_health import release_expired
from ..config import settings as app_config
from ..database import get_db
from ..dependencies import get_current_user
from ..models import CampaignSend, Lead, User, WarmupAccount, WarmupLog
from ..plans import check_object_limit
from ..schemas import (
    AccountRiskListOut,
    AccountRiskOut,
    OkOut,
    SpamBotOut,
    WarmupAccountIn,
    WarmupAccountOut,
    WarmupAccountUpdate,
    WarmupDayIn,
    WarmupIdentityIn,
    WarmupListOut,
    WarmupLogOut,
    WarmupToggleIn,
)
from ..services import account_risk as account_risk_service
from ..services import log_event
from ..telegram_gateway import gateway
from ..telegram_sessions import probe_account_safety, probe_account_session, recorded_connected

router = APIRouter(prefix="/api/warmup", tags=["warmup"])

DISCLAIMER = (
    "Пароль, 2FA-код и строка Telegram-сессии не хранятся в базе приложения. "
    "Параметры прогрева назначаются автоматически после включения; подключённый "
    "аккаунт используется только для запущенных вами проверок и рассылок."
)

STATUSES = {"active", "paused", "done"}

AUTO_DAILY_SEND_LIMIT = 30
AUTO_DELAY_MIN_SECONDS = 20
AUTO_DELAY_MAX_SECONDS = 60
PENDING_ACCOUNT_TTL_MINUTES = 15


def _ramp_plan(days: int) -> list[int]:
    """Плавное наращивание числа сообщений в день.

    14 дней — классический прогрев: начинаем с 5, доходим до 50. Короче или
    длиннее — линейная интерполяция между этими границами.
    """
    if days < 1:
        return []
    start, end = 5, 50
    step = (end - start) / max(1, days - 1)
    return [max(1, round(start + i * step)) for i in range(days)]


def _owned_account(account_id: int, db: Session, user: User) -> WarmupAccount:
    row = db.get(WarmupAccount, account_id)
    if not row or row.owner_id != user.id:
        raise HTTPException(404, "Аккаунт не найден")
    return row


def _login_profile_label(user: User) -> str:
    """Имя основной карточки из подтверждённого Telegram Login профиля."""
    return (user.display_name or (f"@{user.username}" if user.username else "Основной аккаунт"))[
        :120
    ]


def _ensure_login_account(
    db: Session,
    user: User,
    *,
    accounts: list[WarmupAccount] | None = None,
) -> WarmupAccount:
    """Учесть Telegram-профиль, которым владелец вошёл в приложение.

    Telegram Login подтверждает личность, но не создаёт MTProto-сессию. Поэтому
    карточка уже существует и считается в тарифе, а `connected_at` остаётся
    пустым до отдельного входа по номеру телефона.
    """
    telegram_id = str(user.telegram_id)
    if accounts is None:
        account = (
            db.query(WarmupAccount)
            .filter(WarmupAccount.owner_id == user.id, WarmupAccount.is_primary.is_(True))
            .first()
        )
        if account is None:
            # Если тот же профиль уже подключали раньше, не плодим дубликат.
            account = (
                db.query(WarmupAccount)
                .filter(WarmupAccount.owner_id == user.id, WarmupAccount.tg_id == telegram_id)
                .first()
            )
    else:
        account = next((row for row in accounts if row.is_primary), None)
        if account is None:
            account = next((row for row in accounts if row.tg_id == telegram_id), None)

    if account is not None:
        username = (user.username or "").lstrip("@")[:64] or None
        label = _login_profile_label(user)
        changed = any(
            (
                not account.is_primary,
                account.tg_id != telegram_id,
                account.tg_username != username,
                account.label != label,
            )
        )
        account.is_primary = True
        account.tg_id = telegram_id
        account.tg_username = username
        account.label = label
        if changed:
            db.commit()
            db.refresh(account)
        return account

    account = WarmupAccount(
        owner_id=user.id,
        label=_login_profile_label(user),
        plan_days=14,
        daily_send_limit=AUTO_DAILY_SEND_LIMIT,
        delay_min_seconds=AUTO_DELAY_MIN_SECONDS,
        delay_max_seconds=AUTO_DELAY_MAX_SECONDS,
        sending_enabled=True,
        verification_enabled=True,
        is_primary=True,
        status="paused",
        started_on=None,
        tg_id=telegram_id,
        tg_username=(user.username or "").lstrip("@")[:64] or None,
    )
    db.add(account)
    try:
        db.flush()
        for day_num, target in enumerate(_ramp_plan(14), start=1):
            db.add(
                WarmupLog(
                    account_id=account.id,
                    day=day_num,
                    date=None,
                    target_messages=target,
                    target_actions=json.dumps(
                        DEFAULT_ACTIONS[:2] if day_num <= 3 else DEFAULT_ACTIONS,
                        ensure_ascii=False,
                    ),
                )
            )
        db.commit()
    except IntegrityError:
        # Параллельные запросы после входа могли одновременно создавать карточку.
        db.rollback()
        account = (
            db.query(WarmupAccount)
            .filter(WarmupAccount.owner_id == user.id, WarmupAccount.is_primary.is_(True))
            .one()
        )
    else:
        db.refresh(account)
    return account


# Сколько неотмеченных дней ещё не считается отставанием. Один-два дня — это
# нормально: человек мог сделать действия, но забыть нажать галочку, и
# кричать об отставании сразу значит приучить игнорировать предупреждения.
WARMUP_OVERDUE_TOLERANCE = 2


def _logs_by_account(db: Session, account_ids: list[int]) -> dict[int, list[WarmupLog]]:
    """Журналы всех аккаунтов одним запросом.

    Раньше список аккаунтов дёргал _enrich на каждую строку, а тот — свой запрос
    к WarmupLog. На десятке аккаунтов по 14 дней это одиннадцать запросов вместо двух.
    """
    if not account_ids:
        return {}
    rows = (
        db.query(WarmupLog)
        .filter(WarmupLog.account_id.in_(account_ids))
        .order_by(WarmupLog.account_id, WarmupLog.day)
        .all()
    )
    grouped: dict[int, list[WarmupLog]] = {}
    for row in rows:
        grouped.setdefault(row.account_id, []).append(row)
    return grouped


def _readiness(
    account: WarmupAccount, logs: list[WarmupLog], today: date_type
) -> tuple[bool, str, int]:
    """Готов ли аккаунт к рассылке: (ready, причина, дней отставания).

    Это НЕ проверка аккаунта в Telegram. Сервер не видит ни сообщений, ни
    ограничений, ни спам-блока — единственный источник данных здесь — галочки,
    которые ставил сам пользователь. Поэтому формулировки говорят о плане, а не
    обещают, что аккаунт точно не получит бан.
    """
    plan_days = account.plan_days or 0
    days_done = sum(1 for log in logs if log.done)

    if plan_days < 1:
        return False, "План прогрева не задан", 0

    if account.status == "done" or days_done >= plan_days:
        return True, f"Прогрев завершён: {days_done} из {plan_days} дней", 0

    if not account.started_on:
        return False, "Прогрев не начат", 0

    if account.status == "paused":
        return (
            False,
            f"Прогрев на паузе: {days_done} из {plan_days} дней",
            0,
        )

    # Сколько дней плана уже должно быть отмечено к сегодняшнему дню.
    elapsed = (today - account.started_on).days + 1
    expected = max(0, min(plan_days, elapsed))
    overdue = max(0, expected - days_done)
    left = plan_days - days_done

    if overdue > WARMUP_OVERDUE_TOLERANCE:
        return (
            False,
            f"Отставание от плана: не отмечено дней — {overdue}. "
            f"Выполнено {days_done} из {plan_days}",
            overdue,
        )

    return (
        False,
        f"Прогрев идёт: выполнено {days_done} из {plan_days}, осталось {left}",
        overdue,
    )


def _health(
    account: WarmupAccount,
    *,
    connected: bool,
    ready: bool,
    overdue_days: int,
) -> tuple[int, str, list[str]]:
    """Оценка именно настройки слота, без выдуманной проверки Telegram."""
    score = 0
    reasons: list[str] = []
    if connected:
        score += 35
        reasons.append("Telegram подключён")
    else:
        reasons.append("подключите Telegram")
    if ready:
        score += 25
        reasons.append("план прогрева завершён")
    elif account.status == "active":
        score += 10
        reasons.append("прогрев идёт")
    else:
        reasons.append("прогрев не активен")
    if account.sending_enabled:
        score += 10
    else:
        reasons.append("рассылка выключена")
    if account.verification_enabled:
        score += 10
    else:
        reasons.append("верификация выключена")
    if (account.delay_min_seconds or 0) >= 15 and (account.delay_max_seconds or 0) >= (
        account.delay_min_seconds or 0
    ):
        score += 10
    else:
        reasons.append("увеличьте паузу минимум до 15 секунд")
    if 1 <= (account.daily_send_limit or 0) <= 60:
        score += 10
    else:
        reasons.append("рекомендуемый дневной лимит — до 60")
    if overdue_days:
        score -= min(20, overdue_days * 4)
        reasons.append(f"пропущено дней плана: {overdue_days}")
    score = max(0, min(100, score))
    label = "Готов" if score >= 85 else "В процессе" if score >= 55 else "Требует настройки"
    return score, label, reasons


def _enrich(
    db: Session,
    account: WarmupAccount,
    logs: list[WarmupLog] | None = None,
    metrics: dict[str, int] | None = None,
) -> WarmupAccountOut:
    """Добавить вычисляемые поля: текущий день, выполнено дней, цель на сегодня.

    logs можно передать снаружи, если они уже выбраны одним запросом на все
    аккаунты — так список не даёт N+1.
    """
    if logs is None:
        logs = (
            db.query(WarmupLog)
            .filter(WarmupLog.account_id == account.id)
            .order_by(WarmupLog.day)
            .all()
        )
    today = date_type.today()
    current_day = 0
    days_done = sum(1 for log in logs if log.done)
    today_target = 0

    if account.started_on and account.status == "active":
        delta = (today - account.started_on).days + 1
        if 1 <= delta <= account.plan_days:
            current_day = delta
            for log in logs:
                if log.day == current_day:
                    today_target = log.target_messages or 0
                    break

    ready, ready_reason, overdue_days = _readiness(account, logs, today)
    connected = recorded_connected(account) and account.operational_status != "reauth"
    health_score, health_label, health_reasons = _health(
        account,
        connected=connected,
        ready=ready,
        overdue_days=overdue_days,
    )

    metrics = metrics or {}
    sent_today = int(metrics.get("sent", 0))
    failed_today = int(metrics.get("failed", 0))
    verified_today = int(metrics.get("verified", 0))
    daily_limit = int(account.daily_send_limit or 30)
    cooldown_until = account.cooldown_until
    if cooldown_until and cooldown_until.tzinfo is None:
        cooldown_until = cooldown_until.replace(tzinfo=UTC)
    cooldown_seconds = max(
        0,
        int((cooldown_until - datetime.now(UTC)).total_seconds()) if cooldown_until else 0,
    )
    utilization_risk = round(min(1.0, sent_today / max(1, daily_limit)) * 20)
    runtime_risk = min(
        100,
        max(int(account.risk_score or 0), utilization_risk)
        + min(15, int(account.consecutive_errors or 0) * 5),
    )

    return WarmupAccountOut(
        id=account.id,
        label=account.label,
        note=account.note,
        started_on=account.started_on,
        plan_days=account.plan_days,
        status=account.status,
        daily_send_limit=daily_limit,
        delay_min_seconds=account.delay_min_seconds or 0,
        delay_max_seconds=account.delay_max_seconds or 0,
        sending_enabled=bool(account.sending_enabled),
        verification_enabled=bool(account.verification_enabled),
        is_primary=bool(account.is_primary),
        created_at=account.created_at,
        current_day=current_day,
        days_done=days_done,
        today_target=today_target,
        ready=ready,
        ready_reason=ready_reason,
        overdue_days=overdue_days,
        health_score=health_score,
        health_label=health_label,
        health_reasons=health_reasons,
        tg_username=account.tg_username,
        tg_phone=account.tg_phone,
        # Подключён = tg-mini-client уже сообщил, кто вошёл. Сам бэкенд файлы
        # сессий не видит и видеть не должен.
        connected=connected,
        connected_at=account.connected_at,
        operational_status=account.operational_status or "ready",
        cooldown_until=cooldown_until,
        cooldown_seconds=cooldown_seconds,
        last_tg_error=account.last_tg_error or "",
        last_tg_error_code=account.last_tg_error_code or "",
        last_tg_error_at=account.last_tg_error_at,
        last_success_at=account.last_success_at,
        risk_score=runtime_risk,
        consecutive_errors=int(account.consecutive_errors or 0),
        unread_count=int(account.unread_count or 0),
        unread_dialogs=int(account.unread_dialogs or 0),
        unread_growth=int(account.unread_growth or 0),
        safety_checked_at=account.safety_checked_at,
        sent_today=sent_today,
        failed_today=failed_today,
        verified_today=verified_today,
        daily_left=max(0, daily_limit - sent_today),
        spambot_status=account.spambot_status or "unknown",
        spambot_detail=account.spambot_detail or "",
        spambot_checked_at=account.spambot_checked_at,
        spambot_appealed_at=account.spambot_appealed_at,
        logs=[WarmupLogOut.model_validate(log) for log in logs],
    )


@router.get("", response_model=WarmupListOut)
def list_accounts(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> WarmupListOut:
    """Все аккаунты прогрева с их планом."""
    rows = (
        db.query(WarmupAccount)
        .filter(WarmupAccount.owner_id == user.id)
        .order_by(WarmupAccount.is_primary.desc(), WarmupAccount.id.desc())
        .all()
    )
    login_account = _ensure_login_account(db, user, accounts=rows)
    if login_account not in rows:
        rows.append(login_account)
        rows.sort(key=lambda row: (bool(row.is_primary), int(row.id or 0)), reverse=True)

    # Страховка на случай закрытой вкладки/оборванного интернета: временный
    # слот, в который так и не вошли, не остаётся вечной карточкой «Аккаунт 2».
    cutoff = datetime.now(UTC) - timedelta(minutes=PENDING_ACCOUNT_TTL_MINUTES)
    stale: list[WarmupAccount] = []
    for account in rows:
        created_at = account.created_at
        if created_at is not None and created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=UTC)
        if (
            not account.is_primary
            and not account.tg_id
            and account.connected_at is None
            and created_at is not None
            and created_at < cutoff
        ):
            stale.append(account)
    for account in stale:
        try:
            gateway.drop_session(user.id, account.id)
        except RuntimeError:
            pass
        db.delete(account)
    if stale:
        stale_ids = {account.id for account in stale}
        rows = [account for account in rows if account.id not in stale_ids]

    released = release_expired(db, user.id, rows=rows)
    if stale or released:
        db.commit()

    # Карточка не доверяет вчерашнему connected_at: Telegram мог отозвать
    # сессию на телефоне. Недоступность gateway не выдаём за отзыв сессии, но
    # явный unauthorized сразу ставит аккаунт на паузу и требует новый вход.
    changed = False
    for account in rows:
        if recorded_connected(account):
            session_probe = probe_account_session(db, account)
            changed = session_probe.changed or changed
            if session_probe.authorized:
                safety_probe = probe_account_safety(db, account)
                changed = safety_probe.changed or changed
    if changed:
        db.commit()
    # Все журналы сразу, чтобы _enrich не дёргал БД на каждый аккаунт.
    grouped = _logs_by_account(db, [r.id for r in rows])
    today_start = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    metrics: dict[int, dict[str, int]] = {row.id: {} for row in rows}
    if rows:
        for account_id, status, count in (
            db.query(CampaignSend.account_id, CampaignSend.status, func.count(CampaignSend.id))
            .filter(
                CampaignSend.owner_id == user.id,
                CampaignSend.account_id.in_(metrics),
                CampaignSend.sent_at >= today_start,
                CampaignSend.status.in_(("sent", "failed", "uncertain")),
            )
            .group_by(CampaignSend.account_id, CampaignSend.status)
            .all()
        ):
            # uncertain для здоровья аккаунта = потраченная отправка: сообщение
            # могло уйти, и дневную нагрузку аккаунта оно создало.
            bucket = "sent" if status in ("sent", "uncertain") else str(status)
            row_metrics = metrics[int(account_id)]
            row_metrics[bucket] = row_metrics.get(bucket, 0) + int(count)
        for account_id, count in (
            db.query(Lead.tg_verified_by_account_id, func.count(Lead.id))
            .filter(
                Lead.tg_verified_by_account_id.in_(metrics),
                Lead.tg_checked_at >= today_start,
            )
            .group_by(Lead.tg_verified_by_account_id)
            .all()
        ):
            metrics[int(account_id)]["verified"] = int(count)
    return WarmupListOut(
        accounts=[_enrich(db, r, grouped.get(r.id, []), metrics.get(r.id)) for r in rows],
        disclaimer=DISCLAIMER,
    )


def _require_connected(db: Session, account: WarmupAccount) -> None:
    probe = probe_account_session(db, account)
    if probe.changed:
        db.commit()
    if probe.authorized:
        return
    if probe.unavailable:
        raise HTTPException(503, "Не удалось подтвердить Telegram-сессию. Повторите позже")
    raise HTTPException(409, probe.reason or "Сначала подключите Telegram-аккаунт")


def _save_spambot_result(
    db: Session,
    account: WarmupAccount,
    body: dict,
    *,
    appealed: bool = False,
) -> SpamBotOut:
    now = datetime.now(UTC)
    status = str(body.get("status") or "unknown")[:30]
    detail = str(body.get("detail") or "")[:2000]
    account.spambot_status = status
    account.spambot_detail = detail
    account.spambot_checked_at = now
    if appealed and status == "appeal_sent":
        account.spambot_appealed_at = now
    if status in {"limited", "appeal_available", "appeal_sent"}:
        account.operational_status = "limited"
        account.cooldown_until = None
        account.risk_score = max(95, int(account.risk_score or 0))
        account.last_tg_error_code = "SPAMBOT_LIMITED"
        account.last_tg_error = "SpamBot сообщил об ограничении аккаунта"
        account.last_tg_error_at = now
    elif status == "clear" and account.last_tg_error_code == "SPAMBOT_LIMITED":
        account.operational_status = "ready"
        account.cooldown_until = None
        account.risk_score = min(35, int(account.risk_score or 0))
        account.last_tg_error_code = ""
        account.last_tg_error = ""
    raw_manual_url = str(body.get("manual_url") or "").strip()
    manual_url = (
        "https://t.me/SpamBot"
        if raw_manual_url.lower().rstrip("/") != "https://t.me/spambot"
        else raw_manual_url
    )
    db.commit()
    return SpamBotOut(
        status=status,
        detail=detail,
        action=str(body.get("action") or ""),
        manual_url=manual_url,
    )


@router.get("/risk", response_model=AccountRiskListOut)
def account_risk(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> AccountRiskListOut:
    """Насколько аккаунты близки к блокировке — для владельца, не для админа.

    Стоит ДО «/{account_id}/...»: иначе FastAPI разобрал бы «risk» как номер
    аккаунта и ответил бы 422 вместо списка.
    """
    accounts = (
        db.query(WarmupAccount)
        .filter(WarmupAccount.owner_id == user.id)
        .order_by(WarmupAccount.id)
        .all()
    )
    connected = [row for row in accounts if row.connected_at]
    if not connected:
        return AccountRiskListOut(level="ok", items=[])

    today_start = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    sent_today: dict[int, int] = {}
    for account_id, count in (
        db.query(CampaignSend.account_id, func.count(CampaignSend.id))
        .filter(
            CampaignSend.owner_id == user.id,
            CampaignSend.account_id.in_([row.id for row in connected]),
            CampaignSend.sent_at >= today_start,
            # uncertain занимает дневной лимит аккаунта (см. sending.py).
            CampaignSend.status.in_(("sent", "uncertain")),
        )
        .group_by(CampaignSend.account_id)
        .all()
    ):
        sent_today[int(account_id)] = int(count)

    items = [
        account_risk_service.assess(row, sent_today=sent_today.get(row.id, 0))
        for row in connected
    ]
    items.sort(key=account_risk_service.sort_key)
    return AccountRiskListOut(
        level=account_risk_service.worst(items),
        items=[AccountRiskOut(**vars(item)) for item in items],
    )


@router.post("/{account_id}/spambot/check", response_model=SpamBotOut)
def check_spambot(
    account_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> SpamBotOut:
    """По кнопке пользователя спросить официальный @SpamBot о своём аккаунте."""
    account = _owned_account(account_id, db, user)
    _require_connected(db, account)
    try:
        body = gateway.spambot_status(user.id, account.id)
    except RuntimeError as exc:
        account.spambot_status = "error"
        account.spambot_detail = str(exc)[:2000]
        account.spambot_checked_at = datetime.now(UTC)
        db.commit()
        raise HTTPException(502, f"SpamBot: {exc}") from exc
    result = _save_spambot_result(db, account, body)
    log_event(
        db,
        user.id,
        "warmup",
        f"SpamBot проверил «{account.label}»: {result.status}",
        "warn" if result.status != "clear" else "info",
        "warmup",
        account.id,
    )
    return result


@router.post("/{account_id}/spambot/appeal", response_model=SpamBotOut)
def appeal_spambot(
    account_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> SpamBotOut:
    """Нажать доступную кнопку пересмотра; дальнейшие вопросы остаются пользователю."""
    account = _owned_account(account_id, db, user)
    _require_connected(db, account)
    try:
        body = gateway.spambot_appeal(user.id, account.id)
    except RuntimeError as exc:
        raise HTTPException(502, f"SpamBot: {exc}") from exc
    result = _save_spambot_result(db, account, body, appealed=True)
    log_event(
        db,
        user.id,
        "warmup",
        f"SpamBot для «{account.label}»: {result.action or result.status}",
        "info",
        "warmup",
        account.id,
    )
    return result


@router.post("", response_model=WarmupAccountOut)
def create_account(
    payload: WarmupAccountIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> WarmupAccountOut:
    """Создать план прогрева. Дней по умолчанию 14."""
    _ensure_login_account(db, user)
    check_object_limit(db, user.id, "warmup")
    days = max(1, min(60, payload.plan_days))
    account = WarmupAccount(
        owner_id=user.id,
        label=(payload.label.strip() or "Telegram-аккаунт")[:120],
        note=payload.note[:2000],
        plan_days=days,
        daily_send_limit=payload.daily_send_limit,
        delay_min_seconds=payload.delay_min_seconds,
        delay_max_seconds=payload.delay_max_seconds,
        sending_enabled=payload.sending_enabled,
        verification_enabled=payload.verification_enabled,
        status="active",
    )
    db.add(account)
    db.flush()

    # Генерируем логи на все дни плана, но без дат — старт укажет пользователь.
    ramp = _ramp_plan(days)
    actions = [
        "вступить в 2–3 открытых канала",
        "прочитать несколько постов",
        "поставить реакции на посты",
        "написать комментарии",
    ]
    for day_num, target in enumerate(ramp, start=1):
        db.add(
            WarmupLog(
                account_id=account.id,
                day=day_num,
                target_messages=target,
                target_actions=json.dumps(
                    actions[:2] if day_num <= 3 else actions, ensure_ascii=False
                ),
            )
        )
    db.commit()
    db.refresh(account)
    log_event(
        db,
        user.id,
        "warmup",
        f"Аккаунт «{account.label}» создан, план {days} дн.",
        "info",
        "warmup",
        account.id,
    )
    return _enrich(db, account)


@router.patch("/{account_id}", response_model=WarmupAccountOut)
def update_account(
    account_id: int,
    payload: WarmupAccountUpdate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> WarmupAccountOut:
    """Переименовать, сменить статус, записать заметку."""
    account = _owned_account(account_id, db, user)
    if payload.label is not None:
        account.label = payload.label[:120]
    if payload.note is not None:
        account.note = payload.note[:2000]
    if payload.status and payload.status in STATUSES:
        account.status = payload.status
    for field in (
        "daily_send_limit",
        "delay_min_seconds",
        "delay_max_seconds",
        "sending_enabled",
        "verification_enabled",
    ):
        value = getattr(payload, field)
        if value is not None:
            setattr(account, field, value)
    if (account.delay_max_seconds or 0) < (account.delay_min_seconds or 0):
        raise HTTPException(400, "Максимальная задержка меньше минимальной")
    db.commit()
    db.refresh(account)
    return _enrich(db, account)


@router.delete("/{account_id}", response_model=OkOut)
def delete_account(
    account_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> OkOut:
    """Удалить аккаунт вместе с его логами."""
    account = _owned_account(account_id, db, user)
    if account.is_primary:
        raise HTTPException(409, "Аккаунт входа удалить нельзя")
    label = account.label
    try:
        gateway.drop_session(user.id, account.id)
    except RuntimeError:
        # Удаление карточки не должно зависеть от краткого сбоя gateway.
        pass
    db.delete(account)
    db.commit()
    log_event(db, user.id, "warmup", f"Аккаунт «{label}» удалён", "warn")
    return OkOut()


@router.delete("/{account_id}/pending", response_model=OkOut)
def discard_pending_account(
    account_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> OkOut:
    """Удалить только незавершённый слот при закрытии формы подключения.

    Проверка identity защищает от гонки: если Telegram-вход успел завершиться,
    запоздавшая кнопка закрытия не удалит уже подключённый аккаунт.
    """
    account = _owned_account(account_id, db, user)
    if account.is_primary:
        raise HTTPException(409, "Основной аккаунт не является временным слотом")
    if account.tg_id or account.tg_username or account.tg_phone or account.connected_at:
        raise HTTPException(409, "Аккаунт уже подключён и не был удалён")
    try:
        gateway.drop_session(user.id, account.id)
    except RuntimeError:
        pass
    db.delete(account)
    db.commit()
    return OkOut()


# --- Аккаунты как слоты, а не анкеты ----------------------------------------
#
# Раньше, чтобы завести аккаунт, надо было вписать название, число дней и
# заметку, а потом отдельно нажать «Старт». Данные дублировали то, что и так
# знает Telegram, а план без старта выглядел сломанным. Теперь: одна кнопка
# создаёт слот, один переключатель ведёт прогрев, а подпись прилетает из
# Telegram-gateway после входа.

# Автоназвание можно тихо заменить на настоящее имя аккаунта; если человек
# переименовал сам — его вариант не трогаем.
AUTO_LABEL_RE = re.compile(r"^Аккаунт \d+$")

DEFAULT_ACTIONS = [
    "вступить в 2–3 открытых канала",
    "прочитать несколько постов",
    "поставить реакции на посты",
    "написать комментарии",
]


def _fill_dates(logs: list[WarmupLog], started_on: date_type) -> None:
    """Разложить дни плана по календарю от даты старта."""
    for log in logs:
        log.date = started_on + timedelta(days=(log.day or 1) - 1)


@router.post("/identity", response_model=OkOut)
def report_identity(
    payload: WarmupIdentityIn,
    x_send_secret: str = Header(default="", alias="X-Send-Secret"),
    db: Session = Depends(get_db),
) -> OkOut:
    """tg-mini-client сообщает, какой Telegram вошёл в этот слот.

    Без этой ручки ник и телефон пришлось бы вводить руками и они бы расходились
    с реальностью. Сессию и пароли здесь не храним — только подпись.
    """
    secret = (app_config.tg_client_secret or "").strip()
    if not secret or not secrets.compare_digest(x_send_secret or "", secret):
        raise HTTPException(status_code=403, detail="Неверный секрет")

    account = (
        db.query(WarmupAccount)
        .filter(
            WarmupAccount.id == payload.account_id,
            WarmupAccount.owner_id == payload.owner_id,
        )
        .first()
    )
    if not account:
        raise HTTPException(status_code=404, detail="Аккаунт не найден")

    if account.is_primary and str(payload.tg_id or "") != str(account.tg_id or ""):
        raise HTTPException(
            status_code=409,
            detail="Для основного профиля войдите тем же Telegram-аккаунтом, что и на сайте",
        )

    was_connected = bool(account.connected_at)
    account.tg_id = (payload.tg_id or "")[:40] or None
    account.tg_username = (payload.username or "").lstrip("@")[:64] or None
    account.tg_phone = (payload.phone or "")[:40] or None
    account.connected_at = datetime.now(UTC)
    account.operational_status = "ready"
    account.cooldown_until = None
    account.consecutive_errors = 0
    account.last_tg_error = ""
    account.last_tg_error_code = ""
    account.risk_score = min(20, int(account.risk_score or 0))
    # Первый реальный safety-снимок после входа должен стать базой сравнения,
    # а не «скачком» относительно пустого слота.
    account.unread_count = 0
    account.unread_dialogs = 0
    account.unread_growth = 0
    account.safety_checked_at = None

    name = (payload.name or "").strip()
    if name and AUTO_LABEL_RE.match(account.label or ""):
        account.label = name[:120]

    db.commit()
    if not was_connected:
        log_event(
            db,
            account.owner_id,
            "warmup",
            f"Аккаунт «{account.label}» подключён в Telegram-gateway",
            "info",
            "warmup",
            account.id,
        )
    return OkOut()


@router.post("/quick", response_model=WarmupAccountOut)
def create_quick(
    payload: WarmupAccountIn | None = None,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> WarmupAccountOut:
    """Создать пустой слот одной кнопкой; параметры прогрева сервер знает сам.

    Прогрев не запускается до явного переключателя в карточке. Это позволяет
    сначала завершить вход в Telegram и увидеть подтверждённый профиль.
    """
    # Основной Telegram-профиль тоже занимает один аккаунт тарифа.
    _ensure_login_account(db, user)
    check_object_limit(db, user.id, "warmup")
    payload = payload or WarmupAccountIn()
    days = payload.plan_days
    used = db.query(WarmupAccount).filter(WarmupAccount.owner_id == user.id).count()
    account = WarmupAccount(
        owner_id=user.id,
        label=(payload.label.strip() or f"Аккаунт {used + 1}")[:120],
        note=payload.note[:2000],
        plan_days=days,
        daily_send_limit=payload.daily_send_limit,
        delay_min_seconds=payload.delay_min_seconds,
        delay_max_seconds=payload.delay_max_seconds,
        sending_enabled=payload.sending_enabled,
        verification_enabled=payload.verification_enabled,
        status="paused",
        started_on=None,
    )
    db.add(account)
    db.flush()

    for day_num, target in enumerate(_ramp_plan(days), start=1):
        db.add(
            WarmupLog(
                account_id=account.id,
                day=day_num,
                date=None,
                target_messages=target,
                target_actions=json.dumps(
                    DEFAULT_ACTIONS[:2] if day_num <= 3 else DEFAULT_ACTIONS,
                    ensure_ascii=False,
                ),
            )
        )
    db.commit()
    db.refresh(account)
    log_event(
        db,
        user.id,
        "warmup",
        f"Добавлен аккаунт «{account.label}»; прогрев доступен с автонастройкой.",
        "info",
        "warmup",
        account.id,
    )
    return _enrich(db, account)


@router.post("/{account_id}/toggle", response_model=WarmupAccountOut)
def toggle_account(
    account_id: int,
    payload: WarmupToggleIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> WarmupAccountOut:
    """Один переключатель вместо «Старт» + смены статуса в форме.

    Включаем — если план ещё не разложен по датам, раскладываем от сегодня.
    Выключаем — пауза без потери отметок: отмеченные дни остаются.
    """
    account = _owned_account(account_id, db, user)
    logs = (
        db.query(WarmupLog).filter(WarmupLog.account_id == account.id).order_by(WarmupLog.day).all()
    )

    if payload.on:
        _require_connected(db, account)
        # В пользовательском интерфейсе нет ручных лимитов: один переключатель
        # всегда приводит слот к согласованному автоматическому профилю.
        account.daily_send_limit = AUTO_DAILY_SEND_LIMIT
        account.delay_min_seconds = AUTO_DELAY_MIN_SECONDS
        account.delay_max_seconds = AUTO_DELAY_MAX_SECONDS
        account.status = "active"
        if not account.started_on:
            account.started_on = date_type.today()
            _fill_dates(logs, account.started_on)
        elif logs and not logs[0].date:
            _fill_dates(logs, account.started_on)
    else:
        account.status = "paused"

    db.commit()
    db.refresh(account)
    log_event(
        db,
        user.id,
        "warmup",
        f"Прогрев «{account.label}» " + ("включён" if payload.on else "поставлен на паузу"),
        "info",
        "warmup",
        account.id,
    )
    return _enrich(db, account, logs)


@router.patch("/{account_id}/days/{day}", response_model=WarmupAccountOut)
def update_warmup_day(
    account_id: int,
    day: int,
    payload: WarmupDayIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> WarmupAccountOut:
    """Отметить фактический результат дня и сразу вернуть обновлённый план."""
    account = _owned_account(account_id, db, user)
    if day < 1 or day > int(account.plan_days or 0):
        raise HTTPException(404, "День плана не найден")
    log = (
        db.query(WarmupLog)
        .filter(WarmupLog.account_id == account.id, WarmupLog.day == day)
        .one_or_none()
    )
    if not log:
        raise HTTPException(404, "День плана не найден")
    if payload.done_messages is not None:
        log.done_messages = max(0, min(1000, payload.done_messages))
    if payload.note is not None:
        log.note = payload.note[:1000]
    if payload.done is not None:
        log.done = payload.done
        log.completed_at = datetime.now(UTC) if payload.done else None
    if all(row.done for row in account.logs):
        account.status = "done"
    elif account.status == "done":
        account.status = "active"
    db.commit()
    db.refresh(account)
    return _enrich(db, account)
