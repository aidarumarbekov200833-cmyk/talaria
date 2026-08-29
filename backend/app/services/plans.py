"""Transport-neutral subscription, entitlement, and plan-usage policy."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..models import AppSettings, Subscription
from ..plan_catalog import LEGACY_PLAN_ALIASES, NO_PLAN, PLAN_ORDER, PLANS, TRIAL_PLAN
from ..repositories.plans import PlanRepository
from ..schemas import PlanOut, SubscriptionOut, UsageOut
from .errors import DomainError

RepositoryFactory = Callable[[Session], PlanRepository]
Clock = Callable[[], datetime]

_KIND_LABEL = {
    "databases": "баз лидов",
    "offers": "офферов",
    "campaigns": "рассылок",
    "warmup": "аккаунтов прогрева",
}
_KIND_COUNTER = {
    "databases": "count_databases",
    "offers": "count_offers",
    "campaigns": "count_campaigns",
    "warmup": "count_warmup",
}
_KIND_LIMIT = {
    "databases": "max_databases",
    "offers": "max_offers",
    "campaigns": "max_campaigns",
    "warmup": "max_warmup",
}
_NO_PLAN_HINT = "Тариф не активирован. Откройте раздел «Тариф» — после оплаты доступ откроется."

# Отказы на пробном тарифе объясняют причину, а не сообщают «лимит 0». Человек
# должен понять: это не поломка и не жадный счётчик, а граница пробного доступа.
_TRIAL_NO_SEND = (
    "На пробном тарифе отправка сообщений выключена. "
    "Искать лидов, собирать базы и готовить офферы можно без ограничений — "
    "рассылка в Telegram включается после оплаты тарифа Talaria. "
    "Так мы отвечаем за то, что уходит с ваших аккаунтов в чужие личные сообщения."
)
_TRIAL_NO_NEURO = (
    "На пробном тарифе нейрокомментинг недоступен: он тоже пишет от вашего имени "
    "в чужих чатах. Откроется после оплаты тарифа Talaria."
)
_TRIAL_NO_AI = (
    "ИИ-помощник доступен на платном тарифе. На пробном работают поиск лидов, "
    "проверка контактов и офферы."
)


def _repository(db: Session, factory: RepositoryFactory | None) -> PlanRepository:
    return (factory or PlanRepository)(db)


def _utcnow() -> datetime:
    return datetime.now(UTC)


def trial_days() -> int:
    """Длительность пробного доступа: настройка окружения, иначе значение по умолчанию."""
    from ..config import settings as app_settings
    from ..plan_catalog import TRIAL_DAYS

    return max(1, int(getattr(app_settings, "trial_days", TRIAL_DAYS) or TRIAL_DAYS))


def trial_expiry(*, now_fn: Clock = _utcnow) -> datetime:
    """Когда закончится пробный доступ, начатый сейчас."""
    return now_fn() + timedelta(days=trial_days())


def get_subscription(
    db: Session,
    owner_id: int,
    *,
    repository_factory: RepositoryFactory | None = None,
) -> Subscription:
    repository = _repository(db, repository_factory)
    row = repository.get_subscription(owner_id)
    if row is not None:
        return row

    row = Subscription(owner_id=owner_id, plan=NO_PLAN, status="none")
    try:
        repository.stage_subscription(row)
        db.commit()
    except IntegrityError:
        db.rollback()
        return repository.require_subscription(owner_id)
    except Exception:
        db.rollback()
        raise
    db.refresh(row)
    return row


def as_utc(value: datetime | None) -> datetime | None:
    if value is not None and value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value


def effective_plan_slug(
    db: Session,
    owner_id: int,
    *,
    get_subscription_fn: Callable[[Session, int], object] = get_subscription,
    as_utc_fn: Callable[[datetime | None], datetime | None] = as_utc,
    now_fn: Clock = _utcnow,
) -> str:
    sub = get_subscription_fn(db, owner_id)
    slug = (sub.plan or "").strip().lower()
    if slug in ("", "free"):
        return NO_PLAN
    if slug != NO_PLAN and sub.paid_until is not None:
        paid_until = as_utc_fn(sub.paid_until)
        if paid_until is not None and paid_until < now_fn():
            return NO_PLAN
    slug = LEGACY_PLAN_ALIASES.get(slug, slug)
    return slug if slug in PLANS else NO_PLAN


def is_blocked(
    db: Session,
    owner_id: int,
    *,
    get_subscription_fn: Callable[[Session, int], object] = get_subscription,
) -> bool:
    return bool(get_subscription_fn(db, owner_id).blocked)


def start_trial(
    db: Session,
    owner_id: int,
    *,
    get_subscription_fn: Callable[[Session, int], object] = get_subscription,
    now_fn: Clock = _utcnow,
) -> bool:
    """Выдать пробный доступ при первом входе. True — если выдали сейчас.

    Триал получает только тот, кто ещё ни разу ничего не имел: план «none» и
    пустой activated_at. Так повторный вход не продлевает пробный период по
    кругу, а истёкший триал (paid_until в прошлом) не выдаётся заново — иначе
    достаточно было бы выйти и войти, чтобы получить ещё две недели.
    """
    sub = get_subscription_fn(db, owner_id)
    slug = (sub.plan or "").strip().lower()
    if slug not in ("", "free", NO_PLAN) or sub.activated_at is not None:
        return False
    if sub.paid_until is not None:
        return False  # триал уже был и закончился
    sub.plan = TRIAL_PLAN
    sub.status = "trial"
    sub.paid_until = trial_expiry(now_fn=now_fn)
    db.commit()
    return True


def trial_state(
    db: Session,
    owner_id: int,
    *,
    effective_plan_slug_fn: Callable[[Session, int], str] = effective_plan_slug,
    get_subscription_fn: Callable[[Session, int], object] = get_subscription,
    now_fn: Clock = _utcnow,
) -> dict:
    """Состояние пробного доступа для интерфейса.

    Фронт не должен вычислять «сколько осталось» из дат — иначе часовой пояс
    браузера начнёт спорить с сервером и в плашке появятся разные числа.
    """
    slug = effective_plan_slug_fn(db, owner_id)
    sub = get_subscription_fn(db, owner_id)
    stored = (sub.plan or "").strip().lower()
    until = as_utc(sub.paid_until)
    now = now_fn()
    days_left = 0
    if slug == TRIAL_PLAN and until is not None and until > now:
        # Округляем вверх: пока остаток больше нуля, «осталось 0 дней» — ложь.
        days_left = (until - now).days + 1
    return {
        "is_trial": slug == TRIAL_PLAN,
        "trial_expired": stored == TRIAL_PLAN and slug == NO_PLAN,
        "days_left": days_left,
        "expires_at": until,
        "sending": bool(PLANS.get(slug, PLANS[NO_PLAN]).get("sending")),
    }


def sending_allowed(
    db: Session,
    owner_id: int,
    *,
    effective_plan_slug_fn: Callable[[Session, int], str] = effective_plan_slug,
    get_plan_fn: Callable[[str | None], dict] | None = None,
) -> bool:
    """Можно ли отправлять сообщения в Telegram на текущем тарифе."""
    if get_plan_fn is None:
        from ..plan_catalog import get_plan as get_plan_fn

    return bool(get_plan_fn(effective_plan_slug_fn(db, owner_id)).get("sending"))


def require_sending(
    db: Session,
    owner_id: int,
    *,
    effective_plan_slug_fn: Callable[[Session, int], str] = effective_plan_slug,
    get_plan_fn: Callable[[str | None], dict] | None = None,
) -> None:
    """Остановить действие, если тариф не разрешает отправку — с объяснением почему."""
    if get_plan_fn is None:
        from ..plan_catalog import get_plan as get_plan_fn

    plan = get_plan_fn(effective_plan_slug_fn(db, owner_id))
    if plan.get("sending"):
        return
    raise DomainError(402, _TRIAL_NO_SEND if plan["slug"] == TRIAL_PLAN else _NO_PLAN_HINT)


def count_owned(
    db: Session,
    model: object,
    owner_id: int,
    *,
    repository_factory: RepositoryFactory | None = None,
) -> int:
    return _repository(db, repository_factory).count_owned(model, owner_id)


def leads_this_month(
    db: Session,
    owner_id: int,
    *,
    repository_factory: RepositoryFactory | None = None,
    now_fn: Clock = _utcnow,
) -> int:
    now = now_fn()
    start = datetime(now.year, now.month, 1, tzinfo=UTC)
    return _repository(db, repository_factory).count_leads_since(owner_id, start)


def check_object_limit(
    db: Session,
    owner_id: int,
    kind: str,
    *,
    effective_plan_slug_fn: Callable[[Session, int], str] = effective_plan_slug,
    get_plan_fn: Callable[[str | None], dict] | None = None,
    repository_factory: RepositoryFactory | None = None,
) -> None:
    if get_plan_fn is None:
        from ..plan_catalog import get_plan as get_plan_fn

    plan = get_plan_fn(effective_plan_slug_fn(db, owner_id))
    if plan["slug"] == NO_PLAN:
        raise DomainError(402, _NO_PLAN_HINT)
    # На пробном тарифе часть разделов выключена целиком. «Лимит 0» выглядит
    # как поломка, поэтому отвечаем причиной, а не счётчиком.
    if plan["slug"] == TRIAL_PLAN and kind == "campaigns":
        raise DomainError(402, _TRIAL_NO_SEND)
    limit = plan[_KIND_LIMIT[kind]]
    if limit < 0:
        return
    repository = _repository(db, repository_factory)
    current = getattr(repository, _KIND_COUNTER[kind])(owner_id)
    if current >= limit:
        raise DomainError(
            402,
            f"Достигнут лимит тарифа «{plan['name']}»: {_KIND_LABEL[kind]} "
            f"не больше {limit}. Проверьте доступ в разделе «Тариф».",
        )


def check_leads_budget(
    db: Session,
    owner_id: int,
    want: int,
    *,
    effective_plan_slug_fn: Callable[[Session, int], str] = effective_plan_slug,
    get_plan_fn: Callable[[str | None], dict] | None = None,
    leads_this_month_fn: Callable[[Session, int], int] = leads_this_month,
) -> int:
    if get_plan_fn is None:
        from ..plan_catalog import get_plan as get_plan_fn

    plan = get_plan_fn(effective_plan_slug_fn(db, owner_id))
    if plan["slug"] == NO_PLAN:
        raise DomainError(402, _NO_PLAN_HINT)
    limit = plan["monthly_leads"]
    if limit < 0:
        return want
    used = leads_this_month_fn(db, owner_id)
    left = max(0, limit - used)
    if left <= 0:
        raise DomainError(
            402,
            f"Исчерпан месячный лимит тарифа «{plan['name']}»: {limit} лидов. "
            f"Лимит обновится в начале месяца или оплатите тариф выше.",
        )
    return min(want, left)


def is_first_purchase(
    db: Session,
    owner_id: int,
    *,
    get_subscription_fn: Callable[[Session, int], object] = get_subscription,
) -> bool:
    return get_subscription_fn(db, owner_id).activated_at is None


def ai_allowed(
    db: Session,
    owner_id: int,
    *,
    effective_plan_slug_fn: Callable[[Session, int], str] = effective_plan_slug,
    get_plan_fn: Callable[[str | None], dict] | None = None,
) -> bool:
    if get_plan_fn is None:
        from ..plan_catalog import get_plan as get_plan_fn

    return bool(get_plan_fn(effective_plan_slug_fn(db, owner_id))["ai"])


def ai_offer_allowed(
    db: Session,
    owner_id: int,
    *,
    effective_plan_slug_fn: Callable[[Session, int], str] = effective_plan_slug,
    get_plan_fn: Callable[[str | None], dict] | None = None,
) -> bool:
    if get_plan_fn is None:
        from ..plan_catalog import get_plan as get_plan_fn

    return bool(get_plan_fn(effective_plan_slug_fn(db, owner_id))["ai_offer"])


def neuro_allowed(
    db: Session,
    owner_id: int,
    *,
    effective_plan_slug_fn: Callable[[Session, int], str] = effective_plan_slug,
    get_plan_fn: Callable[[str | None], dict] | None = None,
) -> bool:
    """Доступен ли нейрокомментинг. Отдельная возможность, не часть рассылки."""
    if get_plan_fn is None:
        from ..plan_catalog import get_plan as get_plan_fn

    return bool(get_plan_fn(effective_plan_slug_fn(db, owner_id)).get("neuro_comment"))


def ai_offer_plan_names() -> str:
    names = [PLANS[slug]["name"] for slug in PLAN_ORDER if PLANS[slug]["ai_offer"]]
    return " или ".join(names) if names else ""


def campaign_limits(
    db: Session,
    owner_id: int,
    *,
    effective_plan_slug_fn: Callable[[Session, int], str] = effective_plan_slug,
    get_plan_fn: Callable[[str | None], dict] | None = None,
    get_settings_fn: Callable[[Session, int], AppSettings],
    roll_month_fn: Callable[[Session, AppSettings], AppSettings],
    repository_factory: RepositoryFactory | None = None,
    now_fn: Clock = _utcnow,
) -> tuple[int, int, int]:
    if get_plan_fn is None:
        from ..plan_catalog import get_plan as get_plan_fn

    plan = get_plan_fn(effective_plan_slug_fn(db, owner_id))
    settings = roll_month_fn(db, get_settings_fn(db, owner_id))
    message_limit = int(plan["monthly_messages"])
    message_left = (
        -1 if message_limit < 0 else max(0, message_limit - int(settings.monthly_used or 0))
    )
    now = now_fn()
    month_start = datetime(now.year, now.month, 1, tzinfo=UTC)
    ai_used = _repository(db, repository_factory).count_sent_ai_since(owner_id, month_start)
    ai_limit = int(plan["monthly_ai_offers"])
    ai_left = max(0, ai_limit - ai_used) if ai_limit >= 0 else -1
    return message_left, int(plan["max_per_launch"]), ai_left


def check_campaign_budget(
    db: Session,
    owner_id: int,
    requested: int,
    *,
    ai_mode: bool,
    effective_plan_slug_fn: Callable[[Session, int], str] = effective_plan_slug,
    get_plan_fn: Callable[[str | None], dict] | None = None,
    campaign_limits_fn: Callable[[Session, int], tuple[int, int, int]] = campaign_limits,
) -> int:
    if get_plan_fn is None:
        from ..plan_catalog import get_plan as get_plan_fn

    plan = get_plan_fn(effective_plan_slug_fn(db, owner_id))
    if plan["slug"] == NO_PLAN:
        raise DomainError(402, _NO_PLAN_HINT)
    if not plan.get("sending"):
        raise DomainError(402, _TRIAL_NO_SEND)
    message_left, per_launch, ai_left = campaign_limits_fn(db, owner_id)
    if message_left == 0:
        raise DomainError(402, "Месячный лимит отправок исчерпан")
    allowed = max(1, requested)
    if per_launch >= 0:
        allowed = min(allowed, per_launch)
    if message_left >= 0:
        allowed = min(allowed, message_left)
    if ai_mode:
        if ai_left == 0:
            raise DomainError(402, "Месячный лимит персональных AI-офферов исчерпан")
        if ai_left >= 0:
            allowed = min(allowed, ai_left)
    return allowed


def usage_summary(
    db: Session,
    owner_id: int,
    *,
    effective_plan_slug_fn: Callable[[Session, int], str] = effective_plan_slug,
    get_plan_fn: Callable[[str | None], dict] | None = None,
    get_settings_fn: Callable[[Session, int], AppSettings],
    roll_month_fn: Callable[[Session, AppSettings], AppSettings],
    campaign_limits_fn: Callable[[Session, int], tuple[int, int, int]] = campaign_limits,
    leads_this_month_fn: Callable[[Session, int], int] = leads_this_month,
    repository_factory: RepositoryFactory | None = None,
) -> dict:
    if get_plan_fn is None:
        from ..plan_catalog import get_plan as get_plan_fn

    plan = get_plan_fn(effective_plan_slug_fn(db, owner_id))
    app_settings = roll_month_fn(db, get_settings_fn(db, owner_id))
    _message_left, _per_launch, ai_left = campaign_limits_fn(db, owner_id)
    ai_limit = int(plan["monthly_ai_offers"])
    repository = _repository(db, repository_factory)
    return {
        "leads_used": leads_this_month_fn(db, owner_id),
        "leads_limit": plan["monthly_leads"],
        "databases": repository.count_databases(owner_id),
        "databases_limit": plan["max_databases"],
        "offers": repository.count_offers(owner_id),
        "offers_limit": plan["max_offers"],
        "campaigns": repository.count_campaigns(owner_id),
        "campaigns_limit": plan["max_campaigns"],
        "warmup": repository.count_warmup(owner_id),
        "warmup_limit": plan["max_warmup"],
        "messages_used": int(app_settings.monthly_used or 0),
        "messages_limit": int(plan["monthly_messages"]),
        "ai_offers_used": max(0, ai_limit - ai_left) if ai_limit >= 0 else 0,
        "ai_offers_limit": ai_limit,
        "max_per_launch": int(plan["max_per_launch"]),
    }


def is_expired(
    db: Session,
    owner_id: int,
    *,
    get_subscription_fn: Callable[[Session, int], object] = get_subscription,
    as_utc_fn: Callable[[datetime | None], datetime | None] = as_utc,
    now_fn: Clock = _utcnow,
) -> bool:
    sub = get_subscription_fn(db, owner_id)
    slug = (sub.plan or "").strip().lower()
    if not slug or slug in (NO_PLAN, "free"):
        return False
    if sub.paid_until is None:
        return False
    paid_until = as_utc_fn(sub.paid_until)
    return bool(paid_until is not None and paid_until < now_fn())


def activate_plan(
    db: Session,
    owner_id: int,
    plan_slug: str,
    months: int,
    admin_id: int | None,
    note: str = "",
    *,
    get_settings_fn: Callable[[Session, int], AppSettings],
    get_subscription_fn: Callable[[Session, int], object] = get_subscription,
    as_utc_fn: Callable[[datetime | None], datetime | None] = as_utc,
    now_fn: Clock = _utcnow,
) -> object:
    slug = (plan_slug or "").strip().lower()
    slug = LEGACY_PLAN_ALIASES.get(slug, slug)
    if slug == "free":
        slug = NO_PLAN
    if slug not in PLANS:
        raise DomainError(400, f"Неизвестный тариф: {plan_slug}")

    app_settings = get_settings_fn(db, owner_id)
    sub = get_subscription_fn(db, owner_id)
    now = now_fn()
    if slug == NO_PLAN:
        next_paid_until = None
    else:
        period_months = max(1, int(months or 1))
        current_paid_until = as_utc_fn(sub.paid_until)
        base = current_paid_until if current_paid_until and current_paid_until > now else now
        next_paid_until = base + timedelta(days=30 * period_months)

    try:
        sub.plan = slug
        sub.status = "none" if slug == NO_PLAN else "active"
        sub.paid_until = next_paid_until
        sub.note = note or sub.note or ""
        sub.activated_by = admin_id
        sub.activated_at = now
        app_settings.monthly_limit = int(PLANS[slug]["monthly_messages"])
        db.commit()
    except Exception:
        db.rollback()
        raise
    db.refresh(sub)
    return sub


def read_subscription(
    db: Session,
    owner_id: int,
    *,
    get_subscription_fn: Callable[[Session, int], object],
    effective_plan_slug_fn: Callable[[Session, int], str],
    get_plan_fn: Callable[[str | None], dict],
    is_first_purchase_fn: Callable[[Session, int], bool],
    is_expired_fn: Callable[[Session, int], bool],
    usage_summary_fn: Callable[[Session, int], dict],
    all_public_plans_fn: Callable[[], list[dict]],
    admin_contact: str,
    first_purchase_discount_pct: int,
) -> SubscriptionOut:
    sub = get_subscription_fn(db, owner_id)
    effective = effective_plan_slug_fn(db, owner_id)
    plan = get_plan_fn(effective)
    first_time = is_first_purchase_fn(db, owner_id)
    trial = trial_state(
        db,
        owner_id,
        effective_plan_slug_fn=effective_plan_slug_fn,
        get_subscription_fn=get_subscription_fn,
    )
    return SubscriptionOut(
        plan=effective,
        plan_name=plan["name"],
        stored_plan=sub.plan or "free",
        status=sub.status or "active",
        blocked=bool(sub.blocked),
        paid_until=sub.paid_until,
        expired=is_expired_fn(db, owner_id),
        ai=bool(plan["ai"]),
        ai_offer=bool(plan["ai_offer"]),
        is_trial=bool(trial["is_trial"]),
        trial_expired=bool(trial["trial_expired"]),
        trial_days_left=int(trial["days_left"]),
        sending=bool(plan.get("sending")),
        note=sub.note or "",
        admin_contact=admin_contact,
        usage=UsageOut(**usage_summary_fn(db, owner_id)),
        plans=[PlanOut(**item) for item in all_public_plans_fn()],
        first_purchase=first_time,
        first_purchase_discount_pct=first_purchase_discount_pct if first_time else 0,
    )


# Тариф, который выдаётся вместе с днями тому, у кого тарифа ещё нет: дни без
# тарифа не значат ничего, доступ считается по плану.
GRANT_DEFAULT_PLAN = "zephyr"


def grant_days(
    db: Session,
    owner_id: int,
    days: int,
    admin_id: int | None,
    note: str = "",
    *,
    get_settings_fn: Callable[[Session, int], AppSettings],
    get_subscription_fn: Callable[[Session, int], object] = get_subscription,
    as_utc_fn: Callable[[datetime | None], datetime | None] = as_utc,
    now_fn: Clock = _utcnow,
) -> object:
    """Добавить (или отнять) дни доступа одному пользователю.

    Продлеваем от текущего срока, если он ещё не вышел, иначе от «сейчас» —
    иначе выдача дней просрочившемуся молча съедалась бы прошлым.

    Отрицательное число разрешено: админ должен уметь исправить свою же
    ошибку, не пересобирая подписку руками.
    """
    delta = int(days or 0)
    if delta == 0:
        raise DomainError(400, "Число дней не может быть нулём")
    if abs(delta) > 3650:
        raise DomainError(400, "Слишком много дней за один раз (максимум 3650)")

    app_settings = get_settings_fn(db, owner_id)
    sub = get_subscription_fn(db, owner_id)
    now = now_fn()
    current = as_utc_fn(sub.paid_until)
    base = current if current and current > now else now
    next_paid_until = base + timedelta(days=delta)

    # Отняли столько, что срок ушёл в прошлое — это не «минус дни», а
    # закончившийся доступ. Оставляем момент как есть: план сам станет
    # неактивным по дате, а обнулять его молча нельзя.
    slug = (sub.plan or NO_PLAN).strip().lower()
    slug = LEGACY_PLAN_ALIASES.get(slug, slug)
    if slug in ("", "free", NO_PLAN):
        slug = GRANT_DEFAULT_PLAN

    try:
        sub.plan = slug
        sub.status = "active" if next_paid_until > now else "expired"
        sub.paid_until = next_paid_until
        if note:
            sub.note = note
        sub.activated_by = admin_id
        sub.activated_at = now
        app_settings.monthly_limit = int(PLANS[slug]["monthly_messages"])
        db.commit()
    except Exception:
        db.rollback()
        raise
    db.refresh(sub)
    return sub


def grant_days_to_everyone(
    db: Session,
    days: int,
    admin_id: int | None,
    note: str = "",
    *,
    only_with_plan: bool = True,
    get_settings_fn: Callable[[Session, int], AppSettings],
    log_event_fn: Callable[..., object] | None = None,
    now_fn: Clock = _utcnow,
) -> list[int]:
    """Выдать дни всем сразу. Возвращает id тех, кому выдали.

    `only_with_plan=True` — только действующим клиентам. Иначе одна кнопка
    раздала бы платный тариф вообще всем, кто когда-либо заходил, включая
    заблокированных: такое лечится только руками.
    """
    owner_ids = PlanRepository(db).owner_ids_for_grant(only_with_plan=only_with_plan)

    touched: list[int] = []
    for owner_id in owner_ids:
        grant_days(
            db,
            owner_id,
            days,
            admin_id,
            note,
            get_settings_fn=get_settings_fn,
            now_fn=now_fn,
        )
        touched.append(owner_id)
        if log_event_fn is not None:
            log_event_fn(
                db,
                owner_id=owner_id,
                scope="settings",
                message=grant_message(days, note),
                level="success",
                ref_type="subscription",
                ref_id=None,
            )
    return touched


def grant_message(days: int, note: str) -> str:
    delta = int(days)
    word = "Админ добавил" if delta > 0 else "Админ убрал"
    return f"{word} {abs(delta)} дн. доступа" + (f" — {note}" if note else "")


def activate_subscription_for_admin(
    db: Session,
    owner_id: int,
    plan_slug: str,
    months: int,
    admin_id: int,
    note: str,
    *,
    repository_factory: RepositoryFactory | None = None,
    activate_plan_fn: Callable[..., object],
    get_plan_fn: Callable[[str | None], dict],
    log_event_fn: Callable[..., object],
) -> object:
    repository = _repository(db, repository_factory)
    if repository.get_user(owner_id) is None:
        raise DomainError(404, "Пользователь не найден")

    sub = activate_plan_fn(db, owner_id, plan_slug, months, admin_id, note)
    until = sub.paid_until.strftime("%Y-%m-%d") if sub.paid_until else "бессрочно"
    log_event_fn(
        db,
        owner_id=owner_id,
        scope="settings",
        message=(
            f"Админ активировал тариф «{get_plan_fn(sub.plan)['name']}» до {until}"
            + (f" — {note}" if note else "")
        ),
        level="success",
        ref_type="subscription",
        ref_id=sub.id,
    )
    return sub


__all__ = [
    "get_subscription",
    "as_utc",
    "effective_plan_slug",
    "is_blocked",
    "count_owned",
    "leads_this_month",
    "check_object_limit",
    "check_leads_budget",
    "is_first_purchase",
    "ai_allowed",
    "ai_offer_allowed",
    "neuro_allowed",
    "ai_offer_plan_names",
    "campaign_limits",
    "check_campaign_budget",
    "usage_summary",
    "is_expired",
    "activate_plan",
    "read_subscription",
    "activate_subscription_for_admin",
]
