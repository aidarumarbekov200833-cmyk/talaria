"""Учёт денег и список тех, кто перестал заходить.

Деньги — единственная часть системы, где расхождение нельзя объяснить
округлением, поэтому здесь проверяется арифметика, а не только маршруты.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app import models
from app.services import payments as payments_service
from app.services.errors import DomainError


def _pay(
    db, owner_id: int, amount_minor: int, *, currency="RUB", days_ago=0
) -> models.Payment:
    return payments_service.add_payment(
        db,
        owner_id,
        amount_minor=amount_minor,
        currency=currency,
        admin_id=1,
        paid_at=datetime.now(UTC) - timedelta(days=days_ago),
    )


def test_платёж_хранится_в_копейках(db, user):
    payment = _pay(db, user.id, 500_000)
    assert payment.amount_minor == 500_000
    assert payment.amount == 5000.0


def test_суммы_не_плывут_при_сложении(db, user):
    """Три платежа по 10.10 должны дать ровно 30.30, а не 30.299999999999997."""
    for _ in range(3):
        _pay(db, user.id, 1010)
    summary = payments_service.revenue_summary(db)
    assert summary["all_time"]["RUB"] == 3030


def test_возврат_уменьшает_выручку_а_ноль_не_принимается(db, user):
    _pay(db, user.id, 500_000)
    _pay(db, user.id, -200_000)
    assert payments_service.revenue_summary(db)["all_time"]["RUB"] == 300_000

    with pytest.raises(DomainError) as err:
        _pay(db, user.id, 0)
    assert err.value.status_code == 400


def test_опечатка_в_сумме_и_валюте_отклоняется(db, user):
    with pytest.raises(DomainError):
        _pay(db, user.id, 10**12)  # лишние нули
    with pytest.raises(DomainError):
        payments_service.add_payment(
            db, user.id, amount_minor=100, currency="рубли", admin_id=1
        )


def test_дата_из_будущего_отклоняется(db, user):
    with pytest.raises(DomainError) as err:
        payments_service.add_payment(
            db,
            user.id,
            amount_minor=100,
            currency="RUB",
            admin_id=1,
            paid_at=datetime.now(UTC) + timedelta(days=30),
        )
    assert "будущем" in err.value.detail


def test_платёж_несуществующему_пользователю_не_записывается(db):
    with pytest.raises(DomainError) as err:
        payments_service.add_payment(db, 999_999, amount_minor=100, currency="RUB", admin_id=1)
    assert err.value.status_code == 404


def test_валюты_не_складываются_между_собой(db, user):
    """Курса на дату платежа мы не храним — значит, складывать нечем."""
    _pay(db, user.id, 500_000, currency="RUB")
    _pay(db, user.id, 300_000, currency="KZT")
    summary = payments_service.revenue_summary(db)
    assert summary["all_time"] == {"RUB": 500_000, "KZT": 300_000}


def test_сводка_режет_по_периодам(db, user):
    _pay(db, user.id, 100_000, days_ago=0)
    _pay(db, user.id, 100_000, days_ago=45)  # за пределами 30 дней
    summary = payments_service.revenue_summary(db)
    assert summary["all_time"]["RUB"] == 200_000
    assert summary["last_30_days"]["RUB"] == 100_000
    assert summary["paying_users_this_month"] >= 1


def test_ошибочный_платёж_удаляется(db, user):
    payment = _pay(db, user.id, 100_000)
    payments_service.delete_payment(db, payment.id)
    assert payments_service.revenue_summary(db)["payments_total"] == 0
    with pytest.raises(DomainError):
        payments_service.delete_payment(db, payment.id)


def test_отток_ловит_молчащих_с_оплаченным_тарифом(db, user):
    from app.routers.admin import _churn_risk

    now = datetime.now(UTC)
    silent = models.User(telegram_id=771_001, username="silent", first_name="Тихий")
    active = models.User(telegram_id=771_002, username="active", first_name="活")
    db.add_all([silent, active])
    db.commit()
    for person in (silent, active):
        db.add(
            models.Subscription(
                owner_id=person.id,
                plan="zephyr",
                status="active",
                paid_until=now + timedelta(days=20),
            )
        )
    db.commit()
    # У одного последнее событие месяц назад, у другого — сегодня.
    db.add(
        models.EventLog(
            owner_id=silent.id, scope="auth", message="вход", created_at=now - timedelta(days=30)
        )
    )
    db.add(models.EventLog(owner_id=active.id, scope="auth", message="вход", created_at=now))
    db.commit()

    ids = {row.user_id for row in _churn_risk(db, now)}
    assert silent.id in ids
    assert active.id not in ids


def test_оплативший_и_ни_разу_не_зашедший_тоже_в_списке(db):
    """Такому звонить нужно не меньше, чем ушедшему: он даже не начал."""
    from app.routers.admin import _churn_risk

    now = datetime.now(UTC)
    newcomer = models.User(telegram_id=771_003, username="newcomer")
    db.add(newcomer)
    db.commit()
    db.add(
        models.Subscription(
            owner_id=newcomer.id,
            plan="zephyr",
            status="active",
            paid_until=now + timedelta(days=25),
        )
    )
    db.commit()

    rows = _churn_risk(db, now)
    row = next(r for r in rows if r.user_id == newcomer.id)
    # Дней молчания посчитать не от чего — честнее показать это, чем выдумать 0.
    assert row.days_silent == -1
    assert row.last_seen is None


def test_заблокированный_и_просроченный_в_отток_не_попадают(db):
    from app.routers.admin import _churn_risk

    now = datetime.now(UTC)
    blocked = models.User(telegram_id=771_004, username="blocked")
    expired = models.User(telegram_id=771_005, username="expired")
    db.add_all([blocked, expired])
    db.commit()
    db.add(
        models.Subscription(
            owner_id=blocked.id,
            plan="zephyr",
            status="active",
            blocked=True,
            paid_until=now + timedelta(days=20),
        )
    )
    db.add(
        models.Subscription(
            owner_id=expired.id,
            plan="zephyr",
            status="active",
            paid_until=now - timedelta(days=3),
        )
    )
    db.commit()

    ids = {row.user_id for row in _churn_risk(db, now)}
    assert blocked.id not in ids
    assert expired.id not in ids


def test_платежи_доступны_только_админу(client, auth_headers):
    assert client.get("/api/admin/payments", headers=auth_headers).status_code == 403
