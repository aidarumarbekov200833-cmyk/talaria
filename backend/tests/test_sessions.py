"""Список входов и принудительный выход.

Раздел про безопасность, поэтому проверяется не «кнопка нажимается», а то,
что закрытый вход действительно перестаёт работать: отметка в таблице сеансов
сама по себе ничего не закрывает, доступ режет чёрный список.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app import models
from app.security import create_jwt
from app.services import sessions as sessions_service


def _login(db, user, *, ip="203.0.113.7", agent="Mozilla/5.0 (Windows NT 10.0) Chrome/120"):
    """Выдать токен и записать сеанс — как это делает вход."""
    token, jti = create_jwt(user.id)
    sessions_service.register(db, user.id, jti, ip=ip, user_agent=agent, ttl_days=14)
    return {"Authorization": f"Bearer {token}"}, jti


# --- Подпись устройства ----------------------------------------------------


def test_подпись_устройства_узнаёт_браузер_и_систему():
    agent = "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0) AppleWebKit Safari/604.1"
    assert sessions_service.describe_agent(agent) == "Safari, iPhone"


def test_edge_не_выдаётся_за_chrome():
    """Edge представляется и Chrome, и Safari — порядок проверок важен."""
    agent = "Mozilla/5.0 (Windows NT 10.0) Chrome/120 Safari/537.36 Edg/120"
    assert sessions_service.describe_agent(agent) == "Edge, Windows"


def test_нераспознанное_устройство_не_придумывается():
    assert sessions_service.describe_agent("") == "Неизвестное устройство"
    assert sessions_service.describe_agent("curl/8.0") == "Неизвестное устройство"


def test_ip_показывается_частично():
    assert sessions_service.mask_ip("203.0.113.7") == "203.0.113.×"
    assert sessions_service.mask_ip("2001:db8:85a3:1:2:3:4:5") == "2001:db8:85a3:×"
    assert sessions_service.mask_ip("") == ""


# --- Список ----------------------------------------------------------------


def test_список_показывает_текущий_вход(client, db, user):
    headers, _jti = _login(db, user)
    body = client.get("/api/auth/sessions", headers=headers).json()
    assert len(body["items"]) == 1
    assert body["items"][0]["current"] is True
    assert body["items"][0]["device"] == "Chrome, Windows"


def test_в_списке_только_свои_входы(client, db, user):
    stranger = models.User(telegram_id=9009, username="other")
    db.add(stranger)
    db.commit()
    db.refresh(stranger)
    _other_token, other_jti = create_jwt(stranger.id)
    sessions_service.register(db, stranger.id, other_jti, ttl_days=14)

    headers, _jti = _login(db, user)
    body = client.get("/api/auth/sessions", headers=headers).json()
    assert [item["jti"] for item in body["items"]] == [_jti]


def test_истёкший_вход_из_списка_пропадает(client, db, user):
    headers, _jti = _login(db, user)
    stale = models.UserSession(
        owner_id=user.id,
        jti="stale-jti",
        expires_at=datetime.now(UTC) - timedelta(days=1),
    )
    db.add(stale)
    db.commit()
    body = client.get("/api/auth/sessions", headers=headers).json()
    assert "stale-jti" not in [item["jti"] for item in body["items"]]


# --- Закрытие --------------------------------------------------------------


def test_закрытый_вход_перестаёт_работать(client, db, user):
    """Главная проверка: после закрытия старый токен получает 401."""
    old_headers, old_jti = _login(db, user, agent="Mozilla/5.0 (iPhone) Safari/604.1")
    new_headers, _new_jti = _login(db, user)

    assert client.get("/api/me", headers=old_headers).status_code == 200

    revoked = client.post(
        "/api/auth/sessions/revoke", json={"jti": old_jti}, headers=new_headers
    )
    assert revoked.status_code == 200
    assert client.get("/api/me", headers=old_headers).status_code == 401


def test_свой_вход_через_список_не_закрывается(client, db, user):
    headers, jti = _login(db, user)
    response = client.post("/api/auth/sessions/revoke", json={"jti": jti}, headers=headers)
    assert response.status_code == 400
    assert client.get("/api/me", headers=headers).status_code == 200


def test_чужой_вход_закрыть_нельзя(client, db, user):
    """Иначе по угаданному jti можно было бы выкидывать чужих людей."""
    stranger = models.User(telegram_id=9010, username="other2")
    db.add(stranger)
    db.commit()
    db.refresh(stranger)
    stranger_token, stranger_jti = create_jwt(stranger.id)
    sessions_service.register(db, stranger.id, stranger_jti, ttl_days=14)

    headers, _jti = _login(db, user)
    response = client.post(
        "/api/auth/sessions/revoke", json={"jti": stranger_jti}, headers=headers
    )
    assert response.status_code == 404
    # И чужой токен продолжает работать.
    assert (
        db.query(models.UserSession).filter_by(jti=stranger_jti).first().revoked_at is None
    )


def test_выйти_везде_оставляет_текущий(client, db, user):
    phone, phone_jti = _login(db, user, agent="Mozilla/5.0 (Android 14) Chrome/120")
    laptop, laptop_jti = _login(db, user)

    response = client.post("/api/auth/sessions/revoke-others", headers=laptop)
    assert response.status_code == 200
    assert client.get("/api/me", headers=laptop).status_code == 200
    assert client.get("/api/me", headers=phone).status_code == 401
    assert db.query(models.UserSession).filter_by(jti=phone_jti).first().revoked_at is not None
    assert db.query(models.UserSession).filter_by(jti=laptop_jti).first().revoked_at is None


def test_повторное_закрытие_не_ломается(client, db, user):
    old_headers, old_jti = _login(db, user)
    new_headers, _ = _login(db, user)
    client.post("/api/auth/sessions/revoke", json={"jti": old_jti}, headers=new_headers)
    again = client.post(
        "/api/auth/sessions/revoke", json={"jti": old_jti}, headers=new_headers
    )
    assert again.status_code == 404


def test_обычный_выход_гасит_запись_сеанса(client, db, user):
    headers, jti = _login(db, user)
    assert client.post("/api/auth/logout", headers=headers).status_code == 200
    row = db.query(models.UserSession).filter_by(jti=jti).first()
    assert row.revoked_at is not None


def test_закрытие_попадает_в_журнал(client, db, user):
    _old, old_jti = _login(db, user)
    new_headers, _ = _login(db, user)
    client.post("/api/auth/sessions/revoke", json={"jti": old_jti}, headers=new_headers)
    events = db.query(models.EventLog).filter(models.EventLog.scope == "security").all()
    assert any("вход" in row.message.lower() for row in events)


# --- Активность ------------------------------------------------------------


def test_активность_обновляется_не_чаще_чем_раз_в_интервал(client, db, user):
    """Запись на каждый запрос — лишняя нагрузка ради точности, которая не нужна."""
    headers, jti = _login(db, user)
    row = db.query(models.UserSession).filter_by(jti=jti).first()
    row.last_seen_at = datetime.now(UTC) - timedelta(seconds=30)
    db.commit()
    before = row.last_seen_at

    client.get("/api/me", headers=headers)
    db.refresh(row)
    assert row.last_seen_at == before

    row.last_seen_at = datetime.now(UTC) - timedelta(hours=2)
    db.commit()
    client.get("/api/me", headers=headers)
    db.refresh(row)
    # SQLite отдаёт время без часового пояса — сравниваем в одном виде.
    fresh = row.last_seen_at
    if fresh.tzinfo is None:
        fresh = fresh.replace(tzinfo=UTC)
    assert fresh > datetime.now(UTC) - timedelta(minutes=1)
