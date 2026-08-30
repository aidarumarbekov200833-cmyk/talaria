"""Учёт денег: платежи пишутся при активации тарифа, выручка сходится.

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


def _admin(db) -> tuple:
    """Админ с токеном (в тестах админ — username admin_user из conftest)."""
    from app import models
    from app.security import create_jwt

    admin = models.User(telegram_id=772_000, username="admin_user", first_name="Админ")
    db.add(admin)
    db.add(models.AccessWhitelist(username="admin_user", note="test"))
    db.flush()
    db.add(models.Subscription(owner_id=admin.id, plan="ultra", status="active"))
    db.commit()
    token, _ = create_jwt(admin.id)
    return admin, {"Authorization": f"Bearer {token}"}


def test_активация_тарифа_с_суммой_пишет_платёж(client, db, user):
    """Деньги записываются вместе с выдачей тарифа: отдельной формы больше нет."""
    _, headers = _admin(db)
    r = client.post(
        f"/api/admin/users/{user.id}/subscription",
        json={"plan": "pro", "months": 2, "note": "перевод", "amount": 25_000, "currency": "KZT"},
        headers=headers,
    )
    assert r.status_code == 200, r.text
    summary = payments_service.revenue_summary(db)
    assert summary["all_time"] == {"KZT": 2_500_000}
    assert summary["payments_total"] == 1


def test_активация_без_суммы_не_выдумывает_платёж(client, db, user):
    _, headers = _admin(db)
    r = client.post(
        f"/api/admin/users/{user.id}/subscription",
        json={"plan": "pro", "months": 1, "note": "подарок"},
        headers=headers,
    )
    assert r.status_code == 200, r.text
    assert payments_service.revenue_summary(db)["payments_total"] == 0


def test_объявление_не_приходит_самому_админу(client, db, user):
    admin, headers = _admin(db)
    r = client.post(
        "/api/admin/notifications",
        json={"target": "all", "message": "плановые работы", "level": "info", "telegram": False},
        headers=headers,
    )
    assert r.status_code == 200, r.text
    notices = (
        db.query(models.EventLog).filter(models.EventLog.scope == "notification").all()
    )
    assert {n.owner_id for n in notices} != set()
    assert admin.id not in {n.owner_id for n in notices}
    assert user.id in {n.owner_id for n in notices}


def test_пробный_тариф_не_считается_платной_подпиской(client, db, user):
    from datetime import UTC, datetime, timedelta

    db.add(
        models.Subscription(
            owner_id=user.id,
            plan="trial",
            status="active",
            paid_until=datetime.now(UTC) + timedelta(days=3),
        )
    )
    db.commit()
    _, headers = _admin(db)
    data = client.get("/api/admin/overview", headers=headers).json()
    # Платная здесь одна — у самого админа (_admin ставит ultra); пробная — отдельно.
    assert data["active_subs"] == 1
    assert data["trial_subs"] == 1


def test_лента_действий_не_показывает_служебные_уведомления(client, db, user):
    db.add(models.EventLog(owner_id=user.id, scope="notification", message="Сообщение администратора: тест"))
    db.add(models.EventLog(owner_id=user.id, scope="search", message="Поиск каналов"))
    db.commit()
    _, headers = _admin(db)
    data = client.get("/api/admin/overview", headers=headers).json()
    messages = [a["message"] for a in data["recent_actions"]]
    assert "Поиск каналов" in messages
    assert all("Сообщение администратора" not in m for m in messages)


def test_платежи_доступны_только_админу(client, auth_headers):
    assert client.get("/api/admin/payments", headers=auth_headers).status_code == 403
