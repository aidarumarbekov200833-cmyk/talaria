"""Переписка Telegram в кабинете.

Здесь проверяется прослойка, а не Telegram: шлюз подменён. Основной риск этой
части — не «не отрисовался чат», а «открыл чужую переписку» и «сообщение ушло
не тому», поэтому большая часть проверок про принадлежность аккаунта и про
идентификатор собеседника.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app import models
from app.telegram_gateway import TelegramGatewayError, gateway


@pytest.fixture
def connected_account(db, user):
    """Подключённый Telegram-аккаунт владельца."""
    account = models.WarmupAccount(
        owner_id=user.id,
        label="Основной",
        is_primary=True,
        tg_id="5001",
        tg_username="samat",
        connected_at=datetime.now(UTC),
    )
    db.add(account)
    db.commit()
    db.refresh(account)
    return account


@pytest.fixture
def stub_chat(monkeypatch):
    """Подменяет шлюз и записывает, с какими аргументами его звали."""
    calls: list[tuple[str, tuple, dict]] = []

    def record(name, result):
        def _call(*args, **kwargs):
            calls.append((name, args, kwargs))
            return result() if callable(result) else result

        return _call

    monkeypatch.setattr(
        gateway,
        "chat_dialogs",
        record(
            "dialogs",
            {
                "items": [
                    {
                        "peer_id": "5001",
                        "kind": "user",
                        "title": "Иван",
                        "username": "ivan",
                        "unread": 2,
                        "last_message": "привет",
                        "last_date": 1700000000000,
                    }
                ],
                "next": None,
            },
        ),
    )
    monkeypatch.setattr(
        gateway,
        "chat_history",
        record(
            "history",
            {
                "items": [{"id": 1, "date": 1700000000000, "text": "привет", "out": False}],
                "next_offset_id": 0,
            },
        ),
    )
    monkeypatch.setattr(gateway, "chat_send", record("send", {"message_id": 77, "date": 1700000001000}))
    monkeypatch.setattr(gateway, "chat_read", record("read", {"ok": True}))
    monkeypatch.setattr(gateway, "chat_avatar", record("avatar", "data:image/jpeg;base64,AAA"))
    monkeypatch.setattr(gateway, "chat_media", record("media", "data:image/jpeg;base64,BBB"))
    return calls


# --- Принадлежность аккаунта ----------------------------------------------


def test_чужой_аккаунт_недоступен(client, db, user, auth_headers, connected_account, stub_chat):
    """Главный риск раздела: подставить чужой account_id и читать чужую переписку."""
    stranger = models.User(telegram_id=2002, username="stranger")
    db.add(stranger)
    db.commit()
    db.refresh(stranger)
    foreign = models.WarmupAccount(
        owner_id=stranger.id,
        label="Чужой",
        connected_at=datetime.now(UTC),
    )
    db.add(foreign)
    db.commit()
    db.refresh(foreign)

    response = client.get(f"/api/chat/dialogs?account_id={foreign.id}", headers=auth_headers)
    assert response.status_code == 404
    # До шлюза запрос дойти не должен вовсе.
    assert stub_chat == []


def test_без_токена_переписка_закрыта(client):
    assert client.get("/api/chat/dialogs").status_code in (401, 403)


def test_неподключённый_аккаунт_отвечает_понятно(client, db, user, auth_headers, stub_chat):
    account = models.WarmupAccount(owner_id=user.id, label="Ещё не подключён")
    db.add(account)
    db.commit()
    db.refresh(account)
    response = client.get(f"/api/chat/dialogs?account_id={account.id}", headers=auth_headers)
    assert response.status_code == 400
    assert "не подключ" in response.json()["detail"].lower()


def test_без_единого_аккаунта_просят_подключить(client, user, auth_headers, stub_chat):
    response = client.get("/api/chat/dialogs", headers=auth_headers)
    assert response.status_code == 400
    assert stub_chat == []


# --- Идентификатор собеседника ---------------------------------------------


@pytest.mark.parametrize("peer", ["", "abc", "12; DROP", "@ivan", "1" * 25, "1.5"])
def test_негодный_идентификатор_не_доходит_до_telegram(
    client, auth_headers, connected_account, stub_chat, peer
):
    response = client.get(f"/api/chat/history?peer_id={peer}", headers=auth_headers)
    assert response.status_code in (400, 422)
    assert stub_chat == []


def test_группа_с_минусом_допустима(client, auth_headers, connected_account, stub_chat):
    response = client.get("/api/chat/history?peer_id=-1001234567890", headers=auth_headers)
    assert response.status_code == 200
    assert stub_chat[0][1][2] == "-1001234567890"


def test_большой_идентификатор_не_теряет_цифры(client, auth_headers, connected_account, stub_chat):
    """Через JSON id идёт строкой: number потерял бы последние разряды."""
    peer = "7284591063847"
    client.get(f"/api/chat/history?peer_id={peer}", headers=auth_headers)
    assert stub_chat[0][1][2] == peer


# --- Диалоги ---------------------------------------------------------------


def test_диалоги_отдаются_с_аккаунтом(client, auth_headers, connected_account, stub_chat):
    body = client.get("/api/chat/dialogs", headers=auth_headers).json()
    assert body["account_id"] == connected_account.id
    assert body["items"][0]["title"] == "Иван"
    assert body["items"][0]["unread"] == 2


def test_собеседник_из_базы_лидов_помечен(client, db, user, auth_headers, connected_account, stub_chat):
    """Рабочий контакт должен отличаться от личного прямо в списке."""
    database = models.LeadDatabase(owner_id=user.id, name="Тест")
    db.add(database)
    db.commit()
    db.refresh(database)
    lead = models.Lead(database_id=database.id, channel_id="UC1", title="Иван", tg_id="5001")
    db.add(lead)
    db.commit()
    db.refresh(lead)

    body = client.get("/api/chat/dialogs", headers=auth_headers).json()
    assert body["items"][0]["lead_id"] == lead.id


def test_чужой_лид_не_подмешивается(client, db, user, auth_headers, connected_account, stub_chat):
    stranger = models.User(telegram_id=3003, username="other")
    db.add(stranger)
    db.commit()
    db.refresh(stranger)
    database = models.LeadDatabase(owner_id=stranger.id, name="Чужая")
    db.add(database)
    db.commit()
    db.refresh(database)
    db.add(models.Lead(database_id=database.id, channel_id="UC1", title="Иван", tg_id="5001"))
    db.commit()

    body = client.get("/api/chat/dialogs", headers=auth_headers).json()
    assert body["items"][0]["lead_id"] is None


# --- Отправка --------------------------------------------------------------


def test_отправка_возвращает_номер_сообщения(client, auth_headers, connected_account, stub_chat):
    response = client.post(
        "/api/chat/send",
        json={"peer_id": "5001", "text": "здравствуйте"},
        headers=auth_headers,
    )
    assert response.status_code == 200
    assert response.json()["message_id"] == 77


def test_пустое_сообщение_не_отправляется(client, auth_headers, connected_account, stub_chat):
    response = client.post(
        "/api/chat/send", json={"peer_id": "5001", "text": "   \n  "}, headers=auth_headers
    )
    assert response.status_code == 400
    assert stub_chat == []


def test_слишком_длинное_сообщение_отклоняется(client, auth_headers, connected_account, stub_chat):
    response = client.post(
        "/api/chat/send",
        json={"peer_id": "5001", "text": "я" * 5000},
        headers=auth_headers,
    )
    assert response.status_code == 400
    assert stub_chat == []


def test_текст_сообщения_не_попадает_в_журнал(
    client, db, user, auth_headers, connected_account, stub_chat
):
    """Журнал видит поддержка — личная переписка в нём не место."""
    secret = "мой пароль от банка 1234"
    client.post("/api/chat/send", json={"peer_id": "5001", "text": secret}, headers=auth_headers)
    logged = " ".join(row.message for row in db.query(models.EventLog).all())
    assert secret not in logged


# --- Ошибки Telegram -------------------------------------------------------


def test_флуд_лимит_отдаётся_как_429(client, auth_headers, connected_account, monkeypatch):
    """Клиент по коду понимает, что надо подождать, а не что чат сломался."""

    def boom(*_args, **_kwargs):
        raise TelegramGatewayError("Слишком часто", code="FLOOD_WAIT", retry_after=30)

    monkeypatch.setattr(gateway, "chat_dialogs", boom)
    response = client.get("/api/chat/dialogs", headers=auth_headers)
    assert response.status_code == 429
    assert response.headers["Retry-After"] == "30"


def test_недоступный_шлюз_отдаётся_как_502(client, auth_headers, connected_account, monkeypatch):
    def boom(*_args, **_kwargs):
        raise TelegramGatewayError("Telegram-gateway недоступен")

    monkeypatch.setattr(gateway, "chat_history", boom)
    response = client.get("/api/chat/history?peer_id=5001", headers=auth_headers)
    assert response.status_code == 502


# --- Картинки --------------------------------------------------------------


def test_аватар_отдаётся_data_url(client, auth_headers, connected_account, stub_chat):
    body = client.get("/api/chat/avatar?peer_id=5001", headers=auth_headers).json()
    assert body["image"].startswith("data:image/")


def test_картинка_требует_номер_сообщения(client, auth_headers, connected_account, stub_chat):
    response = client.get("/api/chat/media?peer_id=5001&message_id=0", headers=auth_headers)
    assert response.status_code == 400
    assert stub_chat == []


# --- Переключатель аккаунтов -----------------------------------------------


def test_список_аккаунтов_показывает_только_свои(
    client, db, user, auth_headers, connected_account
):
    stranger = models.User(telegram_id=4004, username="other2")
    db.add(stranger)
    db.commit()
    db.refresh(stranger)
    db.add(models.WarmupAccount(owner_id=stranger.id, label="Чужой", connected_at=datetime.now(UTC)))
    db.commit()

    body = client.get("/api/chat/accounts", headers=auth_headers).json()
    assert [row["id"] for row in body] == [connected_account.id]
    assert body[0]["connected"] is True
