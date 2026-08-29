"""Выключатель уведомлений об ответе лида.

Проверяем ровно то, ради чего он делался: выключенный — молчит, включённый —
пишет, и при этом сам ответ засчитывается в любом случае. Потерять ответ из-за
выключенного уведомления было бы куда хуже, чем лишнее сообщение.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app import models


def _settings_row(db, owner_id: int) -> models.AppSettings:
    row = db.query(models.AppSettings).filter_by(owner_id=owner_id).one_or_none()
    if row is None:
        row = models.AppSettings(owner_id=owner_id)
        db.add(row)
        db.commit()
    return row


def test_по_умолчанию_уведомление_включено(db, user):
    """Человек, который ничего не настраивал, должен узнавать об ответах."""
    row = _settings_row(db, user.id)
    assert bool(row.reply_notify) is True


def test_настройки_отдают_флаг(client, auth_headers):
    response = client.get("/api/settings", headers=auth_headers)
    assert response.status_code == 200
    assert response.json()["reply_notify"] is True


def test_флаг_переключается_и_сохраняется(client, auth_headers):
    off = client.patch("/api/settings", json={"reply_notify": False}, headers=auth_headers)
    assert off.status_code == 200
    assert off.json()["reply_notify"] is False

    # Именно сохраняется, а не живёт до конца запроса.
    again = client.get("/api/settings", headers=auth_headers)
    assert again.json()["reply_notify"] is False

    on = client.patch("/api/settings", json={"reply_notify": True}, headers=auth_headers)
    assert on.json()["reply_notify"] is True


def test_чужие_поля_настроек_остаются_только_для_чтения(client, auth_headers):
    """Лимит рассылок ставит админ. Попытка поднять его себе не должна проходить."""
    before = client.get("/api/settings", headers=auth_headers).json()
    client.patch(
        "/api/settings",
        json={"reply_notify": True, "monthly_limit": 999_999},
        headers=auth_headers,
    )
    after = client.get("/api/settings", headers=auth_headers).json()
    assert after["monthly_limit"] == before["monthly_limit"]


@pytest.mark.parametrize("enabled", [True, False])
def test_ответ_засчитывается_независимо_от_уведомления(db, user, monkeypatch, enabled):
    """Выключенное уведомление гасит только сообщение, но не саму отметку."""
    from app.routers import campaigns

    row = _settings_row(db, user.id)
    row.reply_notify = enabled
    db.commit()

    monkeypatch.setattr(campaigns.settings, "tg_client_secret", "toggle-secret")
    sent: list[tuple] = []
    monkeypatch.setattr(
        campaigns.alerts, "notify_user", lambda *a, **kw: sent.append((a, kw))
    )
    monkeypatch.setattr(campaigns.alerts, "send_alert", lambda *a, **kw: None)

    account = models.WarmupAccount(owner_id=user.id, label="Acc", tg_id="900")
    db.add(account)
    db.flush()
    watch = models.TestReplyWatch(
        owner_id=user.id,
        account_id=account.id,
        recipient_tg_id="777",
        recipient_username="client",
        expires_at=datetime(2100, 1, 1, tzinfo=UTC),
    )
    db.add(watch)
    db.commit()

    result = campaigns.lead_replied(
        campaigns.CampaignReplyIn(
            owner_id=user.id,
            account_id=account.id,
            tg_id="777",
            username="client",
            sender_name="Клиент",
            text="Интересно, расскажите подробнее",
        ),
        x_send_secret="toggle-secret",
        db=db,
    )

    # Ответ засчитан в обоих случаях.
    assert result.matched is True
    db.refresh(watch)
    assert watch.consumed_at is not None

    # А сообщение ушло только при включённом выключателе.
    assert bool(sent) is enabled
