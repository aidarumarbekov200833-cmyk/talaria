"""Поддержка внутри кабинета: обращение клиента и ответ владельца.

Что здесь проверяется по существу, а не «ручка отвечает 200»:

* обращение доходит до владельца через Telegram, но сохраняется даже если бот
  промолчал — иначе при сбое Telegram вопрос человека исчезает бесследно;
* «непрочитано» гаснет у той стороны, которая открыла диалог, и только у неё;
* ответ владельца попадает в журнал уведомлений клиента — колокольчик обязан
  показать ответ, даже если аккаунт-меню человек не открывал;
* суточный предел не позволяет одному человеку засыпать владельца.
"""

from __future__ import annotations

import pytest

from app import models
from app.security import create_jwt
from app.services import support as support_service


@pytest.fixture
def admin(db):
    """Владелец сервиса. Админ определяется по username (см. conftest)."""
    row = models.User(telegram_id=2002, username="admin_user", first_name="Boss")
    db.add(row)
    db.add(models.AccessWhitelist(username="admin_user", note="support test"))
    db.commit()
    db.refresh(row)
    return row


def _headers(user_id: int) -> dict[str, str]:
    token, _jti = create_jwt(user_id)
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def sent_alerts(monkeypatch):
    """Перехват сообщений бота: тесты не ходят в Telegram."""
    box: dict[str, list[str]] = {"admins": [], "users": []}
    monkeypatch.setattr(
        support_service.alerts,
        "send_alert",
        lambda text, **_kwargs: box["admins"].append(text),
    )
    monkeypatch.setattr(
        support_service.alerts,
        "notify_user",
        lambda telegram_id, text, **_kwargs: box["users"].append(f"{telegram_id}:{text}"),
    )
    # Уведомление уходит в отдельном потоке, чтобы не задерживать ответ клиенту;
    # в тесте выполняем его сразу, иначе проверять было бы нечего.
    monkeypatch.setattr(support_service, "_spawn", lambda target, *args: target(*args))
    return box


def test_обращение_сохраняется_и_уходит_владельцу_в_telegram(client, db, user, sent_alerts):
    response = client.post(
        "/api/support/messages",
        json={"text": "Не вижу лидов после поиска"},
        headers=_headers(user.id),
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["author"] == "user"
    assert body["read"] is False
    assert db.query(models.SupportMessage).count() == 1
    assert len(sent_alerts["admins"]) == 1
    # В уведомлении обязаны быть и текст, и кто написал: иначе владельцу придётся
    # искать автора руками.
    assert "Не вижу лидов после поиска" in sent_alerts["admins"][0]
    assert str(user.id) in sent_alerts["admins"][0]


def test_обращение_сохраняется_даже_если_telegram_упал(client, db, user, monkeypatch):
    def boom(*_args, **_kwargs) -> None:
        raise RuntimeError("Telegram недоступен")

    monkeypatch.setattr(support_service.alerts, "send_alert", boom)
    monkeypatch.setattr(support_service, "_spawn", lambda target, *args: None)

    response = client.post(
        "/api/support/messages",
        json={"text": "Вопрос по тарифу"},
        headers=_headers(user.id),
    )

    assert response.status_code == 200, response.text
    assert db.query(models.SupportMessage).count() == 1


def test_пустое_и_слишком_длинное_обращение_отклоняются(client, user):
    headers = _headers(user.id)
    assert client.post(
        "/api/support/messages", json={"text": "   "}, headers=headers
    ).status_code in {
        400,
        422,
    }
    long_text = "а" * (support_service.MAX_TEXT + 1)
    assert client.post(
        "/api/support/messages", json={"text": long_text}, headers=headers
    ).status_code in {400, 422}


def test_ответ_владельца_виден_клиенту_и_гасит_непрочитанное(client, db, user, admin, sent_alerts):
    client.post(
        "/api/support/messages",
        json={"text": "Как добавить ключ?"},
        headers=_headers(user.id),
    )

    reply = client.post(
        f"/api/admin/support/{user.id}",
        json={"text": "Настройки → ключи YouTube API"},
        headers=_headers(admin.id),
    )
    assert reply.status_code == 200, reply.text
    assert reply.json()["author"] == "admin"
    # Клиенту ушло личное сообщение бота, а не алерт админам.
    assert any(str(user.telegram_id) in item for item in sent_alerts["users"])

    # Ответ обязан появиться в журнале уведомлений: колокольчик — единственное
    # место, куда человек смотрит, не открывая поддержку.
    notice = (
        db.query(models.EventLog)
        .filter(models.EventLog.owner_id == user.id, models.EventLog.ref_type == "support")
        .one()
    )
    assert "Настройки" in notice.message

    thread = client.get("/api/support/messages", headers=_headers(user.id))
    assert thread.status_code == 200, thread.text
    body = thread.json()
    assert [m["author"] for m in body["messages"]] == ["user", "admin"]
    assert body["unread"] == 1, "при первом открытии ответ должен считаться новым"

    # Повторное открытие — уже без непрочитанных: точка на аккаунте гаснет.
    again = client.get("/api/support/messages", headers=_headers(user.id))
    assert again.json()["unread"] == 0
    assert support_service.unread_for_user(db, user.id) == 0


def test_открытие_диалога_клиентом_не_гасит_непрочитанное_у_владельца(
    client, db, user, admin, sent_alerts
):
    client.post("/api/support/messages", json={"text": "Первый вопрос"}, headers=_headers(user.id))
    client.get("/api/support/messages", headers=_headers(user.id))

    listing = client.get("/api/admin/support", headers=_headers(admin.id))
    assert listing.status_code == 200, listing.text
    threads = listing.json()["threads"]
    assert len(threads) == 1
    assert threads[0]["user_id"] == user.id
    assert threads[0]["unread"] == 1, "клиент прочитал свои же слова, а не владелец"
    assert listing.json()["unread_total"] == 1

    opened = client.get(f"/api/admin/support/{user.id}", headers=_headers(admin.id))
    assert opened.status_code == 200
    after = client.get("/api/admin/support", headers=_headers(admin.id))
    assert after.json()["threads"][0]["unread"] == 0


def test_суточный_предел_обращений(client, db, user, monkeypatch, sent_alerts):
    monkeypatch.setattr(support_service, "DAILY_LIMIT", 2)
    headers = _headers(user.id)
    assert (
        client.post("/api/support/messages", json={"text": "1"}, headers=headers).status_code == 200
    )
    assert (
        client.post("/api/support/messages", json={"text": "2"}, headers=headers).status_code == 200
    )
    third = client.post("/api/support/messages", json={"text": "3"}, headers=headers)
    assert third.status_code == 429
    assert db.query(models.SupportMessage).count() == 2


def test_поддержка_закрыта_без_токена_и_для_чужой_админки(client, user):
    assert client.get("/api/support/messages").status_code == 401
    # Обычный пользователь не должен читать чужие обращения и отвечать за админа.
    assert client.get("/api/admin/support", headers=_headers(user.id)).status_code == 403
    assert (
        client.post(
            f"/api/admin/support/{user.id}", json={"text": "я админ"}, headers=_headers(user.id)
        ).status_code
        == 403
    )
