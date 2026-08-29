"""Регрессии нового потока: парсинг → Telegram-верификация → отправка."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app import ai_offer, contacts, models, sending
from app.models import Lead
from app.routers import campaigns as campaign_routes
from app.routers.search import _matches_filters
from app.schemas import CampaignLaunchIn, SearchIn
from app.telegram_gateway import TelegramGateway, TelegramProfile


class _Response:
    def __init__(self, body: dict, status_code: int = 200) -> None:
        self._body = body
        self.status_code = status_code
        self.text = str(body)

    def json(self) -> dict:
        return self._body


def test_contact_parser_ranks_personal_contact_before_channel_and_bot():
    text = (
        "Наш канал: https://t.me/news_room. "
        "По сотрудничеству пишите автору: https://t.me/real_owner. "
        "Бот: t.me/order_helper_bot"
    )
    candidates = contacts.extract_telegram_candidates(text)
    assert [item.handle for item in candidates] == ["real_owner", "news_room", "order_helper_bot"]


def test_gateway_normalizes_nested_gramjs_profile(monkeypatch):
    def fake_post(url, json, headers, timeout) -> _Response:  # noqa: ANN001
        assert url.endswith("/resolve")
        assert json["account_id"] == 7
        return _Response(
            {
                "ok": True,
                "profile": {
                    "id": "123",
                    "username": "person",
                    "kind": "user",
                    "first_name": "Ada",
                    "last_name": "Lovelace",
                    "verified": True,
                },
            }
        )

    monkeypatch.setattr("app.telegram_gateway.httpx.post", fake_post)
    profile = TelegramGateway("http://gateway", "secret").resolve(1, "@person", 7)
    assert profile.sendable is True
    assert profile.tg_id == "123"
    assert profile.first_name == "Ada"
    assert profile.account_id == 7


def test_gateway_keeps_structured_flood_wait(monkeypatch):
    monkeypatch.setattr(
        "app.telegram_gateway.httpx.post",
        lambda *args, **kwargs: _Response(
            {
                "ok": False,
                "error": "Telegram просит подождать",
                "error_code": "FLOOD_WAIT",
                "retry_after": 42,
            },
            502,
        ),
    )
    profile = TelegramGateway("http://gateway", "secret").resolve(1, "person", 9)
    assert profile.ok is False
    assert profile.error_code == "FLOOD_WAIT"
    assert profile.retry_after == 42


def test_gateway_continues_after_channel_until_personal_contact(monkeypatch):
    gateway = TelegramGateway("http://gateway", "secret")
    seen: list[tuple[str, int | None]] = []

    def fake_resolve(owner_id, username, account_id=None) -> TelegramProfile:  # noqa: ANN001
        seen.append((username, account_id))
        if username == "brand_channel":
            return TelegramProfile(
                ok=True, username=username, kind="channel", account_id=account_id
            )
        return TelegramProfile(ok=True, username=username, kind="user", account_id=account_id)

    monkeypatch.setattr(gateway, "resolve", fake_resolve)
    result = gateway.verify_candidates(1, ["brand_channel", "owner_person"], [10, 20])
    assert result.sendable is True
    assert result.username == "owner_person"
    assert seen == [("brand_channel", 10), ("owner_person", 20)]


def test_gateway_discovers_public_owner_from_channel(monkeypatch):
    gateway = TelegramGateway("http://gateway", "secret")

    def fake_resolve(owner_id, username, account_id=None) -> TelegramProfile:  # noqa: ANN001
        kind = "channel" if username == "brand_channel" else "user"
        return TelegramProfile(ok=True, username=username, kind=kind, account_id=account_id)

    monkeypatch.setattr(gateway, "resolve", fake_resolve)
    monkeypatch.setattr(
        gateway,
        "contact_candidates",
        lambda *args, **kwargs: ["public_owner"],
    )
    result = gateway.verify_candidates(1, ["brand_channel"], [10])
    assert result.sendable is True
    assert result.username == "public_owner"


def test_search_filters_require_real_matching_channel():
    payload = SearchIn(
        keywords=["design"],
        min_subscribers=1_000,
        max_subscribers=50_000,
        min_views=10_000,
        min_average_views=5_000,
        min_view_subscriber_ratio=10,
        min_videos=5,
        max_channel_age_days=365,
        country="KZ",
        require_telegram_link=True,
    )
    now = datetime.now(UTC)
    item = {
        "subscribers": 12_000,
        "view_count": 500_000,
        "video_count": 40,
        "published_at": (now - timedelta(days=100)).isoformat(),
        "country": "KZ",
        "description": "Связь: https://t.me/channel_owner",
        "title": "Дизайн",
    }
    assert _matches_filters(item, payload, now) is True
    assert _matches_filters({**item, "subscribers": 100}, payload, now) is False
    assert _matches_filters({**item, "view_count": 20_000}, payload, now) is False
    assert _matches_filters({**item, "description": "без контакта"}, payload, now) is False


def test_search_filters_reject_inverted_subscriber_range():
    with pytest.raises(ValueError, match="максимум подписчиков"):
        SearchIn(keywords=["test"], min_subscribers=100, max_subscribers=10)


def test_verify_lead_persists_only_sendable_user(monkeypatch):
    lead = Lead(
        channel_id="UC1",
        title="Канал",
        description="Для связи: t.me/owner_person",
        tg_status="pending",
    )
    monkeypatch.setattr(
        contacts.gateway,
        "verify_candidates",
        lambda *args, **kwargs: TelegramProfile(
            ok=True,
            username="owner_person",
            tg_id="42",
            kind="user",
            first_name="Иван",
            account_id=9,
        ),
    )
    result = contacts.verify_lead(lead, 1, [9])
    assert result.sendable is True
    assert lead.tg_status == "verified"
    assert lead.tg_username == "owner_person"
    assert lead.tg_verified_by_account_id == 9
    assert sending.recipient_username(lead) == "owner_person"


def test_pending_or_channel_lead_is_not_sendable():
    pending = Lead(channel_id="UC2", tg_username="candidate", tg_status="pending")
    channel = Lead(channel_id="UC3", tg_username="channel", tg_status="channel")
    assert sending.recipient_username(pending) == ""
    assert sending.recipient_username(channel) == ""


def test_one_click_verification_processes_the_whole_database(
    client, paid_user, auth_headers, db, monkeypatch
):
    base = models.LeadDatabase(owner_id=paid_user.id, name="Полная проверка")
    db.add(base)
    db.flush()
    for index in range(3):
        db.add(
            models.Lead(
                database_id=base.id,
                channel_id=f"UC-auto-{index}",
                title=f"Канал {index}",
                description=f"Для связи: t.me/owner_{index}",
                tg_status="pending",
                score=90 - index,
            )
        )
    account = models.WarmupAccount(
        owner_id=paid_user.id,
        label="Проверяющий аккаунт",
        tg_id="900",
        tg_username="sender",
        verification_enabled=True,
    )
    db.add(account)
    db.commit()

    def verified_profile(
        owner_id: int,
        handles: list[str],
        account_ids: list[int],
        start_index: int = 0,
    ) -> TelegramProfile:
        username = handles[0]
        return TelegramProfile(
            ok=True,
            username=username,
            tg_id=f"tg-{username}",
            kind="user",
            first_name="Автор",
            account_id=account_ids[start_index % len(account_ids)],
        )

    monkeypatch.setattr(contacts.gateway, "verify_candidates", verified_profile)
    response = client.post(
        f"/api/leads/databases/{base.id}/verification/start",
        json={"wait": True},
        headers=auth_headers,
    )
    assert response.status_code == 200
    job = response.json()
    assert job["status"] == "completed"
    assert job["total"] == 3
    assert job["processed"] == 3
    assert job["verified"] == 3
    assert job["progress"] == 100

    summary = client.get(
        f"/api/leads/databases/{base.id}/telegram-summary",
        headers=auth_headers,
    ).json()
    assert summary["pending"] == 0
    assert summary["verified"] == 3
    assert summary["job"]["status"] == "completed"


def test_verification_can_be_retried_without_batch_limit(
    client, paid_user, auth_headers, db, monkeypatch
):
    base = models.LeadDatabase(owner_id=paid_user.id, name="Повтор ошибок")
    db.add(base)
    db.flush()
    lead = models.Lead(
        database_id=base.id,
        channel_id="UC-retry",
        title="Канал с временной ошибкой",
        description="Контакт: t.me/retry_owner",
        tg_status="error",
        tg_error="Временная ошибка gateway",
    )
    db.add(lead)
    account = models.WarmupAccount(
        owner_id=paid_user.id,
        label="Аккаунт",
        tg_id="901",
        verification_enabled=True,
    )
    db.add(account)
    db.commit()

    monkeypatch.setattr(
        contacts.gateway,
        "verify_candidates",
        lambda *args, **kwargs: TelegramProfile(
            ok=True,
            username="retry_owner",
            tg_id="77",
            kind="user",
            account_id=account.id,
        ),
    )
    response = client.post(
        f"/api/leads/databases/{base.id}/verification/start",
        json={"retry_errors": True, "wait": True},
        headers=auth_headers,
    )
    assert response.status_code == 200
    assert response.json()["verified"] == 1
    db.refresh(lead)
    assert lead.tg_status == "verified"
    assert lead.tg_username == "retry_owner"


def test_ai_offer_rejects_unresolved_placeholder(monkeypatch):
    monkeypatch.setattr(
        ai_offer.httpx,
        "post",
        lambda *args, **kwargs: _Response(
            {
                "choices": [
                    {
                        "message": {
                            "content": "Здравствуйте! {unknown_field} Обсудим сотрудничество по вашему каналу?"
                        }
                    }
                ]
            }
        ),
    )
    lead = Lead(channel_id="UC4", title="Канал")
    try:
        ai_offer.generate(
            "Шаблон",
            lead,
            base_url="http://ai",
            api_key="key",
            model="model",
        )
    except ai_offer.AiOfferError as exc:
        assert "{unknown_field}" in str(exc)
    else:
        raise AssertionError("Незаполненный плейсхолдер должен отклоняться")


def test_campaign_verifies_pending_lead_before_send(
    client, paid_user, auth_headers, db, monkeypatch
):
    base = models.LeadDatabase(owner_id=paid_user.id, name="Новая база")
    db.add(base)
    db.flush()
    lead = models.Lead(
        database_id=base.id,
        channel_id="UC-pending",
        title="Канал",
        description="По сотрудничеству: t.me/owner_person",
        score=90,
        tg_status="pending",
        telegram_channel="owner_person",
    )
    db.add(lead)
    db.add(models.Offer(owner_id=paid_user.id, name="Оффер", text="Здравствуйте!"))
    account = models.WarmupAccount(
        owner_id=paid_user.id,
        label="Аккаунт",
        tg_id="900",
        tg_username="sender",
    )
    db.add(account)
    db.commit()

    monkeypatch.setattr(
        contacts.gateway,
        "verify_candidates",
        lambda *args, **kwargs: TelegramProfile(
            ok=True,
            username="owner_person",
            tg_id="42",
            kind="user",
            first_name="Иван",
            account_id=account.id,
        ),
    )
    sent: list[tuple[int, str]] = []

    def fake_send(owner_id, account_id, username, text, **kwargs) -> tuple[bool, str]:  # noqa: ANN001
        sent.append((account_id, username))
        return True, ""

    monkeypatch.setattr(sending, "send_via_client", fake_send)
    response = client.post(
        "/api/campaigns/launch",
        headers=auth_headers,
        json={"database_id": base.id, "offer_mode": "regular", "wait": True},
    )
    assert response.status_code == 200, response.text
    assert response.json()["sent"] == 1
    assert sent == [(account.id, "owner_person")]
    db.refresh(lead)
    assert lead.tg_status == "verified"
    assert lead.tg_verified_by_account_id == account.id


def test_warmup_settings_and_manual_day(client, paid_user, auth_headers):
    created = client.post(
        "/api/warmup/quick",
        headers=auth_headers,
        json={
            "label": "Рабочий аккаунт",
            "plan_days": 3,
            "daily_send_limit": 17,
            "delay_min_seconds": 25,
            "delay_max_seconds": 45,
            "sending_enabled": False,
            "verification_enabled": True,
        },
    )
    assert created.status_code == 200, created.text
    account = created.json()
    assert account["daily_send_limit"] == 17
    assert account["sending_enabled"] is False
    assert 0 <= account["health_score"] <= 100
    assert account["health_label"] == "Требует настройки"
    assert any("подключите Telegram" in reason for reason in account["health_reasons"])

    day = client.patch(
        f"/api/warmup/{account['id']}/days/1",
        headers=auth_headers,
        json={"done": True, "done_messages": 4, "note": "без ограничений"},
    )
    assert day.status_code == 200, day.text
    assert day.json()["logs"][0]["done"] is True


def test_quick_account_waits_for_auto_warmup_toggle(client, paid_user, auth_headers, db):
    created = client.post("/api/warmup/quick", headers=auth_headers, json={})
    assert created.status_code == 200, created.text
    account = created.json()
    assert account["status"] == "paused"
    assert account["started_on"] is None
    assert account["plan_days"] == 14
    assert all(day["date"] is None for day in account["logs"])

    enabled = client.post(
        f"/api/warmup/{account['id']}/toggle",
        headers=auth_headers,
        json={"on": True},
    )
    assert enabled.status_code == 409

    # Прогрев и рабочие действия становятся доступны только после identity
    # callback от реально авторизованной Telegram-сессии.
    row = db.get(models.WarmupAccount, account["id"])
    row.tg_id = "902"
    row.connected_at = datetime.now(UTC)
    db.commit()
    enabled = client.post(
        f"/api/warmup/{account['id']}/toggle",
        headers=auth_headers,
        json={"on": True},
    )
    assert enabled.status_code == 200, enabled.text
    body = enabled.json()
    assert body["status"] == "active"
    assert body["started_on"] is not None
    assert body["daily_send_limit"] == 30
    assert body["delay_min_seconds"] == 20
    assert body["delay_max_seconds"] == 60
    assert body["sending_enabled"] is True
    assert body["verification_enabled"] is True
    assert all(day["date"] is not None for day in body["logs"])


def test_login_telegram_profile_counts_as_primary_account(client, paid_user, auth_headers, db):
    listed = client.get("/api/warmup", headers=auth_headers)
    assert listed.status_code == 200, listed.text
    accounts = listed.json()["accounts"]
    assert len(accounts) == 1
    primary = accounts[0]
    assert primary["is_primary"] is True
    assert primary["label"] == "Test"
    assert primary["tg_username"] == "tester"
    assert primary["connected"] is False
    assert primary["status"] == "paused"
    protected = client.delete(f"/api/warmup/{primary['id']}", headers=auth_headers)
    assert protected.status_code == 409

    # Telegram Login подтверждает профиль, но ещё не даёт gateway-сессию.
    assert sending.pick_accounts(db, paid_user.id, []) == []
    row = db.get(models.WarmupAccount, primary["id"])
    row.connected_at = datetime.now(UTC)
    db.commit()
    assert [account.id for account in sending.pick_accounts(db, paid_user.id, [])] == [row.id]

    added = client.post("/api/warmup/quick", headers=auth_headers, json={})
    assert added.status_code == 200, added.text
    assert added.json()["is_primary"] is False
    assert db.query(models.WarmupAccount).filter_by(owner_id=paid_user.id).count() == 2


def test_dashboard_reports_pipeline_and_real_delivery_metrics(client, paid_user, auth_headers, db):
    base = models.LeadDatabase(owner_id=paid_user.id, name="Метрики")
    db.add(base)
    db.flush()
    verified = models.Lead(database_id=base.id, channel_id="UC-v", title="V", tg_status="verified")
    pending = models.Lead(database_id=base.id, channel_id="UC-p", title="P", tg_status="pending")
    db.add_all([verified, pending])
    db.flush()
    campaign = models.Campaign(owner_id=paid_user.id, name="Кампания", database_name=base.name)
    db.add(campaign)
    db.flush()
    db.add(
        models.CampaignSend(
            owner_id=paid_user.id,
            campaign_id=campaign.id,
            lead_id=verified.id,
            status="sent",
            replied_at=datetime.now(UTC),
        )
    )
    db.commit()

    response = client.get("/api/stats", headers=auth_headers)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["verified_telegram"] == 1
    assert body["pending_telegram"] == 1
    assert body["sent_messages"] == 1
    assert body["replies"] == 1
    assert body["reply_rate"] == 100.0


def test_cancelled_campaign_stops_before_next_send(db, paid_user, monkeypatch):
    base = models.LeadDatabase(owner_id=paid_user.id, name="Остановка")
    db.add(base)
    db.flush()
    lead = models.Lead(
        database_id=base.id,
        channel_id="UC-stop",
        title="Стоп",
        tg_status="verified",
        tg_username="personal_user",
    )
    offer = models.Offer(owner_id=paid_user.id, name="Оффер", text="Привет")
    account = models.WarmupAccount(
        owner_id=paid_user.id,
        label="Аккаунт",
        tg_id="1",
        tg_username="sender",
        daily_send_limit=10,
    )
    campaign = models.Campaign(
        owner_id=paid_user.id,
        name="Кампания",
        database_name=base.name,
        status="cancel_requested",
        offer_mode="regular",
    )
    db.add_all([lead, offer, account, campaign])
    db.commit()

    monkeypatch.setattr(
        sending,
        "send_via_client",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("send called")),
    )
    result = sending.run_launch(db, campaign, [lead], [account], [offer])
    assert result["sent"] == 0
    assert campaign.status == "cancelled"
    assert campaign.progress == 0


def test_emergency_stop_marks_every_running_campaign(client, paid_user, auth_headers, db):
    active = models.Campaign(
        owner_id=paid_user.id,
        name="Активная",
        database_name="База",
        status="running",
    )
    paused = models.Campaign(
        owner_id=paid_user.id,
        name="Пауза",
        database_name="База",
        status="paused",
    )
    db.add_all([active, paused])
    db.commit()

    response = client.post("/api/campaigns/stop-all", headers=auth_headers)
    assert response.status_code == 200, response.text
    assert response.json() == {"stopped": 1, "campaign_ids": [active.id]}
    db.refresh(active)
    db.refresh(paused)
    assert active.status == "cancel_requested"
    assert paused.status == "paused"


def test_launch_verification_finds_connected_account_without_legacy_status(
    db, paid_user, monkeypatch
):
    base = models.LeadDatabase(owner_id=paid_user.id, name="Connected accounts")
    db.add(base)
    db.flush()
    lead = models.Lead(
        database_id=base.id,
        channel_id="UC-connected-account",
        tg_username="candidate",
        tg_status="pending",
    )
    account = models.WarmupAccount(
        owner_id=paid_user.id,
        label="Verifier",
        tg_id="100500",
        verification_enabled=True,
    )
    db.add_all([lead, account])
    db.commit()

    seen: list[int] = []

    def fake_verify(_lead, _owner_id, account_ids, **_kwargs):  # noqa: ANN001, ANN202
        seen.extend(account_ids)
        return TelegramProfile(ok=True, username="candidate", kind="user")

    monkeypatch.setattr(contacts, "verify_lead", fake_verify)
    attempted, verified = campaign_routes._verify_launch_candidates(
        db,
        paid_user,
        CampaignLaunchIn(database_id=base.id, limit=1),
        base,
        [],
    )

    assert attempted == 1
    assert verified == 1
    assert seen == [account.id]
