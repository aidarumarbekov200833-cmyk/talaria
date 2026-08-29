"""Нейрокомментинг целиком: API, воркер и общий котёл на живой базе.

Отдельно от юнит-тестов допуска: здесь проверяется, что правила действительно
работают в связке — что комментарий уменьшает тот же лимит, что и рассылка, и
что прогон останавливается по состоянию аккаунта, а не «когда-нибудь потом».
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app import models, neuro_comment, neuro_worker
from app.telegram_gateway import TelegramGatewayError, gateway

NOW = datetime.now(UTC)


@pytest.fixture
def account(db, user):
    acc = models.WarmupAccount(
        owner_id=user.id,
        label="Основной",
        connected_at=NOW - timedelta(days=120),
        daily_send_limit=30,
        sending_enabled=True,
        operational_status="ready",
        tg_username="tester",
    )
    db.add(acc)
    db.commit()
    db.refresh(acc)
    return acc


@pytest.fixture
def campaign(db, user):
    """Кампания-заглушка: campaign_sends не существует без кампании."""
    row = models.Campaign(
        owner_id=user.id, name="Прошлая рассылка", database_name="база", status="completed"
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def add_send(db, user, account, campaign, *, sent_at=None):
    """Одна запись об отправке в личку.

    Каждой нужен свой лид: у campaign_sends уникальная пара (campaign_id,
    lead_id), и без этого 30 отправок превратились бы в одну.
    """
    database = db.query(models.LeadDatabase).filter_by(owner_id=user.id).first()
    if not database:
        database = models.LeadDatabase(owner_id=user.id, name="база")
        db.add(database)
        db.flush()
    index = db.query(models.Lead).count() + 1
    lead = models.Lead(database_id=database.id, channel_id=f"ch{index}", title=f"Лид {index}")
    db.add(lead)
    db.flush()
    db.add(
        models.CampaignSend(
            owner_id=user.id,
            campaign_id=campaign.id,
            lead_id=lead.id,
            account_id=account.id,
            status="sent",
            sent_at=sent_at or NOW,
        )
    )


@pytest.fixture
def target(db, user):
    row = models.NeuroTarget(
        owner_id=user.id,
        username="ai_chat",
        title="Чат про ИИ",
        kind="channel",
        status="approved",
        joined=True,
        linked_chat_id="-1001",
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


@pytest.fixture
def run(db, user, account):
    row = models.NeuroRun(
        owner_id=user.id, name="Нейрокомментинг", account_id=account.id, daily_limit=10
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


@pytest.fixture
def stub_ai(monkeypatch):
    """ИИ подменён: тесты проверяют конвейер, а не качество текста модели."""
    texts = iter(f"а на каком железе это гонял, вариант {i}" for i in range(100))
    monkeypatch.setattr(neuro_comment, "generate", lambda *a, **k: next(texts))


# --- общий котёл на живой базе --------------------------------------------


def test_actions_today_counts_dm_and_comments_together(db, user, account, campaign):
    """Ради этого правила фича и делалась: два источника, один счётчик."""
    assert neuro_worker.actions_today(db, account.id) == 0

    add_send(db, user, account, campaign)
    db.add(
        models.NeuroComment(
            owner_id=user.id, account_id=account.id, status="sent", text="привет"
        )
    )
    db.commit()
    assert neuro_worker.actions_today(db, account.id) == 2


def test_failed_comment_does_not_eat_the_daily_pool(db, user, account):
    db.add(
        models.NeuroComment(
            owner_id=user.id, account_id=account.id, status="failed", text="не ушло"
        )
    )
    db.commit()
    assert neuro_worker.actions_today(db, account.id) == 0


def test_yesterday_does_not_count(db, user, account, campaign):
    add_send(db, user, account, campaign, sent_at=NOW - timedelta(days=1, hours=1))
    db.commit()
    assert neuro_worker.actions_today(db, account.id) == 0


def test_running_campaign_marks_account_busy(db, user, account):
    db.add(
        models.Campaign(
            owner_id=user.id,
            name="Кампания",
            database_name="база",
            status="running",
            account_ids=f"[{account.id}]",
        )
    )
    db.commit()
    assert neuro_worker.account_busy_with_campaign(db, user.id, account.id) is True
    assert neuro_worker.account_busy_with_campaign(db, user.id, account.id + 999) is False


def test_completed_campaign_does_not_block(db, user, account):
    db.add(
        models.Campaign(
            owner_id=user.id,
            name="Кампания",
            database_name="база",
            status="completed",
            account_ids=f"[{account.id}]",
        )
    )
    db.commit()
    assert neuro_worker.account_busy_with_campaign(db, user.id, account.id) is False


# --- выбор поста -----------------------------------------------------------


def test_pick_post_skips_commented_own_and_empty():
    posts = [
        {"id": 10, "text": "старый", "out": False},
        {"id": 11, "text": "", "out": False},
        {"id": 12, "text": "мой пост", "out": True},
        {"id": 13, "text": "свежий текст", "out": False},
    ]
    assert neuro_worker.pick_post(posts, last_post_id=10)["id"] == 13
    assert neuro_worker.pick_post(posts, last_post_id=13) is None


# --- прогон ----------------------------------------------------------------


def test_run_once_sends_comment_and_records_it(db, run, target, account, stub_ai, monkeypatch):
    monkeypatch.setattr(
        gateway, "neuro_posts", lambda *a, **k: [{"id": 55, "text": "пост про обучение"}]
    )
    sent = {}

    def fake_comment(owner_id, account_id, username, text, *, post_id, kind) -> dict:
        sent.update(username=username, text=text, post_id=post_id, kind=kind)
        return {"message_id": 777}

    monkeypatch.setattr(gateway, "neuro_comment", fake_comment)

    stats = neuro_worker.run_once(db, run, sleeper=lambda _s: None, respect_hours=False)

    assert stats["sent"] == 1
    assert sent["username"] == "ai_chat"
    assert sent["post_id"] == 55
    assert sent["kind"] == "channel"

    db.refresh(target)
    assert target.last_post_id == 55
    assert target.comments_sent == 1

    log = db.query(models.NeuroComment).one()
    assert log.status == "sent"
    assert log.message_id == 777
    # Комментарий сразу же уменьшает общий котёл аккаунта.
    assert neuro_worker.actions_today(db, account.id) == 1


def test_run_once_refuses_when_campaign_is_running(db, user, run, target, account, stub_ai):
    db.add(
        models.Campaign(
            owner_id=user.id,
            name="Кампания",
            database_name="база",
            status="running",
            account_ids=f"[{account.id}]",
        )
    )
    db.commit()
    stats = neuro_worker.run_once(db, run, sleeper=lambda _s: None, respect_hours=False)
    assert stats["sent"] == 0
    assert "рассылк" in stats["stopped"]


def test_run_once_stops_when_pool_is_spent_by_dm(
    db, user, run, target, account, campaign, stub_ai
):
    """30 личных отправок = комментировать сегодня больше нечем."""
    for _ in range(30):
        add_send(db, user, account, campaign)
    db.commit()
    stats = neuro_worker.run_once(db, run, sleeper=lambda _s: None, respect_hours=False)
    assert stats["sent"] == 0
    assert "лимит" in stats["stopped"].lower()


def test_peer_flood_stops_run_and_quarantines_account(
    db, run, target, account, stub_ai, monkeypatch
):
    monkeypatch.setattr(gateway, "neuro_posts", lambda *a, **k: [{"id": 5, "text": "пост"}])

    def boom(*_a, **_k) -> None:
        raise TelegramGatewayError("Слишком много жалоб", code="PEER_FLOOD")

    monkeypatch.setattr(gateway, "neuro_comment", boom)
    stats = neuro_worker.run_once(db, run, sleeper=lambda _s: None, respect_hours=False)

    assert stats["failed"] == 1
    assert "остановлен" in stats["stopped"].lower()
    db.refresh(account)
    assert account.operational_status != "ready"
    assert account.cooldown_until is not None


def test_unusable_comment_is_skipped_not_sent(db, run, target, monkeypatch):
    monkeypatch.setattr(gateway, "neuro_posts", lambda *a, **k: [{"id": 5, "text": "пост"}])

    def refuse(*_a, **_k) -> None:
        raise neuro_comment.NeuroCommentError("не удалось получить живой комментарий")

    monkeypatch.setattr(neuro_comment, "generate", refuse)
    called = []
    monkeypatch.setattr(gateway, "neuro_comment", lambda *a, **k: called.append(1))

    stats = neuro_worker.run_once(db, run, sleeper=lambda _s: None, respect_hours=False)
    assert stats["skipped"] == 1
    assert not called


def test_run_without_joined_targets_reports_reason(db, run, target, stub_ai):
    target.joined = False
    db.commit()
    stats = neuro_worker.run_once(db, run, sleeper=lambda _s: None, respect_hours=False)
    assert "площадок" in stats["stopped"]


# --- HTTP ------------------------------------------------------------------


def test_targets_require_plan(client, auth_headers, user, db):
    response = client.post("/api/neuro/targets", json={"username": "ai_chat"}, headers=auth_headers)
    assert response.status_code == 403
    assert "тариф" in response.json()["detail"]


def test_target_lifecycle(client, auth_headers, paid_user, account, db, monkeypatch):
    monkeypatch.setattr(
        gateway,
        "neuro_info",
        lambda *a, **k: {
            "id": "-100500",
            "title": "Чат про ИИ",
            "kind": "channel",
            "participants": 1200,
            "joined": False,
            "slowmode_seconds": 30,
            "linked_chat_id": "-1001",
        },
    )
    created = client.post(
        "/api/neuro/targets", json={"username": "ai_chat"}, headers=auth_headers
    )
    assert created.status_code == 200, created.text
    body = created.json()
    assert body["slowmode_seconds"] == 30
    assert body["can_comment"] is True

    # Повторное добавление той же площадки — конфликт, а не второй ряд в базе.
    again = client.post("/api/neuro/targets", json={"username": "ai_chat"}, headers=auth_headers)
    assert again.status_code == 409

    listed = client.get("/api/neuro/targets", headers=auth_headers).json()["targets"]
    assert [t["username"] for t in listed] == ["ai_chat"]

    target_id = body["id"]
    paused = client.post(
        f"/api/neuro/targets/{target_id}/status", json={"status": "paused"}, headers=auth_headers
    )
    assert paused.json()["status"] == "paused"

    assert client.delete(f"/api/neuro/targets/{target_id}", headers=auth_headers).status_code == 200
    assert client.get("/api/neuro/targets", headers=auth_headers).json()["targets"] == []


def test_channel_without_discussion_cannot_be_commented(
    client, auth_headers, paid_user, account, monkeypatch
):
    monkeypatch.setattr(
        gateway,
        "neuro_info",
        lambda *a, **k: {
            "id": "-100500",
            "title": "Канал без обсуждений",
            "kind": "channel",
            "joined": True,
            "slowmode_seconds": 0,
            "linked_chat_id": "",
        },
    )
    body = client.post(
        "/api/neuro/targets", json={"username": "silent_channel"}, headers=auth_headers
    ).json()
    assert body["can_comment"] is False


def test_search_returns_candidates_and_never_joins(
    client, auth_headers, paid_user, account, monkeypatch
):
    """Поиск не должен вступать: joinChannel — только по кнопке человека."""
    monkeypatch.setattr(
        gateway,
        "neuro_search",
        lambda *a, **k: [
            {"username": "ai_chat", "title": "ИИ", "kind": "group", "participants": 900},
        ],
    )
    joined = []
    monkeypatch.setattr(gateway, "neuro_join", lambda *a, **k: joined.append(1))

    response = client.post("/api/neuro/search", json={"query": "нейросети"}, headers=auth_headers)
    assert response.status_code == 200
    data = response.json()
    assert data["results"][0]["username"] == "ai_chat"
    assert "реклама запрещена" in data["note"]
    assert not joined
    # Кандидаты не сохраняются как цели автоматически.
    assert client.get("/api/neuro/targets", headers=auth_headers).json()["targets"] == []


def test_budget_endpoint_shows_shared_pool(
    client, auth_headers, paid_user, account, campaign, db
):
    add_send(db, paid_user, account, campaign)
    db.commit()
    body = client.get("/api/neuro/budget", headers=auth_headers).json()
    assert body["used_today"] == 1
    assert body["tier"] == "mature"
    assert body["account_id"] == account.id


def test_start_requires_joined_targets(client, auth_headers, paid_user, account, db):
    db.add(
        models.NeuroTarget(
            owner_id=paid_user.id, username="ai_chat", status="approved", joined=False
        )
    )
    db.commit()
    response = client.post("/api/neuro/start", headers=auth_headers)
    assert response.status_code == 400
    assert "вступил" in response.json()["detail"]
