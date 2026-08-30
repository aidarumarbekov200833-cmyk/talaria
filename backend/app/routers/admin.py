"""Админ-панель: сводка по всем пользователям.

Доступ только у Telegram ID из ADMIN_TELEGRAM_IDS. Здесь намеренно не отдаётся ничего секретного:
ни токенов, ни значений API-ключей — только счётчики, публичный профиль и
факт наличия ключей. Так админ видит картину, но не может выкрасть доступы.
"""

from __future__ import annotations

import csv
import io
import threading
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import func, or_
from sqlalchemy.orm import Query as OrmQuery
from sqlalchemy.orm import Session

from .. import alerts
from ..access import (
    is_whitelisted,
    normalize_username,
    registration_open,
    set_registration_open,
)
from ..config import settings
from ..database import get_db
from ..dependencies import require_admin
from ..models import (
    AccessWhitelist,
    ApiKey,
    Campaign,
    EventLog,
    Lead,
    LeadDatabase,
    LoginAttempt,
    Offer,
    Payment,
    PromoCode,
    Subscription,
    User,
    WarmupAccount,
)
from ..plans import (
    NO_PLAN,
    TRIAL_PLAN,
    activate_plan,
    effective_plan_slug,
    get_plan,
    get_subscription,
    is_expired,
    is_first_purchase,
    leads_this_month,
)
from ..schemas import (
    AdminActivateIn,
    AdminAuditListOut,
    AdminAuditOut,
    AdminBlockIn,
    AdminChurnOut,
    AdminDatabaseOut,
    AdminDayPointOut,
    AdminGrantDaysAllIn,
    AdminGrantDaysIn,
    AdminGrantDaysResultOut,
    AdminLoginAttemptOut,
    AdminNotificationIn,
    AdminNotificationOut,
    AdminOverviewOut,
    AdminPaymentIn,
    AdminPaymentListOut,
    AdminPaymentOut,
    AdminPromoIn,
    AdminPromoOut,
    AdminPromoPatchIn,
    AdminRecomputeScoreOut,
    AdminRegistrationIn,
    AdminRevenueOut,
    AdminUserDetailOut,
    AdminUserOut,
    AdminUsersOut,
    AdminViewAsOut,
    AdminVisitorOut,
    AdminWhitelistAddIn,
    AdminWhitelistListOut,
    AdminWhitelistOut,
    EventLogOut,
    OkOut,
)
from ..security import create_view_token
from ..services import compute_lead_score, get_settings, log_event
from ..services import payments as payments_service
from ..services import promo as promo_service
from ..services.plans import (
    activate_subscription_for_admin,
    grant_days,
    grant_days_to_everyone,
    grant_message,
)

# Сколько живёт доступ «смотреть глазами пользователя». Полчаса хватает на
# разбор обращения, а забытая вкладка сама перестаёт открывать чужой кабинет.
VIEW_AS_MINUTES = 30

router = APIRouter(prefix="/api/admin", tags=["admin"])

# require_admin переехал в dependencies.py — он нужен и вне админки (настройки
# ИИ). Здесь оставляем реэкспорт для старых импортов из этого модуля.
__all__ = ["router", "require_admin"]


# Потолок списков в карточке пользователя. Раньше базы, офферы и кампании
# тянулись целиком: у активного пользователя это сотни строк в одном ответе,
# которые в интерфейсе всё равно не помещаются.
MAX_DETAIL_ITEMS = 100


def _push_announcement(rows: list[tuple[int, int]], message: str) -> None:
    for user_id, telegram_id in rows:
        alerts.notify_user(
            telegram_id,
            "Уведомление Talaria\n\n" + message,
            dedupe_key=f"admin-notice:{user_id}:{hash(message)}",
        )


@router.post("/notifications", response_model=AdminNotificationOut)
def send_admin_notification(
    payload: AdminNotificationIn,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> AdminNotificationOut:
    """Объявление внутри сайта с необязательным дублем через Telegram-бота."""
    query = db.query(User)
    if payload.target == "user":
        query = query.filter(User.id == payload.user_id)
    elif payload.target == "active":
        active_from = datetime.now(UTC) - timedelta(days=30)
        active_ids = db.query(EventLog.owner_id).filter(EventLog.created_at >= active_from)
        query = query.filter(User.id.in_(active_ids))
    users = query.order_by(User.id).limit(10_000).all()
    if not users:
        raise HTTPException(404, "Получатели не найдены")

    message = payload.message.strip()
    for user in users:
        db.add(
            EventLog(
                owner_id=user.id,
                scope="notification",
                level=payload.level,
                message=f"Сообщение администратора: {message}"[:2000],
                ref_type="admin",
                ref_id=admin.id,
            )
        )
    db.commit()

    telegram_rows = [(user.id, user.telegram_id) for user in users if user.telegram_id]
    if payload.telegram and telegram_rows:
        threading.Thread(
            target=_push_announcement,
            args=(telegram_rows, message),
            name=f"admin-notice-{admin.id}",
            daemon=True,
        ).start()
    log_event(
        db,
        admin.id,
        "admin",
        f"Уведомление отправлено: {len(users)} получателям"
        + (", Telegram включён" if payload.telegram else ""),
        "success",
    )
    return AdminNotificationOut(
        recipients=len(users),
        in_app=len(users),
        telegram_queued=len(telegram_rows) if payload.telegram else 0,
    )


def _count(db: Session, model, owner_id: int) -> int:
    return db.query(func.count(model.id)).filter(model.owner_id == owner_id).scalar() or 0


def _attempt_out(a: LoginAttempt) -> AdminLoginAttemptOut:
    return AdminLoginAttemptOut(
        id=a.id,
        telegram_id=a.telegram_id,
        username=a.username or "",
        name=f"{a.first_name or ''} {a.last_name or ''}".strip(),
        ok=bool(a.ok),
        reason=a.reason or "",
        ip=a.ip or "",
        user_agent=a.user_agent or "",
        created_at=a.created_at,
    )


# CSV-экспорт журналов: тот же формат, что и экспорт лидов в routers/leads.py —
# точка с запятой, BOM для Excel, потолок строк.
EXPORT_CSV_DELIMITER = ";"
MAX_EXPORT_ROWS = 50_000


def _csv_safe(value: object) -> object:
    """Нейтрализовать формулы в данных, открываемых через Excel."""
    if isinstance(value, str) and value.lstrip().startswith(("=", "+", "-", "@")):
        return "'" + value
    return value


def _csv_response(columns: list[str], rows: list[list], filename: str) -> Response:
    buffer = io.StringIO()
    writer = csv.writer(buffer, delimiter=EXPORT_CSV_DELIMITER, lineterminator="\r\n")
    writer.writerow(columns)
    for row in rows:
        writer.writerow([_csv_safe(value) for value in row])
    body = ("\ufeff" + buffer.getvalue()).encode("utf-8")
    return Response(
        content=body,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def _bulk_counts(db: Session, model, user_ids: list[int]) -> dict[int, int]:
    """Сколько строк модели у каждого владельца — одним запросом."""
    if not user_ids:
        return {}
    return {
        owner_id: int(count)
        for owner_id, count in db.query(model.owner_id, func.count(model.id))
        .filter(model.owner_id.in_(user_ids))
        .group_by(model.owner_id)
        .all()
    }


def _bulk_leads_counts(db: Session, user_ids: list[int]) -> dict[int, int]:
    """Число лидов по владельцам — одним запросом через базы лидов."""
    if not user_ids:
        return {}
    return {
        owner_id: int(count)
        for owner_id, count in db.query(LeadDatabase.owner_id, func.count(Lead.id))
        .join(Lead, Lead.database_id == LeadDatabase.id)
        .filter(LeadDatabase.owner_id.in_(user_ids))
        .group_by(LeadDatabase.owner_id)
        .all()
    }


def _leads_count(db: Session, owner_id: int) -> int:
    return (
        db.query(func.count(Lead.id))
        .join(LeadDatabase, Lead.database_id == LeadDatabase.id)
        .filter(LeadDatabase.owner_id == owner_id)
        .scalar()
        or 0
    )


_USER_SCOPES = ("all", "trial", "paid", "expired", "blocked", "flagged")


def _apply_scope(db: Session, query: OrmQuery, scope: str) -> OrmQuery:
    """Сузить список пользователей до одной группы.

    Фильтруем по тому, что записано в подписке, а срок учитываем отдельным
    условием на paid_until: считать «истёк» на стороне Python пришлось бы уже
    после выборки, и постраничная навигация перестала бы сходиться с итогом.
    """
    if scope == "all":
        return query
    now = datetime.now(UTC)
    joined = query.outerjoin(Subscription, Subscription.owner_id == User.id)
    if scope == "blocked":
        return joined.filter(Subscription.blocked.is_(True))
    if scope == "trial":
        return joined.filter(
            Subscription.plan == TRIAL_PLAN,
            Subscription.blocked.is_(False),
            or_(Subscription.paid_until.is_(None), Subscription.paid_until >= now),
        )
    if scope == "paid":
        return joined.filter(
            Subscription.plan.notin_([NO_PLAN, TRIAL_PLAN, "free", ""]),
            Subscription.blocked.is_(False),
            or_(Subscription.paid_until.is_(None), Subscription.paid_until >= now),
        )
    if scope == "expired":
        return joined.filter(
            Subscription.paid_until.isnot(None),
            Subscription.paid_until < now,
        )
    if scope == "flagged":
        # «Похоже на бота» на стороне базы — это отсутствие имени и аватара
        # одновременно. Остальные признаки считаются по журналу входов уже
        # после выборки и в фильтр не годятся.
        return joined.filter(
            or_(User.username.is_(None), User.username == ""),
            or_(User.photo_url.is_(None), User.photo_url == ""),
        )
    return query


def _scope_counts(db: Session, term: str) -> dict[str, int]:
    """Сколько человек в каждой группе — числа на кнопках фильтра."""
    counts: dict[str, int] = {}
    for name in _USER_SCOPES:
        base = db.query(User)
        if term:
            like = f"%{term}%"
            base = base.filter(
                or_(
                    User.username.ilike(like),
                    User.first_name.ilike(like),
                    User.last_name.ilike(like),
                )
            )
        counts[name] = _apply_scope(db, base, name).count()
    return counts


def _last_logins(db: Session, telegram_ids: list[int]) -> dict[int, tuple[datetime, str]]:
    """Последний успешный вход по каждому telegram_id: когда и с какого адреса.

    Одним запросом на всю страницу: построчные запросы превращали список из
    пятидесяти человек в пятьдесят обращений к журналу.
    """
    if not telegram_ids:
        return {}
    rows = (
        db.query(LoginAttempt.telegram_id, LoginAttempt.created_at, LoginAttempt.ip)
        .filter(
            LoginAttempt.telegram_id.in_(telegram_ids),
            LoginAttempt.ok.is_(True),
        )
        .order_by(LoginAttempt.telegram_id, LoginAttempt.created_at.desc())
        .all()
    )
    latest: dict[int, tuple[datetime, str]] = {}
    for telegram_id, created_at, ip in rows:
        if telegram_id is not None and telegram_id not in latest:
            latest[telegram_id] = (created_at, ip or "")
    return latest


def _shared_ip_counts(db: Session, ips: list[str]) -> dict[str, int]:
    """Сколько РАЗНЫХ аккаунтов входило с каждого адреса.

    Общий Wi-Fi даёт ложные срабатывания, поэтому это не блокировка, а подсказка
    админу: «посмотрите на этих троих внимательнее».
    """
    clean = [ip for ip in {i for i in ips if i}]
    if not clean:
        return {}
    rows = (
        db.query(LoginAttempt.ip, func.count(func.distinct(LoginAttempt.telegram_id)))
        .filter(LoginAttempt.ip.in_(clean), LoginAttempt.ok.is_(True))
        .group_by(LoginAttempt.ip)
        .all()
    )
    return {ip: int(count or 0) for ip, count in rows}


def _user_flags(row: AdminUserOut, *, shared_ip: int) -> list[str]:
    """Человеческие подсказки вместо сырых цифр.

    Ни один признак сам по себе не приговор: у живого человека может не быть
    username, а сосед по офису сидит за тем же NAT. Признаки нужны, чтобы было
    с чего начать разбор, а решение принимает админ.
    """
    flags: list[str] = []
    if not row.username:
        flags.append("без username")
    if not row.photo_url:
        flags.append("без аватара")
    if shared_ip > 1:
        flags.append(f"IP как ещё у {shared_ip - 1}")
    # «Нет активности» имеет смысл только для тех, кто ничего не платил: у
    # платящего пустая статистика — это вопрос к продукту, а не к аккаунту.
    if row.plan in (NO_PLAN, TRIAL_PLAN) and not (
        row.databases or row.leads or row.offers or row.campaigns
    ):
        flags.append("нет активности")
    return flags


def _row(db: Session, u: User, counts: dict[str, dict[int, int]] | None = None) -> AdminUserOut:
    """Строка списка пользователей.

    Если передан counts (предварительно посчитанные групповые счётчики), свои
    запросы не делаются. Без него поведение прежнее — это важно для карточки
    одного пользователя, где групповые запросы смысла не имеют.
    """
    sub = get_subscription(db, u.id)
    effective = effective_plan_slug(db, u.id)

    def _n(key: str, model) -> int:
        if counts is not None:
            return counts[key].get(u.id, 0)
        return _count(db, model, u.id)

    return AdminUserOut(
        id=u.id,
        telegram_id=u.telegram_id,
        username=u.username,
        name=u.display_name,
        photo_url=u.photo_url,
        created_at=u.created_at,
        is_admin=settings.is_admin(u.username, u.telegram_id),
        databases=_n("databases", LeadDatabase),
        leads=(counts["leads"].get(u.id, 0) if counts is not None else _leads_count(db, u.id)),
        offers=_n("offers", Offer),
        campaigns=_n("campaigns", Campaign),
        warmup_accounts=_n("warmup_accounts", WarmupAccount),
        api_keys=_n("api_keys", ApiKey),
        plan=effective,
        plan_name=get_plan(effective)["name"],
        sub_status=sub.status or "active",
        blocked=bool(sub.blocked),
        paid_until=sub.paid_until,
        expired=is_expired(db, u.id),
    )


@router.get("/users", response_model=AdminUsersOut)
def list_users(
    _: User = Depends(require_admin),
    db: Session = Depends(get_db),
    q: str = Query("", description="поиск по username/имени"),
    scope: str = Query("all", description="all | trial | paid | expired | blocked | flagged"),
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
) -> AdminUsersOut:
    base = db.query(User)
    term = (q or "").strip()
    if term:
        like = f"%{term}%"
        # Поиск по числу — это поиск по telegram_id: админ обычно копирует
        # именно его из журнала входов, а не имя.
        conditions = [
            User.username.ilike(like),
            User.first_name.ilike(like),
            User.last_name.ilike(like),
        ]
        digits = term.lstrip("@").strip()
        if digits.isdigit():
            conditions.append(User.telegram_id == int(digits))
        base = base.filter(or_(*conditions))

    scope = (scope or "all").strip().lower()
    if scope not in _USER_SCOPES:
        scope = "all"
    base = _apply_scope(db, base, scope)

    total = base.count()
    # Новые сверху: с открытой регистрацией самое интересное — те, кто пришёл
    # только что, а по возрастанию id они оказывались на последней странице.
    users = base.order_by(User.id.desc()).offset(offset).limit(limit).all()

    # Счётчики сразу по всей странице: раньше каждая строка делала шесть
    # отдельных COUNT-запросов, то есть страница из 50 человек — триста запросов
    # только на циферки. Теперь шесть запросов на всю страницу.
    user_ids = [u.id for u in users]
    counts = {
        "databases": _bulk_counts(db, LeadDatabase, user_ids),
        "offers": _bulk_counts(db, Offer, user_ids),
        "campaigns": _bulk_counts(db, Campaign, user_ids),
        "warmup_accounts": _bulk_counts(db, WarmupAccount, user_ids),
        "api_keys": _bulk_counts(db, ApiKey, user_ids),
        "leads": _bulk_leads_counts(db, user_ids),
    }
    rows = [_row(db, u, counts) for u in users]

    # Кто это и откуда: последний вход, адрес, подсказки о подозрительном.
    logins = _last_logins(db, [u.telegram_id for u in users])
    shared = _shared_ip_counts(db, [ip for _when, ip in logins.values()])
    for user, row in zip(users, rows, strict=True):
        seen = logins.get(user.telegram_id)
        if seen:
            row.last_login_at, row.last_ip = seen
        row.flags = _user_flags(row, shared_ip=shared.get(row.last_ip, 0))

    return AdminUsersOut(
        users=rows,
        total_users=total,
        total_leads=sum(r.leads for r in rows),
        offset=offset,
        limit=limit,
        query=term,
        scope=scope,
        counts=_scope_counts(db, term),
        open_registration=registration_open(db),
    )


@router.get("/users/{user_id}", response_model=AdminUserDetailOut)
def user_detail(
    user_id: int,
    _: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> AdminUserDetailOut:
    u = db.get(User, user_id)
    if not u:
        raise HTTPException(404, "Пользователь не найден")

    dbs = (
        db.query(LeadDatabase)
        .filter(LeadDatabase.owner_id == user_id)
        .order_by(LeadDatabase.id.desc())
        .limit(MAX_DETAIL_ITEMS)
        .all()
    )
    # Число лидов по всем базам одним запросом вместо запроса на каждую базу:
    # у человека с тридцатью базами карточка делала тридцать лишних запросов.
    db_ids = [d.id for d in dbs]
    lead_counts: dict[int, int] = {}
    if db_ids:
        lead_counts = {
            db_id: int(count)
            for db_id, count in db.query(Lead.database_id, func.count(Lead.id))
            .filter(Lead.database_id.in_(db_ids))
            .group_by(Lead.database_id)
            .all()
        }
    databases_list = [
        AdminDatabaseOut(
            id=d.id,
            name=d.name,
            source=d.source,
            mode=d.mode,
            leads=lead_counts.get(d.id, 0),
            created_at=d.created_at,
        )
        for d in dbs
    ]

    status_rows = (
        db.query(Lead.status, func.count(Lead.id))
        .join(LeadDatabase, Lead.database_id == LeadDatabase.id)
        .filter(LeadDatabase.owner_id == user_id)
        .group_by(Lead.status)
        .all()
    )
    leads_by_status = {status: count for status, count in status_rows}

    offers_list = [
        o.name
        for o in db.query(Offer)
        .filter(Offer.owner_id == user_id)
        .order_by(Offer.id.desc())
        .limit(MAX_DETAIL_ITEMS)
        .all()
    ]
    campaigns_list = [
        c.name
        for c in db.query(Campaign)
        .filter(Campaign.owner_id == user_id)
        .order_by(Campaign.id.desc())
        .limit(MAX_DETAIL_ITEMS)
        .all()
    ]

    app_settings = get_settings(db, user_id)
    events = (
        db.query(EventLog)
        .filter(EventLog.owner_id == user_id)
        .order_by(EventLog.id.desc())
        .limit(20)
        .all()
    )

    # Последние попытки входа этого человека — чтобы видеть, с каких IP и
    # устройств он заходит (и ловить мультиаккаунты по совпадению с другими).
    attempts = (
        db.query(LoginAttempt)
        .filter(
            or_(
                LoginAttempt.owner_id == user_id,
                LoginAttempt.telegram_id == u.telegram_id,
            )
        )
        .order_by(LoginAttempt.id.desc())
        .limit(20)
        .all()
    )

    sub = get_subscription(db, user_id)
    return AdminUserDetailOut(
        user=_row(db, u),
        databases_list=databases_list,
        leads_by_status=leads_by_status,
        offers_list=offers_list,
        campaigns_list=campaigns_list,
        monthly_limit=int(app_settings.monthly_limit or 0),
        monthly_used=int(app_settings.monthly_used or 0),
        recent_events=[EventLogOut.model_validate(e) for e in reversed(events)],
        login_attempts=[_attempt_out(a) for a in attempts],
        whitelisted=is_whitelisted(db, u.telegram_id, u.username),
        first_purchase=is_first_purchase(db, user_id),
        plan=effective_plan_slug(db, user_id),
        stored_plan=sub.plan or NO_PLAN,
        sub_status=sub.status or "none",
        blocked=bool(sub.blocked),
        paid_until=sub.paid_until,
        sub_note=sub.note or "",
        leads_this_month=leads_this_month(db, user_id),
    )


@router.post("/users/{user_id}/subscription", response_model=AdminUserDetailOut)
def activate_subscription(
    user_id: int,
    payload: AdminActivateIn,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> AdminUserDetailOut:
    """Активировать/продлить купленный план после оплаты в личку.

    Это и есть «белый список»: оплата прошла вне системы, админ вручную ставит
    план и срок. Пишем строку в журнал пользователя, чтобы решение осталось в
    аудите.
    """
    activate_subscription_for_admin(
        db,
        user_id,
        payload.plan,
        payload.months,
        admin.id,
        payload.note,
        activate_plan_fn=activate_plan,
        get_plan_fn=get_plan,
        log_event_fn=log_event,
    )
    return user_detail(user_id, admin, db)


@router.post("/users/{user_id}/grant-days", response_model=AdminUserDetailOut)
def grant_days_to_user(
    user_id: int,
    payload: AdminGrantDaysIn,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> AdminUserDetailOut:
    """Добавить или снять дни доступа одному пользователю.

    Отдельно от активации тарифа: продлить на пару дней в качестве компенсации
    и «активировать тариф на месяц» — разные по смыслу действия, и мешать их в
    одну кнопку значит регулярно ошибаться месяцем.
    """
    if db.get(User, user_id) is None:
        raise HTTPException(404, "Пользователь не найден")
    grant_days(
        db,
        user_id,
        payload.days,
        admin.id,
        payload.note,
        get_settings_fn=get_settings,
    )
    log_event(
        db,
        owner_id=user_id,
        scope="settings",
        message=grant_message(payload.days, payload.note),
        level="success",
        ref_type="subscription",
    )
    return user_detail(user_id, admin, db)


@router.post("/grant-days", response_model=AdminGrantDaysResultOut)
def grant_days_to_all(
    payload: AdminGrantDaysAllIn,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> AdminGrantDaysResultOut:
    """Выдать дни всем сразу — компенсация за простой и подобное."""
    touched = grant_days_to_everyone(
        db,
        payload.days,
        admin.id,
        payload.note,
        only_with_plan=payload.only_with_plan,
        get_settings_fn=get_settings,
        log_event_fn=log_event,
    )
    scope = "клиентам с тарифом" if payload.only_with_plan else "всем пользователям"
    return AdminGrantDaysResultOut(
        affected=len(touched),
        days=payload.days,
        message=f"{grant_message(payload.days, payload.note)} — {scope} ({len(touched)})",
    )


# ------------------------------- промокоды ---------------------------------


def _promo_out(row: PromoCode) -> AdminPromoOut:
    out = AdminPromoOut.model_validate(row)
    out.usable = row.usable_at(datetime.now(UTC))
    return out


@router.get("/promo-codes", response_model=list[AdminPromoOut])
def list_promo_codes(
    _: User = Depends(require_admin), db: Session = Depends(get_db)
) -> list[AdminPromoOut]:
    return [_promo_out(row) for row in promo_service.list_codes(db)]


@router.post("/promo-codes", response_model=AdminPromoOut, status_code=201)
def create_promo_code(
    payload: AdminPromoIn,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> AdminPromoOut:
    """Завести промокод. Раньше для этого требовался деплой."""
    row = promo_service.create_code(
        db,
        code=payload.code,
        discount_pct=payload.discount_pct,
        valid_until=payload.valid_until,
        max_uses=payload.max_uses,
        note=payload.note,
        admin_id=admin.id,
    )
    return _promo_out(row)


@router.patch("/promo-codes/{code_id}", response_model=AdminPromoOut)
def update_promo_code(
    code_id: int,
    payload: AdminPromoPatchIn,
    _: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> AdminPromoOut:
    row = promo_service.update_code(
        db,
        code_id,
        discount_pct=payload.discount_pct,
        active=payload.active,
        valid_until=payload.valid_until,
        max_uses=payload.max_uses,
        note=payload.note,
    )
    return _promo_out(row)


@router.delete("/promo-codes/{code_id}", response_model=OkOut)
def delete_promo_code(
    code_id: int, _: User = Depends(require_admin), db: Session = Depends(get_db)
) -> OkOut:
    promo_service.delete_code(db, code_id)
    return OkOut(ok=True)


@router.post("/users/{user_id}/view-as", response_model=AdminViewAsOut)
def view_as_user(
    user_id: int,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> AdminViewAsOut:
    """Открыть чужой кабинет глазами владельца — только чтение.

    Половина обращений в поддержку звучит как «у меня ничего не работает», и
    без возможности посмотреть тот же экран разбор превращается в переписку
    скриншотами. Поэтому просмотр есть, но с тремя ограничениями:

      1. Только чтение — запрет на изменения стоит в middleware, а не в ручках,
         чтобы новая забытая ручка не сделала его неправдой.
      2. Короткий срок — забытая вкладка перестаёт быть доступом сама.
      3. Запись в журнал ОБОИМ: и админу, и владельцу кабинета. Тихий просмотр
         чужой переписки и чужих клиентов — не поддержка, а слежка.
    """
    if user_id == admin.id:
        raise HTTPException(400, "Это ваш собственный кабинет")
    target = db.get(User, user_id)
    if not target:
        raise HTTPException(404, "Пользователь не найден")

    token, _jti = create_view_token(admin.id, target.id, ttl_minutes=VIEW_AS_MINUTES)
    who = admin.username or f"id{admin.id}"
    log_event(
        db,
        owner_id=target.id,
        scope="security",
        message=f"Поддержка открыла ваш кабинет для просмотра (только чтение), админ @{who}",
        level="warn",
    )
    log_event(
        db,
        owner_id=admin.id,
        scope="admin",
        message=f"Просмотр кабинета пользователя {target.display_name} (id {target.id})",
        level="warn",
    )
    return AdminViewAsOut(
        access_token=token,
        expires_in=VIEW_AS_MINUTES * 60,
        user_name=target.display_name,
    )


@router.post("/users/{user_id}/block", response_model=AdminUserDetailOut)
def set_block(
    user_id: int,
    payload: AdminBlockIn,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> AdminUserDetailOut:
    """Заблокировать/разблокировать доступ пользователю.

    Заблокированный пользователь получает 403 в get_current_user на любом
    защищённом эндпоинте. Себя заблокировать нельзя — иначе админ закроет себе
    же вход.
    """
    if user_id == admin.id:
        raise HTTPException(400, "Нельзя заблокировать самого себя")
    u = db.get(User, user_id)
    if not u:
        raise HTTPException(404, "Пользователь не найден")

    sub = get_subscription(db, user_id)
    sub.blocked = bool(payload.blocked)
    if payload.note:
        sub.note = payload.note
    db.commit()
    log_event(
        db,
        owner_id=user_id,
        scope="settings",
        message=("Админ заблокировал доступ" if payload.blocked else "Админ снял блокировку")
        + (f" — {payload.note}" if payload.note else ""),
        level="warn" if payload.blocked else "info",
        ref_type="subscription",
        ref_id=sub.id,
    )
    return user_detail(user_id, admin, db)


# ------------------------------ белый список ------------------------------


def _whitelist_entries(db: Session) -> list[AdminWhitelistOut]:
    """Весь белый список с именами тех, кто уже заходил.

    Имена подтягиваются одним запросом: раньше на каждую строку списка шёл
    отдельный запрос к users. По одному username в списке админу трудно
    вспомнить, кто это, поэтому имя стоит лишнего запроса.
    """
    rows = db.query(AccessWhitelist).order_by(AccessWhitelist.id.desc()).all()

    tg_ids = [w.telegram_id for w in rows if w.telegram_id is not None]
    names: dict[int, str] = {}
    if tg_ids:
        names = {
            u.telegram_id: u.display_name
            for u in db.query(User).filter(User.telegram_id.in_(tg_ids)).all()
        }

    return [
        AdminWhitelistOut(
            id=w.id,
            telegram_id=w.telegram_id,
            username=w.username or "",
            name=names.get(w.telegram_id, "") if w.telegram_id is not None else "",
            note=w.note or "",
            created_at=w.created_at,
        )
        for w in rows
    ]


@router.post("/registration", response_model=AdminUsersOut)
def toggle_registration(
    payload: AdminRegistrationIn,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> AdminUsersOut:
    """Открыть или закрыть самостоятельную регистрацию.

    Отвечаем тем же телом, что и список пользователей: интерфейсу после
    переключения нужен свежий список, а не только флаг, и лишний запрос из-за
    этого не нужен.
    """
    set_registration_open(db, bool(payload.open), admin_id=admin.id)
    log_event(
        db,
        admin.id,
        "admin",
        "Регистрация открыта" if payload.open else "Регистрация закрыта",
        "warn",
    )
    # Значения передаём явно: у параметров функции значения по умолчанию —
    # объекты Query, и прямой вызов без аргументов подсунул бы их в запрос.
    return list_users(_=admin, db=db, q="", scope="all", offset=0, limit=50)


@router.get("/whitelist", response_model=AdminWhitelistListOut)
def list_whitelist(
    _: User = Depends(require_admin), db: Session = Depends(get_db)
) -> AdminWhitelistListOut:
    return AdminWhitelistListOut(entries=_whitelist_entries(db))


@router.post("/whitelist", response_model=AdminWhitelistListOut, status_code=201)
def add_whitelist(
    payload: AdminWhitelistAddIn,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> AdminWhitelistListOut:
    """Добавить доступ по telegram_id и/или username.

    Хотя бы одно поле обязательно. По username удобно пускать того, кто ещё ни
    разу не входил (id неизвестен); telegram_id надёжнее — его нельзя сменить.
    """
    tg_id = payload.telegram_id
    uname = normalize_username(payload.username)
    if tg_id is None and not uname:
        raise HTTPException(400, "Укажите telegram_id или username")

    # Не плодим дубли: и по id, и по имени.
    exists = None
    if tg_id is not None:
        exists = db.query(AccessWhitelist).filter(AccessWhitelist.telegram_id == tg_id).first()
    if exists is None and uname:
        exists = db.query(AccessWhitelist).filter(AccessWhitelist.username == uname).first()
    if exists is not None:
        raise HTTPException(409, "Этот аккаунт уже в списке допущенных")

    db.add(
        AccessWhitelist(
            telegram_id=tg_id,
            username=uname or None,
            note=payload.note or "",
            added_by=admin.id,
        )
    )
    db.commit()
    return AdminWhitelistListOut(entries=_whitelist_entries(db))


@router.delete("/whitelist/{entry_id}", response_model=OkOut)
def remove_whitelist(
    entry_id: int,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> OkOut:
    """Убрать из белого списка. Действующий токен человека перестаёт работать на
    следующем запросе — get_current_user тоже сверяется с белым списком."""
    w = db.get(AccessWhitelist, entry_id)
    if not w:
        raise HTTPException(404, "Запись не найдена")
    # Себя из списка убрать нельзя: админ проходит и без записи (settings.is_admin),
    # но на всякий случай не даём отстрелить единственный гарантированный доступ.
    if w.telegram_id == admin.telegram_id or normalize_username(w.username) == normalize_username(
        admin.username
    ):
        raise HTTPException(400, "Нельзя убрать из списка самого себя")
    db.delete(w)
    db.commit()
    return OkOut()


# ------------------------------ журнал входов ------------------------------


@router.get("/login-attempts", response_model=list[AdminLoginAttemptOut])
def list_login_attempts(
    _: User = Depends(require_admin),
    db: Session = Depends(get_db),
    only_failed: bool = Query(False, description="только отклонённые входы"),
    reason: str = Query("", description="not_whitelisted | blocked | hash_mismatch | ..."),
    ip: str = Query("", description="точное совпадение IP"),
    q: str = Query("", description="поиск по username и имени"),
    offset: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
) -> list[AdminLoginAttemptOut]:
    """Журнал попыток входа — кто и откуда пытался войти.

    По умолчанию отдаём и удачные, и отклонённые: для поиска мультиаккаунтов
    важны в том числе успешные входы с одного IP под разными аккаунтами.
    """
    query = db.query(LoginAttempt)
    if only_failed:
        query = query.filter(LoginAttempt.ok.is_(False))
    if reason.strip():
        query = query.filter(LoginAttempt.reason == reason.strip())
    if ip.strip():
        query = query.filter(LoginAttempt.ip == ip.strip())
    term = (q or "").strip()
    if term:
        like = f"%{term}%"
        query = query.filter(
            or_(
                LoginAttempt.username.ilike(like),
                LoginAttempt.first_name.ilike(like),
                LoginAttempt.last_name.ilike(like),
            )
        )
    rows = query.order_by(LoginAttempt.id.desc()).offset(offset).limit(limit).all()
    return [_attempt_out(a) for a in rows]


# --------------------------- журнал активности ---------------------------


def _activity_filtered(
    base: OrmQuery, user_id: int | None, scope: str, level: str, q: str
) -> OrmQuery:
    """Общий фильтр для /activity и /activity/export — один набор условий, чтобы
    экспорт всегда совпадал с тем, что видно на экране.

    outerjoin, а не join: события удалённого пользователя должны остаться видны.
    Журнал, из которого записи пропадают вместе с аккаунтом, бесполезен ровно
    тогда, когда он нужен.
    """
    base = base.outerjoin(User, EventLog.owner_id == User.id)
    if user_id is not None:
        base = base.filter(EventLog.owner_id == user_id)
    if scope.strip():
        base = base.filter(EventLog.scope == scope.strip())
    if level.strip():
        base = base.filter(EventLog.level == level.strip())
    term = (q or "").strip()
    if term:
        like = f"%{term}%"
        base = base.filter(or_(EventLog.message.ilike(like), User.username.ilike(like)))
    return base


@router.get("/activity", response_model=AdminAuditListOut)
def list_activity(
    _: User = Depends(require_admin),
    db: Session = Depends(get_db),
    user_id: int | None = Query(None, description="только один пользователь"),
    scope: str = Query(
        "", description="search | leads | campaigns | ai | settings | warmup | offers"
    ),
    level: str = Query("", description="info | success | warn | error"),
    q: str = Query("", description="поиск по тексту события и username"),
    offset: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
) -> AdminAuditListOut:
    """Общий журнал действий всех пользователей.

    Раньше события были видны только внутри карточки конкретного человека и
    только последние 20 штук — чтобы понять, что вообще происходит в системе,
    приходилось обходить всех по очереди.

    Фильтр level=error даёт ленту ошибок. Важная оговорка: сюда попадают только
    события, которые код записал через log_event, а не все исключения приложения.
    Настоящие падения с трейсбеками живут в логах контейнера и в Sentry — это
    журнал действий, а не замена мониторингу.
    """

    def _filtered(base: OrmQuery) -> OrmQuery:
        return _activity_filtered(base, user_id, scope, level, q)

    total = _filtered(db.query(func.count(EventLog.id))).scalar() or 0
    rows = (
        _filtered(db.query(EventLog, User))
        .order_by(EventLog.id.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )

    events = [
        AdminAuditOut(
            id=event.id,
            owner_id=event.owner_id,
            username=(owner.username or "") if owner else "",
            user_name=owner.display_name if owner else "",
            scope=event.scope,
            level=event.level,
            message=event.message,
            ref_type=event.ref_type,
            ref_id=event.ref_id,
            ip=event.ip or "",
            created_at=event.created_at,
        )
        for event, owner in rows
    ]
    return AdminAuditListOut(events=events, total=total, offset=offset, limit=limit)


@router.get("/activity/export")
def export_activity(
    _: User = Depends(require_admin),
    db: Session = Depends(get_db),
    user_id: int | None = Query(None),
    scope: str = Query(""),
    level: str = Query(""),
    q: str = Query(""),
) -> Response:
    """Выгрузить журнал активности в CSV с теми же фильтрами, что и /activity."""
    rows = (
        _activity_filtered(db.query(EventLog, User), user_id, scope, level, q)
        .order_by(EventLog.id.desc())
        .limit(MAX_EXPORT_ROWS)
        .all()
    )
    columns = [
        "id",
        "created_at",
        "owner_id",
        "username",
        "user_name",
        "scope",
        "level",
        "message",
        "ref_type",
        "ref_id",
    ]
    csv_rows = [
        [
            event.id,
            event.created_at.isoformat() if event.created_at else "",
            event.owner_id or "",
            (owner.username or "") if owner else "",
            owner.display_name if owner else "",
            event.scope or "",
            event.level or "",
            " ".join((event.message or "").split()),
            event.ref_type or "",
            event.ref_id or "",
        ]
        for event, owner in rows
    ]
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M")
    return _csv_response(columns, csv_rows, f"activity-{stamp}.csv")


@router.get("/login-attempts/export")
def export_login_attempts(
    _: User = Depends(require_admin),
    db: Session = Depends(get_db),
    only_failed: bool = Query(False),
    reason: str = Query(""),
    ip: str = Query(""),
    q: str = Query(""),
) -> Response:
    """Выгрузить журнал попыток входа в CSV с теми же фильтрами, что и /login-attempts."""
    query = db.query(LoginAttempt)
    if only_failed:
        query = query.filter(LoginAttempt.ok.is_(False))
    if reason.strip():
        query = query.filter(LoginAttempt.reason == reason.strip())
    if ip.strip():
        query = query.filter(LoginAttempt.ip == ip.strip())
    term = (q or "").strip()
    if term:
        like = f"%{term}%"
        query = query.filter(
            or_(
                LoginAttempt.username.ilike(like),
                LoginAttempt.first_name.ilike(like),
                LoginAttempt.last_name.ilike(like),
            )
        )
    rows = query.order_by(LoginAttempt.id.desc()).limit(MAX_EXPORT_ROWS).all()
    columns = [
        "id",
        "created_at",
        "telegram_id",
        "username",
        "name",
        "ok",
        "reason",
        "ip",
        "user_agent",
    ]
    csv_rows = [
        [
            a.id,
            a.created_at.isoformat() if a.created_at else "",
            a.telegram_id or "",
            a.username or "",
            f"{a.first_name or ''} {a.last_name or ''}".strip(),
            "1" if a.ok else "0",
            a.reason or "",
            a.ip or "",
            a.user_agent or "",
        ]
        for a in rows
    ]
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M")
    return _csv_response(columns, csv_rows, f"login-attempts-{stamp}.csv")


# --------------------------- пересчёт балла лидов ---------------------------


@router.post("/leads/recompute-score", response_model=AdminRecomputeScoreOut)
def recompute_lead_scores(
    _: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> AdminRecomputeScoreOut:
    """Пересчитать балл всех лидов по текущей формуле compute_lead_score.

    Нужно после изменения весов/шкалы формулы: без этого старые лиды так и
    остаются с баллом, считанным по старой формуле на момент импорта/поиска. Идёт
    пакетами с промежуточными commit, чтобы не держать одну огромную транзакцию
    открытой на всё время пересчёта.
    """
    total = 0
    updated = 0
    batch_size = 500
    query = db.query(Lead).order_by(Lead.id)
    last_id = 0
    while True:
        batch = query.filter(Lead.id > last_id).limit(batch_size).all()
        if not batch:
            break
        for lead in batch:
            total += 1
            last_id = lead.id
            new_score = compute_lead_score(
                lead.subscribers, lead.video_count, lead.view_count, lead.matched_keyword
            )
            if new_score != lead.score:
                lead.score = new_score
                updated += 1
        db.commit()

    return AdminRecomputeScoreOut(total=total, updated=updated)


# ---------- сводка для вкладки «Админка» ----------

ONLINE_WINDOW_MINUTES = 15
OVERVIEW_DAYS = 14


def _payment_out(payment: Payment, label: str = "") -> AdminPaymentOut:
    return AdminPaymentOut(
        id=payment.id,
        owner_id=payment.owner_id,
        amount=round(int(payment.amount_minor or 0) / 100, 2),
        currency=payment.currency,
        plan=payment.plan or "",
        months=int(payment.months or 0),
        promo_code=payment.promo_code or "",
        note=payment.note or "",
        paid_at=payment.paid_at,
        user_label=label,
    )


def _revenue_out(raw: dict) -> AdminRevenueOut:
    """Перевести минорные единицы в обычные только на границе ответа.

    Внутри всё считается целыми: копейки в плавающей точке не складываются
    без расхождений, а расхождение в отчёте о деньгах невозможно объяснить.
    """

    def to_major(bucket: dict[str, int]) -> dict[str, float]:
        return {code: round(value / 100, 2) for code, value in bucket.items()}

    return AdminRevenueOut(
        month=to_major(raw["month"]),
        last_30_days=to_major(raw["last_30_days"]),
        all_time=to_major(raw["all_time"]),
        payments_total=raw["payments_total"],
        paying_users_this_month=raw["paying_users_this_month"],
    )


@router.get("/payments", response_model=AdminPaymentListOut)
def list_payments(
    owner_id: int | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> AdminPaymentListOut:
    """Платежи и сводка выручки."""
    query = db.query(Payment, User).join(User, User.id == Payment.owner_id)
    if owner_id is not None:
        query = query.filter(Payment.owner_id == owner_id)
    total = query.count()
    rows = query.order_by(Payment.paid_at.desc(), Payment.id.desc()).offset(offset).limit(limit).all()
    return AdminPaymentListOut(
        payments=[_payment_out(payment, user.display_name or f"id {user.id}") for payment, user in rows],
        total=total,
        revenue=_revenue_out(payments_service.revenue_summary(db)),
    )


@router.post("/users/{user_id}/payments", response_model=AdminPaymentOut, status_code=201)
def add_payment(
    user_id: int,
    payload: AdminPaymentIn,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> AdminPaymentOut:
    """Записать полученные от пользователя деньги.

    Отдельно от активации тарифа: оплата и выдача доступа не всегда совпадают
    ни по времени, ни по сумме — бывает предоплата, доплата и возврат.
    """
    payment = payments_service.add_payment(
        db,
        user_id,
        # Округляем на входе один раз: если это делать в каждом клиенте,
        # рано или поздно копейка потеряется по-разному.
        amount_minor=round(payload.amount * 100),
        currency=payload.currency,
        admin_id=admin.id,
        plan=payload.plan,
        months=payload.months,
        promo_code=payload.promo_code,
        note=payload.note,
        paid_at=payload.paid_at,
    )
    log_event(
        db,
        admin.id,
        "admin",
        f"Записан платёж {payment.amount} {payment.currency} от пользователя {user_id}",
        "success",
    )
    return _payment_out(payment)


@router.delete("/payments/{payment_id}", response_model=OkOut)
def remove_payment(
    payment_id: int,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> OkOut:
    """Удалить ошибочно заведённый платёж."""
    payments_service.delete_payment(db, payment_id)
    log_event(db, admin.id, "admin", f"Удалён платёж #{payment_id}", "warning")
    return OkOut()


# Сколько дней молчания считать тревожным. Две недели: недельная пауза бывает
# у любого (отпуск, занятость), а две у человека с оплаченным тарифом — уже
# сигнал, что сервисом перестали пользоваться.
CHURN_SILENT_DAYS = 14


def _churn_risk(db: Session, now: datetime, limit: int = 20) -> list[AdminChurnOut]:
    """Кто оплатил, но перестал заходить.

    Считаем по последнему событию в журнале, а не по последнему входу: сессия
    живёт долго, и человек может неделями работать, ни разу не логинясь заново.
    Отсутствие событий — единственный честный признак, что сервисом не
    пользуются.
    """
    silent_before = now - timedelta(days=CHURN_SILENT_DAYS)
    last_seen = (
        db.query(EventLog.owner_id, func.max(EventLog.created_at).label("seen"))
        .group_by(EventLog.owner_id)
        .subquery()
    )
    rows = (
        db.query(User, Subscription, last_seen.c.seen)
        .join(Subscription, Subscription.owner_id == User.id)
        .outerjoin(last_seen, last_seen.c.owner_id == User.id)
        .filter(
            Subscription.paid_until.isnot(None),
            Subscription.paid_until > now,
            Subscription.blocked.is_(False),
            # Никогда не заходивший тоже попадает сюда: оплатил и не начал —
            # случай, требующий звонка ничуть не меньше.
            or_(last_seen.c.seen.is_(None), last_seen.c.seen < silent_before),
        )
        .order_by(last_seen.c.seen.asc().nullsfirst())
        .limit(limit)
        .all()
    )

    out: list[AdminChurnOut] = []
    for user, subscription, seen in rows:
        moment = seen.replace(tzinfo=UTC) if seen is not None and seen.tzinfo is None else seen
        days = int((now - moment).days) if moment is not None else -1
        out.append(
            AdminChurnOut(
                user_id=user.id,
                label=user.display_name or f"id {user.id}",
                plan=subscription.plan or "",
                paid_until=subscription.paid_until,
                last_seen=moment,
                days_silent=days,
            )
        )
    return out


@router.get("/overview", response_model=AdminOverviewOut)
def admin_overview(
    _: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> AdminOverviewOut:
    """Что сейчас происходит в сервисе: кто заходил, кто что делал, сколько чего.

    «Сейчас на сайте» считаем по действиям за последние 15 минут, а не по
    числу живых сессий: сессия живёт неделю и показывала бы онлайн там,
    где человек просто не нажал «Выйти».
    """
    now = datetime.now(UTC)
    day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    online_from = now - timedelta(minutes=ONLINE_WINDOW_MINUTES)
    week_ago = now - timedelta(days=7)
    period_start = day_start - timedelta(days=OVERVIEW_DAYS - 1)

    online_now = (
        db.query(func.count(func.distinct(EventLog.owner_id)))
        .filter(EventLog.created_at >= online_from)
        .scalar()
        or 0
    )
    logins_today = (
        db.query(func.count(LoginAttempt.id))
        .filter(LoginAttempt.created_at >= day_start, LoginAttempt.ok.is_(True))
        .scalar()
        or 0
    )
    failed_today = (
        db.query(func.count(LoginAttempt.id))
        .filter(LoginAttempt.created_at >= day_start, LoginAttempt.ok.is_(False))
        .scalar()
        or 0
    )
    events_today = (
        db.query(func.count(EventLog.id)).filter(EventLog.created_at >= day_start).scalar() or 0
    )
    errors_today = (
        db.query(func.count(EventLog.id))
        .filter(EventLog.created_at >= day_start, EventLog.level == "error")
        .scalar()
        or 0
    )
    users_total = db.query(func.count(User.id)).scalar() or 0
    users_new = db.query(func.count(User.id)).filter(User.created_at >= week_ago).scalar() or 0
    blocked_users = (
        db.query(func.count(Subscription.id)).filter(Subscription.blocked.is_(True)).scalar() or 0
    )
    active_subs = (
        db.query(func.count(Subscription.id))
        .filter(
            Subscription.plan.notin_(["none", "free", ""]),
            or_(Subscription.paid_until.is_(None), Subscription.paid_until >= now),
        )
        .scalar()
        or 0
    )

    # Последние визиты: берём и удачные, и отклонённые — админу важны оба
    # случая, именно чужие попытки и есть самое интересное в журнале.
    visitors = [
        AdminVisitorOut(
            user_id=a.owner_id,
            username=a.username or "",
            name=" ".join(x for x in [a.first_name or "", a.last_name or ""] if x).strip(),
            ip=a.ip or "",
            ok=bool(a.ok),
            reason=a.reason or "",
            at=a.created_at,
        )
        for a in db.query(LoginAttempt).order_by(LoginAttempt.created_at.desc()).limit(12).all()
    ]

    rows = (
        db.query(EventLog, User)
        .outerjoin(User, User.id == EventLog.owner_id)
        .order_by(EventLog.created_at.desc())
        .limit(15)
        .all()
    )
    actions = [
        AdminAuditOut(
            id=e.id,
            owner_id=e.owner_id,
            username=(u.username if u else "") or "",
            user_name=(u.display_name if u else "") or "",
            scope=e.scope,
            level=e.level,
            message=e.message,
            ref_type=e.ref_type,
            ref_id=e.ref_id,
            created_at=e.created_at,
        )
        for e, u in rows
    ]

    # График по дням: группируем в базе, а не тянем все строки в память.
    login_by_day = dict(
        db.query(func.date(LoginAttempt.created_at), func.count(LoginAttempt.id))
        .filter(LoginAttempt.created_at >= period_start, LoginAttempt.ok.is_(True))
        .group_by(func.date(LoginAttempt.created_at))
        .all()
    )
    events_by_day = dict(
        db.query(func.date(EventLog.created_at), func.count(EventLog.id))
        .filter(EventLog.created_at >= period_start)
        .group_by(func.date(EventLog.created_at))
        .all()
    )

    def _key(value) -> str:
        return value.isoformat() if hasattr(value, "isoformat") else str(value)

    logins_map = {_key(k): v for k, v in login_by_day.items()}
    events_map = {_key(k): v for k, v in events_by_day.items()}

    by_day: list[AdminDayPointOut] = []
    for i in range(OVERVIEW_DAYS):
        day = (period_start + timedelta(days=i)).date().isoformat()
        by_day.append(
            AdminDayPointOut(
                day=day,
                logins=int(logins_map.get(day, 0)),
                events=int(events_map.get(day, 0)),
            )
        )

    return AdminOverviewOut(
        online_now=online_now,
        logins_today=logins_today,
        failed_logins_today=failed_today,
        events_today=events_today,
        users_total=users_total,
        users_new_7d=users_new,
        active_subs=active_subs,
        blocked_users=blocked_users,
        leads_total=db.query(func.count(Lead.id)).scalar() or 0,
        offers_total=db.query(func.count(Offer.id)).scalar() or 0,
        campaigns_total=db.query(func.count(Campaign.id)).scalar() or 0,
        running_campaigns=(
            db.query(func.count(Campaign.id)).filter(Campaign.status == "running").scalar() or 0
        ),
        connected_accounts=(
            db.query(func.count(WarmupAccount.id))
            .filter(
                or_(
                    WarmupAccount.connected_at.isnot(None),
                    WarmupAccount.is_primary.is_(False)
                    & WarmupAccount.tg_id.isnot(None)
                    & (WarmupAccount.tg_id != ""),
                ),
                WarmupAccount.operational_status != "reauth",
            )
            .scalar()
            or 0
        ),
        pending_verifications=(
            db.query(func.count(Lead.id)).filter(Lead.tg_status == "pending").scalar() or 0
        ),
        errors_today=errors_today,
        recent_visitors=visitors,
        recent_actions=actions,
        by_day=by_day,
        revenue=_revenue_out(payments_service.revenue_summary(db, now=now)),
        churn_risk=_churn_risk(db, now),
    )
