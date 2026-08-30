"""Главный аккаунт для уведомлений.

Проверяем три вещи: выбор действительно меняет адресата, чужой chat_id
подставить нельзя, и «не проверяли» честно отличается от «проверили, доходит».
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app import models
from app.services import settings as settings_service
from app.services.errors import DomainError


def _sender(db, owner_id: int, tg_id: str, label: str) -> models.WarmupAccount:
    account = models.WarmupAccount(
        owner_id=owner_id,
        label=label,
        tg_id=tg_id,
        connected_at=datetime.now(UTC),
    )
    db.add(account)
    db.commit()
    return account


def test_выбор_состоит_из_аккаунта_входа_и_подключённых(db, user):
    _sender(db, user.id, "5001", "Рабочий")
    _sender(db, user.id, "5002", "Запасной")

    targets = settings_service.list_notify_targets(db, user)
    kinds = [t.kind for t in targets]
    assert kinds.count("login") == 1
    assert kinds.count("sender") == 2
    # По умолчанию выбран аккаунт входа.
    chosen = [t for t in targets if t.selected]
    assert len(chosen) == 1
    assert chosen[0].kind == "login"


def test_чужой_аккаунт_выбрать_нельзя(db, user):
    """Иначе уведомления о своих лидах можно направить постороннему человеку."""
    stranger = models.User(telegram_id=999_777, username="stranger")
    db.add(stranger)
    db.commit()
    _sender(db, stranger.id, "6001", "Чужой аккаунт")

    with pytest.raises(DomainError) as err:
        settings_service.update_settings(db, user.id, notify_target_tg_id=6001, user=user)
    assert err.value.status_code == 400

    # И «просто число» тоже не проходит.
    with pytest.raises(DomainError):
        settings_service.update_settings(db, user.id, notify_target_tg_id=1234, user=user)


def test_выбор_сохраняется_и_меняет_адресата(db, user):
    _sender(db, user.id, "5001", "Рабочий")

    assert settings_service.notify_target_of(db, user) == user.telegram_id
    settings_service.update_settings(db, user.id, notify_target_tg_id=5001, user=user)
    assert settings_service.notify_target_of(db, user) == 5001

    targets = settings_service.list_notify_targets(db, user)
    assert [t.tg_id for t in targets if t.selected] == [5001]


def test_смена_аккаунта_сбрасывает_отметку_о_доставке(db, user):
    """Проверка относилась к прежнему адресу — переносить её было бы ложью."""
    _sender(db, user.id, "5001", "Рабочий")
    settings_service.test_notify_target(db, user, send_fn=lambda *a, **kw: True)
    assert settings_service.get_settings(db, user.id).notify_target_ok is True

    settings_service.update_settings(db, user.id, notify_target_tg_id=5001, user=user)
    assert settings_service.get_settings(db, user.id).notify_target_ok is None


def test_недоставленная_проверка_объясняет_что_делать(db, user):
    """Бот не может писать первым: без Start сообщение молча пропадает."""
    result = settings_service.test_notify_target(db, user, send_fn=lambda *a, **kw: False)
    assert result.delivered is False
    assert "Start" in result.detail
    assert settings_service.get_settings(db, user.id).notify_target_ok is False


def test_проверка_идёт_на_выбранный_аккаунт_а_не_на_аккаунт_входа(db, user):
    _sender(db, user.id, "5001", "Рабочий")
    settings_service.update_settings(db, user.id, notify_target_tg_id=5001, user=user)

    seen: list[int] = []

    def fake_send(target, text, **kwargs) -> bool:
        seen.append(target)
        return True

    settings_service.test_notify_target(db, user, send_fn=fake_send)
    assert seen == [5001]


def test_ответ_лида_уходит_на_выбранный_аккаунт(db, user, monkeypatch):
    from app.routers import campaigns

    monkeypatch.setattr(campaigns.settings, "tg_client_secret", "target-secret")
    monkeypatch.setattr(campaigns.alerts, "send_alert", lambda *a, **kw: None)
    sent: list[int] = []
    monkeypatch.setattr(
        campaigns.alerts, "notify_user", lambda target, *a, **kw: sent.append(target) or True
    )

    account = _sender(db, user.id, "5001", "Рабочий")
    settings_service.update_settings(db, user.id, notify_target_tg_id=5001, user=user)

    watch = models.TestReplyWatch(
        owner_id=user.id,
        account_id=account.id,
        recipient_tg_id="777",
        recipient_username="client",
        expires_at=datetime(2100, 1, 1, tzinfo=UTC),
    )
    db.add(watch)
    db.commit()

    campaigns.lead_replied(
        campaigns.CampaignReplyIn(
            owner_id=user.id,
            account_id=account.id,
            tg_id="777",
            username="client",
            sender_name="Клиент",
            text="Давайте обсудим",
        ),
        x_send_secret="target-secret",
        db=db,
    )
    assert sent == [5001], "уведомление ушло не на выбранный аккаунт"


def test_эндпоинты_выбора_работают_целиком(client, auth_headers, db, user):
    _sender(db, user.id, "5001", "Рабочий")

    listing = client.get("/api/settings/notify-targets", headers=auth_headers)
    assert listing.status_code == 200
    assert {t["tg_id"] for t in listing.json()} == {int(user.telegram_id), 5001}

    ok = client.patch(
        "/api/settings", json={"notify_target_tg_id": 5001}, headers=auth_headers
    )
    assert ok.status_code == 200
    assert ok.json()["notify_target_tg_id"] == 5001
    assert ok.json()["notify_target_ok"] is None

    denied = client.patch(
        "/api/settings", json={"notify_target_tg_id": 424242}, headers=auth_headers
    )
    assert denied.status_code == 400
