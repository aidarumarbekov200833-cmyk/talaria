"""Пробный тариф и открытая регистрация.

Проверяем ровно то, что легко сломать при следующей правке и что стоит денег
или доверия: триал выдаётся один раз, отправка на нём невозможна ни одним из
входов, рубильник регистрации переживает перезапуск, а список пользователей в
админке действительно делится на группы.
"""

from __future__ import annotations

import hashlib
import hmac
import time
from datetime import UTC, datetime, timedelta

from app import access, models
from app.config import settings
from app.plan_catalog import TRIAL_PLAN
from app.services import plans as plan_service


def _signed_login(telegram_id: int, username: str) -> dict:
    payload = {
        "id": telegram_id,
        "first_name": "Гость",
        "username": username,
        "auth_date": int(time.time()),
    }
    pairs = sorted(f"{key}={value}" for key, value in payload.items())
    secret = hashlib.sha256(settings.bot_token.encode()).digest()
    payload["hash"] = hmac.new(secret, "\n".join(pairs).encode(), hashlib.sha256).hexdigest()
    return payload


# ------------------------------- выдача триала -------------------------------


def test_триал_выдаётся_один_раз_и_не_возвращается_после_истечения(db, user):
    assert plan_service.start_trial(db, user.id) is True
    sub = db.query(models.Subscription).filter_by(owner_id=user.id).one()
    assert sub.plan == TRIAL_PLAN
    first_until = sub.paid_until

    # Повторный вход не продлевает пробный период по кругу.
    assert plan_service.start_trial(db, user.id) is False
    db.refresh(sub)
    assert sub.paid_until == first_until

    # Истёк — заново не выдаём: иначе выйти и войти означало бы «ещё две недели».
    sub.paid_until = datetime.now(UTC) - timedelta(days=1)
    db.commit()
    assert plan_service.start_trial(db, user.id) is False


def test_на_триале_состояние_отдаёт_дни_и_запрет_отправки(db, user):
    plan_service.start_trial(db, user.id)
    state = plan_service.trial_state(db, user.id)
    assert state["is_trial"] is True
    assert state["sending"] is False
    assert state["days_left"] > 0
    assert plan_service.sending_allowed(db, user.id) is False


def test_истёкший_триал_отличается_от_отсутствия_тарифа(db, user):
    plan_service.start_trial(db, user.id)
    sub = db.query(models.Subscription).filter_by(owner_id=user.id).one()
    sub.paid_until = datetime.now(UTC) - timedelta(days=1)
    db.commit()
    state = plan_service.trial_state(db, user.id)
    assert state["is_trial"] is False
    assert state["trial_expired"] is True


# --------------------------- запрет отправки везде ---------------------------


def test_все_входы_в_отправку_отвечают_объяснением_а_не_лимитом_ноль(client, db, user):
    plan_service.start_trial(db, user.id)
    from app.security import create_jwt

    token, _jti = create_jwt(user.id)
    headers = {"Authorization": f"Bearer {token}"}

    created = client.post("/api/campaigns", json={"name": "п", "database_name": "б"}, headers=headers)
    assert created.status_code == 402
    assert "пробном тарифе отправка сообщений выключена" in created.json()["detail"]

    launched = client.post("/api/campaigns/launch", json={"database_id": 1}, headers=headers)
    assert launched.status_code == 402
    assert "пробном тарифе" in launched.json()["detail"]

    # Нейрокомментинг — отдельная возможность, но отказ тоже с причиной.
    neuro = client.put("/api/neuro/run", json={"enabled": True}, headers=headers)
    assert neuro.status_code == 402
    assert "нейрокомментинг недоступен" in neuro.json()["detail"]


# ----------------------------- открытая регистрация -----------------------------


def test_рубильник_регистрации_живёт_в_базе_и_переживает_перезапуск(db):
    assert access.registration_open(db) is settings.open_registration
    access.set_registration_open(db, False, admin_id=None)
    assert access.registration_open(db) is False
    # Второй вызов не плодит строки: настройка на всю установку одна.
    access.set_registration_open(db, True, admin_id=None)
    assert db.query(models.PlatformSettings).count() == 1
    assert access.registration_open(db) is True


def test_закрытая_регистрация_не_пускает_нового_но_пускает_белый_список(client, db):
    access.set_registration_open(db, False, admin_id=None)
    try:
        denied = client.post("/api/auth/telegram", json=_signed_login(970001, "stranger"))
        assert denied.status_code == 403

        db.add(models.AccessWhitelist(username="invited", note="тест"))
        db.commit()
        allowed = client.post("/api/auth/telegram", json=_signed_login(970002, "invited"))
        assert allowed.status_code == 200
        # Регистрация закрыта — триал не выдаём, доступ выдал админ вручную.
        owner = db.query(models.User).filter_by(telegram_id=970002).one()
        sub = db.query(models.Subscription).filter_by(owner_id=owner.id).one_or_none()
        assert sub is None or sub.plan != TRIAL_PLAN
    finally:
        access.set_registration_open(db, True, admin_id=None)


def test_регистрация_пачками_с_одного_адреса_упирается_в_потолок(client, db, monkeypatch):
    monkeypatch.setattr(settings, "trial_max_per_ip", 2)
    headers = {"X-Forwarded-For": "9.9.9.9"}
    codes = [
        client.post(
            "/api/auth/telegram", json=_signed_login(971000 + i, f"mass{i}"), headers=headers
        ).status_code
        for i in range(4)
    ]
    assert codes[:2] == [200, 200]
    assert 429 in codes[2:]


# --------------------------------- админка ---------------------------------


def test_админский_список_делит_людей_на_группы_и_отмечает_подозрительных(client, db):
    # В тестах админ определяется по username (ADMIN_USERNAME_FALLBACK=1).
    admin = models.User(telegram_id=972000, username="admin_user")
    db.add(admin)
    db.add(models.AccessWhitelist(username="admin_user", note="фикстура"))
    db.flush()
    db.add(models.Subscription(owner_id=admin.id, plan="zephyr", status="active"))

    trial = models.User(telegram_id=972001, username=None, first_name="Бот", photo_url=None)
    db.add(trial)
    db.flush()
    db.add(
        models.Subscription(
            owner_id=trial.id,
            plan=TRIAL_PLAN,
            status="trial",
            paid_until=datetime.now(UTC) + timedelta(days=5),
        )
    )
    db.commit()

    from app.security import create_jwt

    token, _jti = create_jwt(admin.id)
    response = client.get(
        "/api/admin/users?scope=trial", headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["scope"] == "trial"
    assert [row["id"] for row in body["users"]] == [trial.id]
    assert body["counts"]["trial"] == 1
    assert body["counts"]["paid"] == 1
    flags = body["users"][0]["flags"]
    assert "без username" in flags
    assert "без аватара" in flags
