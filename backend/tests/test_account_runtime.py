"""Telegram account quarantine, rotation, metrics and SpamBot regressions."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app import contacts, models, sending
from app.account_health import apply_error, apply_safety_snapshot, classify_error
from app.telegram_gateway import TelegramGatewayError, TelegramProfile, gateway


def test_flood_wait_uses_exact_retry_and_peer_flood_is_estimated():
    now = datetime(2026, 8, 8, tzinfo=UTC)
    flood = classify_error("FLOOD_WAIT", retry_after=125, now=now)
    assert flood.quarantine is True
    assert flood.status == "cooldown"
    assert flood.cooldown_until == now + timedelta(seconds=125)

    peer = classify_error("PEER_FLOOD", now=now)
    assert peer.status == "limited"
    assert peer.cooldown_until == now + timedelta(hours=24)


def test_unread_spike_and_error_growth_trigger_autopause(paid_user):
    account = models.WarmupAccount(owner_id=paid_user.id, label="Safety", tg_id="909")
    baseline_at = datetime(2026, 8, 8, tzinfo=UTC)
    baseline = apply_safety_snapshot(
        account, unread_count=2, unread_dialogs=1, now=baseline_at
    )
    assert baseline.quarantine is False
    account.last_success_at = baseline_at + timedelta(minutes=1)
    spike = apply_safety_snapshot(
        account,
        unread_count=15,
        unread_dialogs=12,
        now=baseline_at + timedelta(minutes=2),
    )
    assert spike.quarantine is True
    assert account.operational_status == "cooldown"
    assert account.last_tg_error_code == "UNREAD_SPIKE"

    account.operational_status = "ready"
    account.cooldown_until = None
    for number in range(3):
        decision = apply_error(account, code="UNKNOWN", error=f"Ошибка {number}")
    assert decision.quarantine is True
    assert account.operational_status == "cooldown"


def test_unread_history_after_connect_does_not_trigger_autopause(paid_user):
    account = models.WarmupAccount(owner_id=paid_user.id, label="Fresh", tg_id="910")
    baseline = apply_safety_snapshot(account, unread_count=0, unread_dialogs=0)
    assert baseline.quarantine is False
    snapshot = apply_safety_snapshot(account, unread_count=38, unread_dialogs=12)
    assert snapshot.quarantine is False
    assert account.operational_status in (None, "ready")
    assert account.last_tg_error_code in (None, "")


def test_expired_account_returns_to_rotation(db, paid_user):
    account = models.WarmupAccount(
        owner_id=paid_user.id,
        label="Cooldown",
        tg_id="101",
        operational_status="cooldown",
        cooldown_until=datetime.now(UTC) - timedelta(seconds=1),
    )
    db.add(account)
    db.commit()

    picked = sending.pick_accounts(db, paid_user.id, [])
    assert [row.id for row in picked] == [account.id]
    assert account.operational_status == "ready"
    assert account.cooldown_until is None


def test_revoked_session_is_removed_from_rotation_and_requires_login(
    db, paid_user, monkeypatch
):
    account = models.WarmupAccount(
        owner_id=paid_user.id,
        label="Revoked",
        tg_id="404",
        connected_at=datetime.now(UTC),
        status="active",
    )
    db.add(account)
    db.commit()
    monkeypatch.setattr(
        gateway,
        "session_status",
        lambda *_args: {
            "authorized": False,
            "error_code": "AUTH_KEY_UNREGISTERED",
            "reason": "Сессия завершена в Telegram. Подключите аккаунт заново.",
        },
    )

    assert sending.pick_accounts(db, paid_user.id, []) == []
    db.refresh(account)
    assert account.operational_status == "reauth"
    assert account.connected_at is None
    assert account.status == "paused"


def test_gateway_outage_fails_closed_without_faking_session_logout(
    db, paid_user, monkeypatch
):
    connected_at = datetime.now(UTC)
    account = models.WarmupAccount(
        owner_id=paid_user.id,
        label="Temporary outage",
        tg_id="405",
        connected_at=connected_at,
    )
    db.add(account)
    db.commit()

    def unavailable(*_args):  # noqa: ANN202
        raise TelegramGatewayError("gateway недоступен")

    monkeypatch.setattr(gateway, "session_status", unavailable)
    assert sending.pick_accounts(db, paid_user.id, []) == []
    db.refresh(account)
    assert account.operational_status == "ready"
    assert account.connected_at is not None
    assert account.connected_at.replace(tzinfo=UTC) == connected_at


def test_pending_account_is_deleted_on_connect_cancel(
    client, paid_user, auth_headers, db
):
    created = client.post("/api/warmup/quick", headers=auth_headers, json={})
    assert created.status_code == 200, created.text
    account_id = created.json()["id"]

    cancelled = client.delete(
        f"/api/warmup/{account_id}/pending",
        headers=auth_headers,
    )
    assert cancelled.status_code == 200, cancelled.text
    assert db.get(models.WarmupAccount, account_id) is None
    assert db.query(models.WarmupAccount).filter_by(owner_id=paid_user.id).count() == 1


def test_pending_cancel_cannot_delete_account_after_identity(
    client, paid_user, auth_headers, db
):
    created = client.post("/api/warmup/quick", headers=auth_headers, json={})
    account_id = created.json()["id"]
    account = db.get(models.WarmupAccount, account_id)
    account.tg_id = "777"
    account.tg_username = "connected"
    account.connected_at = datetime.now(UTC)
    db.commit()

    cancelled = client.delete(
        f"/api/warmup/{account_id}/pending",
        headers=auth_headers,
    )
    assert cancelled.status_code == 409
    assert db.get(models.WarmupAccount, account_id) is not None


def test_delivery_quarantines_and_retries_same_lead_on_next_account(db, paid_user, monkeypatch):
    base = models.LeadDatabase(owner_id=paid_user.id, name="Rotation")
    lead = models.Lead(
        database=base,
        channel_id="UC-runtime-rotation",
        title="Lead",
        tg_status="verified",
        tg_username="lead_user",
    )
    offer = models.Offer(
        owner_id=paid_user.id,
        name="Offer",
        text="Hello",
        image="data:image/png;base64,AA==",
    )
    first = models.WarmupAccount(owner_id=paid_user.id, label="First", tg_id="1")
    second = models.WarmupAccount(owner_id=paid_user.id, label="Second", tg_id="2")
    campaign = models.Campaign(
        owner_id=paid_user.id,
        name="Runtime",
        database_name="Rotation",
        offer_mode="regular",
        status="running",
    )
    db.add_all([base, lead, offer, first, second, campaign])
    db.commit()

    calls: list[tuple[int, str]] = []

    def fake_send(_owner_id, account_id, _username, _text, **kwargs):  # noqa: ANN001, ANN202
        calls.append((account_id, kwargs.get("image", "")))
        if account_id == first.id:
            return sending.SendOutcome(False, "Подождите 2 минуты", "FLOOD_WAIT", 120)
        return sending.SendOutcome(True)

    monkeypatch.setattr(sending, "send_via_client", fake_send)
    result = sending.run_launch(db, campaign, [lead], [first, second], [offer])

    assert result["sent"] == 1
    assert result["failed"] == 0
    assert calls == [(first.id, offer.image), (second.id, offer.image)]
    db.refresh(first)
    assert first.operational_status == "cooldown"
    assert first.cooldown_until is not None
    history = db.query(models.CampaignSend).one()
    assert history.status == "sent"
    assert history.account_id == second.id


def test_delivery_finishes_cleanly_when_whole_pool_is_quarantined(db, paid_user, monkeypatch):
    base = models.LeadDatabase(owner_id=paid_user.id, name="All limited")
    lead = models.Lead(
        database=base,
        channel_id="UC-all-limited",
        tg_status="verified",
        tg_username="lead_user",
    )
    offer = models.Offer(owner_id=paid_user.id, name="Offer", text="Hello")
    account = models.WarmupAccount(owner_id=paid_user.id, label="Only", tg_id="20")
    campaign = models.Campaign(
        owner_id=paid_user.id,
        name="All limited",
        database_name="All limited",
        offer_mode="regular",
        status="running",
    )
    db.add_all([base, lead, offer, account, campaign])
    db.commit()
    monkeypatch.setattr(
        sending,
        "send_via_client",
        lambda *_args, **_kwargs: sending.SendOutcome(False, "Спам-лимит", "PEER_FLOOD"),
    )

    result = sending.run_launch(db, campaign, [lead], [account], [offer])
    assert result["sent"] == 0
    assert result["failed"] == 1
    assert campaign.status == "completed"
    db.refresh(account)
    assert account.operational_status == "limited"
    assert db.query(models.CampaignSend).one().status == "failed"


def test_recipient_error_does_not_quarantine_account(db, paid_user):
    account = models.WarmupAccount(owner_id=paid_user.id, label="Healthy", tg_id="3")
    db.add(account)
    db.commit()
    decision = apply_error(
        account,
        code="USER_PRIVACY_RESTRICTED",
        error="Получатель запретил сообщения",
    )
    assert decision.quarantine is False
    assert account.operational_status == "ready"


def test_verification_skips_account_quarantined_by_previous_lead(db, paid_user, monkeypatch):
    limited = models.WarmupAccount(
        owner_id=paid_user.id,
        label="Limited",
        tg_id="31",
        operational_status="limited",
    )
    ready = models.WarmupAccount(owner_id=paid_user.id, label="Ready", tg_id="32")
    base = models.LeadDatabase(owner_id=paid_user.id, name="Verification rotation")
    lead = models.Lead(
        database=base,
        channel_id="UC-verification-health",
        tg_username="candidate",
    )
    db.add_all([limited, ready, base, lead])
    db.commit()
    seen: list[int] = []

    def fake_verify(_owner_id, _handles, account_ids, **_kwargs):  # noqa: ANN001, ANN202
        seen.extend(account_ids)
        return TelegramProfile(
            ok=True,
            username="candidate",
            kind="user",
            account_id=account_ids[0],
        )

    monkeypatch.setattr(contacts.gateway, "verify_candidates", fake_verify)
    result = contacts.verify_lead(lead, paid_user.id, [limited.id, ready.id])
    assert result.sendable is True
    assert seen == [ready.id]


def test_warmup_list_contains_runtime_metrics(client, paid_user, auth_headers, db):
    account = models.WarmupAccount(
        owner_id=paid_user.id,
        label="Metrics",
        tg_id="4",
        risk_score=40,
    )
    base = models.LeadDatabase(owner_id=paid_user.id, name="Metrics")
    db.add_all([account, base])
    db.flush()
    lead = models.Lead(
        database_id=base.id,
        channel_id="UC-metrics-runtime",
        tg_verified_by_account_id=account.id,
        tg_checked_at=datetime.now(UTC),
    )
    failed_lead = models.Lead(
        database_id=base.id,
        channel_id="UC-metrics-runtime-failed",
    )
    campaign = models.Campaign(owner_id=paid_user.id, name="C", database_name="Metrics")
    db.add_all([lead, failed_lead, campaign])
    db.flush()
    db.add_all(
        [
            models.CampaignSend(
                owner_id=paid_user.id,
                campaign_id=campaign.id,
                lead_id=lead.id,
                account_id=account.id,
                status="sent",
            ),
            models.CampaignSend(
                owner_id=paid_user.id,
                campaign_id=campaign.id,
                lead_id=failed_lead.id,
                account_id=account.id,
                status="failed",
            ),
        ]
    )
    db.commit()

    response = client.get("/api/warmup", headers=auth_headers)
    assert response.status_code == 200, response.text
    row = next(item for item in response.json()["accounts"] if item["id"] == account.id)
    assert row["sent_today"] == 1
    assert row["failed_today"] == 1
    assert row["verified_today"] == 1
    assert row["daily_left"] == 29
    assert row["risk_score"] >= 40


def test_spambot_check_quarantines_and_clear_releases(
    client, paid_user, auth_headers, db, monkeypatch
):
    account = models.WarmupAccount(
        owner_id=paid_user.id,
        label="Spam check",
        tg_id="5",
        connected_at=datetime.now(UTC),
    )
    db.add(account)
    db.commit()

    monkeypatch.setattr(
        "app.routers.warmup.gateway.spambot_status",
        lambda *_args: {"ok": True, "status": "limited", "detail": "Ограничен"},
    )
    limited = client.post(f"/api/warmup/{account.id}/spambot/check", headers=auth_headers)
    assert limited.status_code == 200, limited.text
    db.refresh(account)
    assert account.operational_status == "limited"
    assert account.last_tg_error_code == "SPAMBOT_LIMITED"

    monkeypatch.setattr(
        "app.routers.warmup.gateway.spambot_status",
        lambda *_args: {"ok": True, "status": "clear", "detail": "Ограничений нет"},
    )
    clear = client.post(f"/api/warmup/{account.id}/spambot/check", headers=auth_headers)
    assert clear.status_code == 200, clear.text
    db.refresh(account)
    assert account.operational_status == "ready"
    assert account.spambot_status == "clear"
