"""Tariff catalog compatibility facade and lazy DB-policy adapters.

Payment remains manual: the client contacts the administrator and an admin
activates the subscription after confirmation. This module intentionally keeps
the historical public names while delegating persistence and transaction
policy to repositories/services.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy.orm import Session

from .plan_catalog import (  # noqa: F401
    DEFAULT_PLAN,
    FIRST_PURCHASE_DISCOUNT_PCT,
    LEGACY_PLAN_ALIASES,
    NO_PLAN,
    PLAN_ORDER,
    PLANS,
    PROMO_CODE,
    PROMO_DISCOUNT_PCT,
    QUARTER_DISCOUNT_PCT,
    TRIAL_DAYS,
    TRIAL_PLAN,
    all_public_plans,
    first_purchase_price,
    get_plan,
    public_plan,
    quote_plan,
)


def _service_module():  # noqa: ANN202 - private lazy module loader
    from .services import plans as plan_service

    return plan_service


def _http_call(function, *args, **kwargs):  # noqa: ANN001,ANN002,ANN003,ANN202
    from fastapi import HTTPException

    from .services.errors import DomainError

    try:
        return function(*args, **kwargs)
    except DomainError as exc:
        raise HTTPException(exc.status_code, exc.detail, headers=exc.headers) from exc


def get_subscription(db: Session, owner_id: int):
    return _service_module().get_subscription(db, owner_id)


def _as_utc(value: datetime | None) -> datetime | None:
    return _service_module().as_utc(value)


def effective_plan_slug(db: Session, owner_id: int) -> str:
    return _service_module().effective_plan_slug(
        db,
        owner_id,
        get_subscription_fn=get_subscription,
        as_utc_fn=_as_utc,
    )


def is_blocked(db: Session, owner_id: int) -> bool:
    return _service_module().is_blocked(
        db,
        owner_id,
        get_subscription_fn=get_subscription,
    )


def _count(db: Session, model, owner_id: int) -> int:  # noqa: ANN001
    return _service_module().count_owned(db, model, owner_id)


def leads_this_month(db: Session, owner_id: int) -> int:
    return _service_module().leads_this_month(db, owner_id)


def check_object_limit(db: Session, owner_id: int, kind: str) -> None:
    service = _service_module()
    return _http_call(
        service.check_object_limit,
        db,
        owner_id,
        kind,
        effective_plan_slug_fn=effective_plan_slug,
        get_plan_fn=get_plan,
    )


def check_leads_budget(db: Session, owner_id: int, want: int) -> int:
    service = _service_module()
    return _http_call(
        service.check_leads_budget,
        db,
        owner_id,
        want,
        effective_plan_slug_fn=effective_plan_slug,
        get_plan_fn=get_plan,
        leads_this_month_fn=leads_this_month,
    )


def is_first_purchase(db: Session, owner_id: int) -> bool:
    return _service_module().is_first_purchase(
        db,
        owner_id,
        get_subscription_fn=get_subscription,
    )


def ai_allowed(db: Session, owner_id: int) -> bool:
    return _service_module().ai_allowed(
        db,
        owner_id,
        effective_plan_slug_fn=effective_plan_slug,
        get_plan_fn=get_plan,
    )


def ai_offer_allowed(db: Session, owner_id: int) -> bool:
    return _service_module().ai_offer_allowed(
        db,
        owner_id,
        effective_plan_slug_fn=effective_plan_slug,
        get_plan_fn=get_plan,
    )


def start_trial(db: Session, owner_id: int) -> bool:
    return _service_module().start_trial(
        db,
        owner_id,
        get_subscription_fn=get_subscription,
    )


def trial_state(db: Session, owner_id: int) -> dict:
    return _service_module().trial_state(
        db,
        owner_id,
        effective_plan_slug_fn=effective_plan_slug,
        get_subscription_fn=get_subscription,
    )


def sending_allowed(db: Session, owner_id: int) -> bool:
    return _service_module().sending_allowed(
        db,
        owner_id,
        effective_plan_slug_fn=effective_plan_slug,
        get_plan_fn=get_plan,
    )


def require_sending(db: Session, owner_id: int) -> None:
    service = _service_module()
    return _http_call(
        service.require_sending,
        db,
        owner_id,
        effective_plan_slug_fn=effective_plan_slug,
        get_plan_fn=get_plan,
    )


def ai_offer_plan_names() -> str:
    return _service_module().ai_offer_plan_names()


def campaign_limits(db: Session, owner_id: int) -> tuple[int, int, int]:
    from .services import get_settings, roll_month

    return _service_module().campaign_limits(
        db,
        owner_id,
        effective_plan_slug_fn=effective_plan_slug,
        get_plan_fn=get_plan,
        get_settings_fn=get_settings,
        roll_month_fn=roll_month,
    )


def check_campaign_budget(db: Session, owner_id: int, requested: int, *, ai_mode: bool) -> int:
    service = _service_module()
    return _http_call(
        service.check_campaign_budget,
        db,
        owner_id,
        requested,
        ai_mode=ai_mode,
        effective_plan_slug_fn=effective_plan_slug,
        get_plan_fn=get_plan,
        campaign_limits_fn=campaign_limits,
    )


def usage_summary(db: Session, owner_id: int) -> dict:
    from .services import get_settings, roll_month

    return _service_module().usage_summary(
        db,
        owner_id,
        effective_plan_slug_fn=effective_plan_slug,
        get_plan_fn=get_plan,
        get_settings_fn=get_settings,
        roll_month_fn=roll_month,
        campaign_limits_fn=campaign_limits,
        leads_this_month_fn=leads_this_month,
    )


def is_expired(db: Session, owner_id: int) -> bool:
    return _service_module().is_expired(
        db,
        owner_id,
        get_subscription_fn=get_subscription,
        as_utc_fn=_as_utc,
    )


def activate_plan(
    db: Session,
    owner_id: int,
    plan_slug: str,
    months: int,
    admin_id: int | None,
    note: str = "",
) -> object:
    from .services import get_settings

    service = _service_module()
    return _http_call(
        service.activate_plan,
        db,
        owner_id,
        plan_slug,
        months,
        admin_id,
        note,
        get_settings_fn=get_settings,
        get_subscription_fn=get_subscription,
        as_utc_fn=_as_utc,
    )
