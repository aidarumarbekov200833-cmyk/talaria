"""Закрепление и «без звука» в переписке.

Шлюз подменён: проверяется прослойка. Два риска. Первый — рассинхрон зеркала:
mute должен записаться в свою таблицу только после успешного ответа шлюза,
иначе Telegram шумит, а Talaria молчит (или наоборот). Второй — уведомление
«лид ответил» обязано уважать mute, поставленный в чате.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app import models
from app.telegram_gateway import TelegramGatewayError, gateway


@pytest.fixture
def connected_account(db, user):
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
def stub_gateway(monkeypatch):
    calls: list[tuple[str, tuple]] = []
    monkeypatch.setattr(
        gateway, "chat_pin", lambda *args: calls.append(("pin", args)) or {"ok": True}
    )
    monkeypatch.setattr(
        gateway, "chat_mute", lambda *args: calls.append(("mute", args)) or {"ok": True}
    )
    return calls


def _prefs(db) -> list:
    return db.query(models.ChatDialogPref).all()


def test_закрепление_уходит_в_шлюз(client, auth_headers, connected_account, stub_gateway):
    response = client.post(
        "/api/chat/pin",
        headers=auth_headers,
        json={"peer_id": "777", "pinned": True},
    )
    assert response.status_code == 200
    assert stub_gateway == [("pin", (connected_account.owner_id, connected_account.id, "777", True))]


def test_mute_пишет_зеркало_а_снятие_удаляет(
    client, db, auth_headers, connected_account, stub_gateway
):
    ok = client.post(
        "/api/chat/mute",
        headers=auth_headers,
        json={"peer_id": "777", "muted": True},
    )
    assert ok.status_code == 200
    rows = _prefs(db)
    assert len(rows) == 1
    assert (rows[0].peer_id, rows[0].muted) == ("777", True)

    # Повторный mute не плодит вторую строку.
    client.post("/api/chat/mute", headers=auth_headers, json={"peer_id": "777", "muted": True})
    assert len(_prefs(db)) == 1

    off = client.post(
        "/api/chat/mute",
        headers=auth_headers,
        json={"peer_id": "777", "muted": False},
    )
    assert off.status_code == 200
    assert _prefs(db) == []


def test_ошибка_шлюза_не_трогает_зеркало(
    client, db, auth_headers, connected_account, monkeypatch
):
    def boom(*_args) -> None:
        raise TelegramGatewayError("Telegram недоступен")

    monkeypatch.setattr(gateway, "chat_mute", boom)
    response = client.post(
        "/api/chat/mute",
        headers=auth_headers,
        json={"peer_id": "777", "muted": True},
    )
    assert response.status_code == 502
    assert _prefs(db) == []


def test_ответ_замьюченного_лида_не_будит_владельца(client, db, paid_user, monkeypatch):
    """Mute в чате глушит и уведомление Talaria «лид ответил»."""
    from datetime import datetime as dt

    from app import alerts

    account = models.WarmupAccount(
        owner_id=paid_user.id,
        label="Отправитель",
        tg_id="500",
        tg_username="sender",
        connected_at=dt.now(UTC),
    )
    base = models.LeadDatabase(owner_id=paid_user.id, name="База")
    lead = models.Lead(
        database=base,
        channel_id="UC-muted",
        title="Канал лида",
        tg_id="777",
        tg_username="ivan",
    )
    campaign = models.Campaign(owner_id=paid_user.id, name="Запуск", database_name="База")
    db.add_all([account, base, lead, campaign])
    db.flush()
    db.add(
        models.CampaignSend(
            owner_id=paid_user.id,
            campaign_id=campaign.id,
            lead_id=lead.id,
            account_id=account.id,
            status="sent",
            delivered_at=dt.now(UTC),
        )
    )
    db.add(
        models.ChatDialogPref(
            owner_id=paid_user.id, account_id=account.id, peer_id="777", muted=True
        )
    )
    db.commit()

    owner_alerts: list[tuple[object, str]] = []
    monkeypatch.setattr(alerts, "send_alert", lambda *_a, **_kw: None)
    monkeypatch.setattr(
        alerts, "notify_user", lambda telegram_id, text, **_kw: owner_alerts.append((telegram_id, text))
    )
    monkeypatch.setattr("app.routers.campaigns.settings.tg_client_secret", "reply-secret")

    response = client.post(
        "/api/campaigns/reply",
        headers={"X-Send-Secret": "reply-secret"},
        json={
            "owner_id": paid_user.id,
            "account_id": account.id,
            "tg_id": "777",
            "username": "ivan",
            "sender_name": "Иван",
            "text": "Интересно, расскажите",
        },
    )

    assert response.status_code == 200
    assert response.json()["matched"] is True
    # Ответ засчитан, но в личку бота владельцу ничего не ушло: диалог без звука.
    assert owner_alerts == []
