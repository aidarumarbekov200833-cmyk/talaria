"""Regression tests for exact, one-shot Telegram reply correlation."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any


def _account(db, owner_id: int, *, username: str = "sender") -> Any:
    from app.models import WarmupAccount

    row = WarmupAccount(
        owner_id=owner_id,
        label="Отправитель",
        tg_id="500",
        tg_username=username,
        connected_at=datetime.now(UTC),
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def _campaign_send(
    db, owner_id: int, account_id: int, *, tg_id: str, username: str
) -> tuple[Any, Any, Any]:
    from app.models import Campaign, CampaignSend, Lead, LeadDatabase

    base = LeadDatabase(owner_id=owner_id, name="База")
    lead = Lead(
        database=base,
        channel_id=f"UC-reply-{tg_id}",
        title="Канал лида",
        tg_id=tg_id,
        tg_username=username,
    )
    campaign = Campaign(owner_id=owner_id, name="Запуск", database_name="База")
    db.add_all([base, lead, campaign])
    db.flush()
    send = CampaignSend(
        owner_id=owner_id,
        campaign_id=campaign.id,
        lead_id=lead.id,
        account_id=account_id,
        status="sent",
        delivered_at=datetime.now(UTC),
    )
    db.add(send)
    db.commit()
    return lead, campaign, send


def _capture_notifications(
    monkeypatch,
) -> tuple[list[str], list[tuple[object, str]], list[tuple[int, dict]]]:
    from app import alerts
    from app.events_hub import hub

    admin_alerts: list[str] = []
    owner_alerts: list[tuple[object, str]] = []
    live: list[tuple[int, dict]] = []
    monkeypatch.setattr(alerts, "send_alert", lambda text, **_kw: admin_alerts.append(text))
    monkeypatch.setattr(
        alerts,
        "notify_user",
        lambda telegram_id, text, **_kw: owner_alerts.append((telegram_id, text)),
    )
    monkeypatch.setattr(
        hub,
        "publish",
        lambda owner_id, payload: (
            live.append((owner_id, payload)) if payload.get("type") == "reply" else None
        ),
    )
    return admin_alerts, owner_alerts, live


def test_unrelated_incoming_message_is_ignored_without_side_effects(
    client, paid_user, db, monkeypatch
):
    from app.models import CampaignLog, EventLog, Lead, LeadDatabase

    account = _account(db, paid_user.id)
    base = LeadDatabase(owner_id=paid_user.id, name="Контакты")
    lead = Lead(
        database=base,
        channel_id="UC-unrelated",
        title="Обычный контакт",
        tg_id="777",
        tg_username="personal_chat",
    )
    db.add_all([base, lead])
    db.commit()
    admin_alerts, owner_alerts, live = _capture_notifications(monkeypatch)
    monkeypatch.setattr("app.routers.campaigns.settings.tg_client_secret", "reply-secret")

    response = client.post(
        "/api/campaigns/reply",
        headers={"X-Send-Secret": "reply-secret"},
        json={
            "owner_id": paid_user.id,
            "account_id": account.id,
            "tg_id": "777",
            "username": "personal_chat",
            "sender_name": "Личный контакт",
            "text": "Обычное сообщение",
        },
    )

    assert response.status_code == 200
    assert response.json()["matched"] is False
    assert not admin_alerts and not owner_alerts and not live
    assert db.query(EventLog).count() == 0
    assert db.query(CampaignLog).count() == 0
    db.expire_all()
    assert db.get(Lead, lead.id).status == "new"


def test_campaign_reply_matches_exact_account_and_tg_id_once(
    client, paid_user, db, monkeypatch
):
    from app.models import Campaign, CampaignLog, CampaignSend, EventLog, Lead

    account = _account(db, paid_user.id)
    other_account = _account(db, paid_user.id, username="other_sender")
    lead, campaign, send = _campaign_send(
        db,
        paid_user.id,
        account.id,
        tg_id="888",
        username="old_username",
    )
    admin_alerts, owner_alerts, live = _capture_notifications(monkeypatch)
    monkeypatch.setattr("app.routers.campaigns.settings.tg_client_secret", "reply-secret")
    payload = {
        "owner_id": paid_user.id,
        "account_id": other_account.id,
        "tg_id": "888",
        "username": "renamed_user",
        "sender_name": "Иван Петров",
        "text": "Интересно, расскажите подробнее",
    }

    wrong_account = client.post(
        "/api/campaigns/reply",
        headers={"X-Send-Secret": "reply-secret"},
        json=payload,
    )
    assert wrong_account.json()["matched"] is False
    assert not admin_alerts and not owner_alerts and not live

    payload["account_id"] = account.id
    accepted = client.post(
        "/api/campaigns/reply",
        headers={"X-Send-Secret": "reply-secret"},
        json=payload,
    )
    duplicate = client.post(
        "/api/campaigns/reply",
        headers={"X-Send-Secret": "reply-secret"},
        json={**payload, "text": "Ещё одно обычное сообщение"},
    )

    assert accepted.status_code == 200
    assert accepted.json() == {
        "ok": True,
        "matched": True,
        "lead_id": lead.id,
        "campaign_id": campaign.id,
        "note": "Ответ по рассылке засчитан",
    }
    assert duplicate.json()["matched"] is False
    db.expire_all()
    assert db.get(CampaignSend, send.id).replied_at is not None
    assert db.get(Campaign, campaign.id).reply_count == 1
    assert db.get(Lead, lead.id).status == "replied"
    assert db.query(CampaignLog).count() == 1
    assert db.query(EventLog).count() == 1
    assert len(admin_alerts) == len(owner_alerts) == len(live) == 1
    assert "Иван Петров (@renamed_user)" in admin_alerts[0]
    assert live[0][1]["lead_name"] == "Иван Петров"


def test_campaign_reply_falls_back_to_username_and_real_send_watches_for_one_hour(
    client, paid_user, db, monkeypatch
):
    from app import sending

    account = _account(db, paid_user.id)
    _lead, _campaign, _send = _campaign_send(
        db,
        paid_user.id,
        account.id,
        tg_id="",
        username="KnownHandle",
    )
    monkeypatch.setattr("app.routers.campaigns.settings.tg_client_secret", "reply-secret")
    _capture_notifications(monkeypatch)

    fallback = client.post(
        "/api/campaigns/reply",
        headers={"X-Send-Secret": "reply-secret"},
        json={
            "owner_id": paid_user.id,
            "account_id": account.id,
            "tg_id": "123456",
            "username": "knownhandle",
            "sender_name": "Получатель",
            "text": "Ответ по username",
        },
    )
    assert fallback.json()["matched"] is True

    gateway_calls: list[dict] = []

    def fake_gateway_send(*_args, **kwargs) -> dict:
        gateway_calls.append(kwargs)
        return {"message_id": 10, "peer_tg_id": "123", "peer_username": "Lead"}

    monkeypatch.setattr(sending.gateway, "send", fake_gateway_send)
    outcome = sending.send_via_client(paid_user.id, account.id, "Lead", "Оффер")
    assert outcome.ok is True
    # v38: альбом офферов — шлюз получает ещё и список картинок.
    assert gateway_calls == [{"image": "", "images": [], "watch_seconds": 3600}]


def test_second_message_does_not_fall_back_to_an_older_campaign_send(
    client, paid_user, db, monkeypatch
):
    from app.models import Campaign, CampaignSend

    account = _account(db, paid_user.id)
    lead, _old_campaign, old_send = _campaign_send(
        db,
        paid_user.id,
        account.id,
        tg_id="4545",
        username="repeat_peer",
    )
    latest_campaign = Campaign(
        owner_id=paid_user.id,
        name="Последняя кампания",
        database_name="База",
    )
    db.add(latest_campaign)
    db.flush()
    latest_send = CampaignSend(
        owner_id=paid_user.id,
        campaign_id=latest_campaign.id,
        lead_id=lead.id,
        account_id=account.id,
        status="sent",
    )
    db.add(latest_send)
    db.commit()
    monkeypatch.setattr("app.routers.campaigns.settings.tg_client_secret", "reply-secret")
    _capture_notifications(monkeypatch)
    payload = {
        "owner_id": paid_user.id,
        "account_id": account.id,
        "tg_id": "4545",
        "username": "repeat_peer",
        "sender_name": "Один получатель",
        "text": "Первый ответ",
    }

    first = client.post(
        "/api/campaigns/reply",
        headers={"X-Send-Secret": "reply-secret"},
        json=payload,
    )
    second = client.post(
        "/api/campaigns/reply",
        headers={"X-Send-Secret": "reply-secret"},
        json={**payload, "text": "Второе сообщение"},
    )

    assert first.json()["campaign_id"] == latest_campaign.id
    assert second.json()["matched"] is False
    db.expire_all()
    assert db.get(CampaignSend, latest_send.id).replied_at is not None
    assert db.get(CampaignSend, old_send.id).replied_at is None


def test_test_send_creates_300_second_watch_and_consumes_first_reply(
    client, paid_user, auth_headers, db, monkeypatch
):
    from app import sending
    from app.models import EventLog, TestReplyWatch

    account = _account(db, paid_user.id)
    send_calls: list[dict] = []

    def fake_send(*_args, **kwargs) -> sending.SendOutcome:
        send_calls.append(kwargs)
        return sending.SendOutcome(
            True,
            message_id=42,
            recipient_tg_id="999",
            recipient_username="TwinAccount",
        )

    monkeypatch.setattr(sending, "send_via_client", fake_send)
    monkeypatch.setattr("app.routers.campaigns.settings.tg_client_secret", "reply-secret")
    admin_alerts, owner_alerts, live = _capture_notifications(monkeypatch)

    sent = client.post(
        "/api/campaigns/test-send",
        headers=auth_headers,
        json={"username": "@TwinAccount", "text": "Проверка уведомления"},
    )
    assert sent.status_code == 200, sent.text
    assert send_calls == [{"images": [], "watch_seconds": 300}]
    db.expire_all()
    watch = db.query(TestReplyWatch).one()
    assert watch.account_id == account.id
    assert watch.recipient_tg_id == "999"
    assert watch.recipient_username == "TwinAccount"
    created = watch.created_at
    if created.tzinfo is None:
        created = created.replace(tzinfo=UTC)
    expires = watch.expires_at
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=UTC)
    assert timedelta(seconds=295) <= expires - created <= timedelta(seconds=305)

    event_count_after_send = db.query(EventLog).count()
    reply = client.post(
        "/api/campaigns/reply",
        headers={"X-Send-Secret": "reply-secret"},
        json={
            "owner_id": paid_user.id,
            "account_id": account.id,
            "tg_id": "999",
            "username": "TwinAccount",
            "sender_name": "Тестовый получатель",
            "text": "Уведомление работает",
        },
    )
    repeated = client.post(
        "/api/campaigns/reply",
        headers={"X-Send-Secret": "reply-secret"},
        json={
            "owner_id": paid_user.id,
            "account_id": account.id,
            "tg_id": "999",
            "username": "TwinAccount",
            "sender_name": "Тестовый получатель",
            "text": "Второе сообщение",
        },
    )

    assert reply.json()["matched"] is True
    assert reply.json()["campaign_id"] is None
    assert repeated.json()["matched"] is False
    db.expire_all()
    assert db.get(TestReplyWatch, watch.id).consumed_at is not None
    assert db.query(EventLog).count() == event_count_after_send + 1
    assert len(admin_alerts) == len(owner_alerts) == len(live) == 1
    assert live[0][1]["lead_name"] == "Тестовый получатель"


def test_failed_or_expired_test_watch_never_notifies(
    client, paid_user, auth_headers, db, monkeypatch
):
    from app import sending
    from app.models import TestReplyWatch

    account = _account(db, paid_user.id)
    monkeypatch.setattr("app.routers.campaigns.settings.tg_client_secret", "reply-secret")
    admin_alerts, owner_alerts, live = _capture_notifications(monkeypatch)
    monkeypatch.setattr(
        sending,
        "send_via_client",
        lambda *_args, **_kwargs: sending.SendOutcome(False, "Telegram отказал"),
    )

    failed = client.post(
        "/api/campaigns/test-send",
        headers=auth_headers,
        json={"username": "ExpiredPeer", "text": "Не ушло"},
    )
    assert failed.status_code == 502
    assert db.query(TestReplyWatch).count() == 0
    # Middleware штатно сообщает админу о самом HTTP 502. Для этой проверки
    # важны только побочные эффекты последующего, уже успешного /reply webhook.
    admin_alerts.clear()
    owner_alerts.clear()
    live.clear()

    db.add(
        TestReplyWatch(
            owner_id=paid_user.id,
            account_id=account.id,
            recipient_tg_id="1000",
            recipient_username="ExpiredPeer",
            expires_at=datetime.now(UTC) - timedelta(seconds=1),
        )
    )
    db.commit()
    ignored = client.post(
        "/api/campaigns/reply",
        headers={"X-Send-Secret": "reply-secret"},
        json={
            "owner_id": paid_user.id,
            "account_id": account.id,
            "tg_id": "1000",
            "username": "ExpiredPeer",
            "sender_name": "Старый тест",
            "text": "Поздний ответ",
        },
    )
    assert ignored.json()["matched"] is False
    assert not admin_alerts and not owner_alerts and not live
