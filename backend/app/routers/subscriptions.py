"""Тарифы и подписка пользователя.

Оплата вне системы: клиент выбирает тариф, пишет админу в личку (@admin_contact),
оплачивает, и админ вручную активирует план (см. routers/admin.py). Здесь —
только чтение: публичный список тарифов для лендинга и состояние подписки для
настроек. Ничего не списывается и не продаётся программно.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ..config import settings
from ..database import get_db
from ..dependencies import get_current_user
from ..models import User
from ..plans import (
    FIRST_PURCHASE_DISCOUNT_PCT,
    all_public_plans,
    effective_plan_slug,
    get_plan,
    get_subscription,
    is_expired,
    is_first_purchase,
    quote_plan,
    usage_summary,
)
from ..schemas import PlanOut, PlanQuoteIn, PlanQuoteOut, PlansOut, SubscriptionOut
from ..services import promo as promo_service
from ..services.plans import read_subscription

router = APIRouter(prefix="/api", tags=["subscription"])


def _plan_models() -> list[PlanOut]:
    return [PlanOut(**p) for p in all_public_plans()]


@router.get("/plans", response_model=PlansOut)
def public_plans() -> PlansOut:
    """Публичный список тарифов — показывается на лендинге до входа."""
    return PlansOut(
        plans=_plan_models(),
        currencies=["RUB", "KZT"],
        admin_contact=settings.admin_contact,
        first_purchase_discount_pct=FIRST_PURCHASE_DISCOUNT_PCT,
    )


@router.post("/plans/quote", response_model=PlanQuoteOut)
def plan_quote(payload: PlanQuoteIn, db: Session = Depends(get_db)) -> PlanQuoteOut:
    """Рассчитать 1/3 месяца и промокод без доверия арифметике браузера.

    Промокоды живут в базе и заводятся из админки, поэтому проверка кода —
    запрос, а не сравнение с константой.
    """
    return PlanQuoteOut(
        **quote_plan(
            payload.period_months,
            payload.promo_code,
            payload.currency,
            promo_lookup=lambda code: promo_service.discount_for(db, code),
        ),
        admin_contact=settings.admin_contact,
    )


@router.get("/subscription", response_model=SubscriptionOut)
def my_subscription(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> SubscriptionOut:
    """Состояние подписки текущего пользователя для раздела настроек."""
    return read_subscription(
        db,
        user.id,
        get_subscription_fn=get_subscription,
        effective_plan_slug_fn=effective_plan_slug,
        get_plan_fn=get_plan,
        is_first_purchase_fn=is_first_purchase,
        is_expired_fn=is_expired,
        usage_summary_fn=usage_summary,
        all_public_plans_fn=all_public_plans,
        admin_contact=settings.admin_contact,
        first_purchase_discount_pct=FIRST_PURCHASE_DISCOUNT_PCT,
    )
