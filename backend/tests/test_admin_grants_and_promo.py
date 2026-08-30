"""Админка: выдача дней, промокоды и IP в журнале действий.

Проверяем то, что реально стоит денег и доступа: продление считается от
правильной точки, массовая выдача не задевает лишних, промокод перестаёт
работать по всем трём причинам, а журнал показывает, с какого адреса пришло
действие.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app import models
from app.plan_catalog import quote_plan
from app.services import promo as promo_service
from app.services.errors import DomainError
from app.services.plans import grant_days, grant_days_to_everyone
from app.services.settings import get_settings


def _sub(db, owner_id: int) -> models.Subscription:
    return db.query(models.Subscription).filter_by(owner_id=owner_id).one()


def _make_user(db, telegram_id: int, plan: str, *, blocked: bool = False) -> models.User:
    user = models.User(telegram_id=telegram_id, username=f"u{telegram_id}")
    db.add(user)
    db.flush()
    db.add(
        models.Subscription(
            owner_id=user.id,
            plan=plan,
            status="active" if plan != "none" else "none",
            blocked=blocked,
        )
    )
    db.commit()
    return user


# ------------------------------- выдача дней -------------------------------


def test_продление_действующего_считается_от_текущего_срока(db, user):
    until = datetime.now(UTC) + timedelta(days=10)
    db.add(models.Subscription(owner_id=user.id, plan="zephyr", status="active", paid_until=until))
    db.commit()

    grant_days(db, user.id, 5, admin_id=None, get_settings_fn=get_settings)

    got = _sub(db, user.id).paid_until
    got = got if got.tzinfo else got.replace(tzinfo=UTC)
    assert abs((got - (until + timedelta(days=5))).total_seconds()) < 5


def test_продление_просроченного_считается_от_сегодня(db, user):
    """Иначе выдача дней молча уходила бы в прошлое и ничего не давала."""
    past = datetime.now(UTC) - timedelta(days=30)
    db.add(models.Subscription(owner_id=user.id, plan="zephyr", status="expired", paid_until=past))
    db.commit()

    grant_days(db, user.id, 7, admin_id=None, get_settings_fn=get_settings)

    got = _sub(db, user.id).paid_until
    got = got if got.tzinfo else got.replace(tzinfo=UTC)
    assert got > datetime.now(UTC) + timedelta(days=6)


def test_дни_без_тарифа_включают_тариф(db, user):
    """Дни без плана не значат ничего: доступ считается по тарифу."""
    db.add(models.Subscription(owner_id=user.id, plan="none", status="none"))
    db.commit()

    grant_days(db, user.id, 3, admin_id=None, get_settings_fn=get_settings)
    assert _sub(db, user.id).plan == "zephyr"


def test_можно_снять_ошибочно_выданные_дни(db, user):
    until = datetime.now(UTC) + timedelta(days=40)
    db.add(models.Subscription(owner_id=user.id, plan="zephyr", status="active", paid_until=until))
    db.commit()

    grant_days(db, user.id, -30, admin_id=None, get_settings_fn=get_settings)

    got = _sub(db, user.id).paid_until
    got = got if got.tzinfo else got.replace(tzinfo=UTC)
    assert got < until


@pytest.mark.parametrize("days", [0, 4000, -4000])
def test_бессмысленные_значения_отклоняются(db, user, days):
    db.add(models.Subscription(owner_id=user.id, plan="zephyr", status="active"))
    db.commit()
    with pytest.raises(DomainError):
        grant_days(db, user.id, days, admin_id=None, get_settings_fn=get_settings)


def test_массовая_выдача_не_трогает_безтарифных_и_заблокированных(db, user):
    db.add(models.Subscription(owner_id=user.id, plan="zephyr", status="active"))
    db.commit()
    paying = _make_user(db, 8001, "zephyr")
    _make_user(db, 8002, "none")
    _make_user(db, 8003, "zephyr", blocked=True)

    touched = grant_days_to_everyone(db, 2, admin_id=None, get_settings_fn=get_settings)

    assert set(touched) == {user.id, paying.id}


def test_массовая_выдача_всем_включает_безтарифных(db, user):
    db.add(models.Subscription(owner_id=user.id, plan="none", status="none"))
    db.commit()
    _make_user(db, 8004, "none")

    touched = grant_days_to_everyone(
        db, 2, admin_id=None, only_with_plan=False, get_settings_fn=get_settings
    )
    assert len(touched) == 2


# -------------------------------- промокоды --------------------------------


def test_созданный_код_сразу_даёт_скидку(db):
    promo_service.create_code(db, code="летО-25", discount_pct=25)
    code, pct = promo_service.discount_for(db, " лето-25 ")
    assert (code, pct) == ("ЛЕТО-25", 25)


def test_код_перестаёт_работать_по_каждой_из_трёх_причин(db):
    off = promo_service.create_code(db, code="OFF", discount_pct=10)
    promo_service.update_code(db, off.id, active=False)
    assert promo_service.discount_for(db, "OFF") == ("", 0)

    old = promo_service.create_code(db, code="OLD", discount_pct=10)
    promo_service.update_code(db, old.id, valid_until=datetime.now(UTC) - timedelta(days=1))
    assert promo_service.discount_for(db, "OLD") == ("", 0)

    promo_service.create_code(db, code="SPENT", discount_pct=10, max_uses=1)
    promo_service.mark_used(db, "SPENT")
    assert promo_service.discount_for(db, "SPENT") == ("", 0)


def test_дубликат_и_кривые_значения_не_проходят(db):
    promo_service.create_code(db, code="DUP", discount_pct=10)
    with pytest.raises(DomainError):
        promo_service.create_code(db, code="dup", discount_pct=10)
    with pytest.raises(DomainError):
        promo_service.create_code(db, code="BAD CODE", discount_pct=10)
    with pytest.raises(DomainError):
        promo_service.create_code(db, code="ZERO", discount_pct=0)


def test_расчёт_цены_берёт_скидку_из_базы(db):
    promo_service.create_code(db, code="HALF", discount_pct=50)
    quote = quote_plan(1, "HALF", "RUB", promo_lookup=lambda c: promo_service.discount_for(db, c))
    assert quote["promo_valid"] is True
    assert quote["promo_discount"] == round(quote["base_total"] * 0.5)
    assert "50%" in quote["promo_message"]


def test_негодный_код_не_подсказывает_причину(db):
    """Иначе «код есть, но кончился» помогает перебирать чужие коды."""
    code = promo_service.create_code(db, code="GONE", discount_pct=10, max_uses=1)
    promo_service.mark_used(db, "GONE")
    quote = quote_plan(1, "GONE", "RUB", promo_lookup=lambda c: promo_service.discount_for(db, c))
    assert quote["promo_valid"] is False
    assert quote["promo_message"] == "Промокод не найден"
    assert code.code not in quote["promo_message"]


# ---------------------------- журнал действий и IP ----------------------------


def test_событие_запоминает_адрес_запроса(db, user):
    """Раньше по журналу нельзя было понять, откуда пришло действие."""
    from app.access import bind_request_ip
    from app.services import log_event

    class FakeRequest:
        headers = {"x-forwarded-for": "203.0.113.9"}
        client = None

    bind_request_ip(FakeRequest())
    log_event(db, owner_id=user.id, scope="settings", message="Проверка адреса")

    row = db.query(models.EventLog).filter_by(owner_id=user.id).order_by(
        models.EventLog.id.desc()
    ).first()
    assert row.ip == "203.0.113.9"


def test_у_фоновой_задачи_адреса_нет(db, user):
    """Пустой IP честнее выдуманного: у крона нет запроса."""
    from app.access import bind_request_ip
    from app.services import log_event

    bind_request_ip(None)
    log_event(db, owner_id=user.id, scope="app", message="Фоновая задача")

    row = db.query(models.EventLog).filter_by(owner_id=user.id).order_by(
        models.EventLog.id.desc()
    ).first()
    assert row.ip == ""
