"""v44: автовозобновление поиска и рассылки после рестарта + пуши в Telegram.

Поиск живёт в search_jobs и продолжается с первого непройденного слова;
рассылка хранит список получателей и продолжает с первого, кому не писали.
По завершении обоих владельцу уходит личное уведомление через бота.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

from app import models, sending
from app.routers import campaigns as campaigns_router
from app.routers import search as search_router


def _api_key(db, owner_id: int) -> models.ApiKey:
    key = models.ApiKey(owner_id=owner_id, key="AIza" + "x" * 35, daily_quota=10_000)
    db.add(key)
    db.commit()
    return key


def _fake_youtube(monkeypatch, *, per_keyword: int = 3) -> None:
    """YouTube из воздуха: каждой паре (слово, страница) — свои каналы."""

    def search_channels(_key, keyword, _page_token, _size, _region) -> tuple[list[str], str]:
        ids = [f"UC-{keyword}-{i}" for i in range(per_keyword)]
        return ids, ""

    def channels_details(_key, chunk) -> list[dict]:
        return [
            {
                "channel_id": cid,
                "title": f"Канал {cid}",
                "description": "Контакт: t.me/someone",
                "custom_url": "",
                "country": "KZ",
                "thumbnail": "",
                "subscribers": 5000,
                "video_count": 20,
                "view_count": 100_000,
                "published_at": "",
            }
            for cid in chunk
        ]

    monkeypatch.setattr(search_router.yt, "search_channels", search_channels)
    monkeypatch.setattr(search_router.yt, "channels_details", channels_details)


def test_поиск_идёт_прогоном_и_завершается_базой(client, db, paid_user, auth_headers, monkeypatch):
    _api_key(db, paid_user.id)
    _fake_youtube(monkeypatch)
    pushes: list[tuple[object, str]] = []
    monkeypatch.setattr(
        search_router.alerts,
        "notify_user",
        lambda tg_id, text, **_kw: pushes.append((tg_id, text)) or True,
    )

    response = client.post(
        "/api/search",
        headers=auth_headers,
        json={"keywords": ["криптообразование", "инвестиции"], "limit": 3, "wait": True},
    )
    assert response.status_code == 200
    job = response.json()
    assert job["status"] == "completed"
    assert job["keywords_done"] == 2
    assert job["found_count"] == 6
    assert job["database"]["leads_count"] == 6

    # Пуш владельцу ушёл и говорит о готовой базе.
    assert pushes and pushes[0][0] == paid_user.telegram_id
    assert "поиск завершён" in pushes[0][1]

    # Текущий прогон отдаётся отдельной ручкой — интерфейс подхватывает его после F5.
    current = client.get("/api/search/jobs/current", headers=auth_headers).json()
    assert current["id"] == job["id"]
    assert current["status"] == "completed"


def test_поиск_возобновляется_с_непройденного_слова(db, paid_user, monkeypatch):
    _api_key(db, paid_user.id)
    seen_keywords: list[str] = []

    def search_channels(_key, keyword, _page_token, _size, _region) -> tuple[list[str], str]:
        seen_keywords.append(keyword)
        return [f"UC-{keyword}-0"], ""

    monkeypatch.setattr(search_router.yt, "search_channels", search_channels)
    monkeypatch.setattr(
        search_router.yt,
        "channels_details",
        lambda _key, chunk: [
            {
                "channel_id": cid,
                "title": cid,
                "description": "",
                "custom_url": "",
                "country": "",
                "thumbnail": "",
                "subscribers": 5000,
                "video_count": 20,
                "view_count": 100_000,
                "published_at": "",
            }
            for cid in chunk
        ],
    )
    monkeypatch.setattr(search_router.alerts, "notify_user", lambda *_a, **_kw: True)

    payload = {"keywords": ["первое", "второе", "третье"], "limit": 1}
    job = models.SearchJob(
        owner_id=paid_user.id,
        status="running",  # «шёл» в момент рестарта
        payload=json.dumps(payload),
        limit_total=3,
        per_keyword=1,
        keywords_done=1,  # первое слово пройдено до рестарта
        found_json=json.dumps({"UC-первое-0": "первое"}),
    )
    db.add(job)
    db.commit()

    resumed = search_router.recover_search_jobs(db)
    assert resumed == [job.id]
    db.refresh(job)
    assert job.status == "queued"

    search_router._process_search_job(db, job.id)
    db.refresh(job)
    assert job.status == "completed"
    # Пройденное слово не искалось заново, найденное до рестарта не потеряно.
    assert seen_keywords == ["второе", "третье"]
    assert job.leads_count == 3


def test_второй_поиск_параллельно_не_запускается(client, db, paid_user, auth_headers):
    _api_key(db, paid_user.id)
    db.add(
        models.SearchJob(
            owner_id=paid_user.id,
            status="running",
            payload=json.dumps({"keywords": ["x"], "limit": 1}),
        )
    )
    db.commit()
    response = client.post(
        "/api/search",
        headers=auth_headers,
        json={"keywords": ["ещё"], "limit": 1},
    )
    assert response.status_code == 409


def _campaign_fixture(
    db, owner_id: int, lead_count: int = 3
) -> tuple[models.Campaign, list[models.Lead], models.WarmupAccount, models.Offer]:
    base = models.LeadDatabase(owner_id=owner_id, name="Resume")
    db.add(base)
    db.flush()
    leads = []
    for index in range(lead_count):
        lead = models.Lead(
            database_id=base.id,
            channel_id=f"UC-resume-{index}",
            title=f"Лид {index}",
            tg_status="verified",
            tg_username=f"lead_{index}",
        )
        db.add(lead)
        leads.append(lead)
    offer = models.Offer(owner_id=owner_id, name="Оффер", text="Привет")
    account = models.WarmupAccount(
        owner_id=owner_id,
        label="Аккаунт",
        tg_id="555",
        connected_at=datetime.now(UTC),
    )
    db.add_all([offer, account])
    db.flush()
    campaign = models.Campaign(
        owner_id=owner_id,
        name="После рестарта",
        database_name=base.name,
        status="running",
        total_recipients=lead_count,
        offer_ids=json.dumps([offer.id]),
        account_ids=json.dumps([account.id]),
        lead_ids=json.dumps([lead.id for lead in leads]),
        started_at=datetime.now(UTC),
    )
    db.add(campaign)
    db.commit()
    return campaign, leads, account, offer


def test_рестарт_готовит_рассылку_к_продолжению_а_не_хоронит(db, paid_user):
    campaign, leads, account, _offer = _campaign_fixture(db, paid_user.id)
    # Первому уже писали до рестарта.
    db.add(
        models.CampaignSend(
            campaign_id=campaign.id,
            owner_id=paid_user.id,
            lead_id=leads[0].id,
            account_id=account.id,
            status="sent",
            sent_at=datetime.now(UTC),
        )
    )
    campaign.sent_count = 1
    db.commit()

    interrupted, resume = campaigns_router.recover_interrupted(db)
    assert interrupted == 0
    assert resume == [campaign.id]
    db.refresh(campaign)
    assert campaign.status == "running"

    remaining = campaigns_router._remaining_lead_ids(
        db, campaign, campaigns_router._campaign_lead_ids(campaign)
    )
    assert remaining == [leads[1].id, leads[2].id]


def test_рестарт_помечает_прерванной_рассылку_без_списка_получателей(db, paid_user):
    campaign, _leads, _account, _offer = _campaign_fixture(db, paid_user.id)
    campaign.lead_ids = "[]"  # кампания времён до v44
    db.commit()

    interrupted, resume = campaigns_router.recover_interrupted(db)
    assert interrupted == 1
    assert resume == []
    db.refresh(campaign)
    assert campaign.status == "interrupted"


def test_возобновление_дошивает_остаток_и_шлёт_пуш(db, paid_user, monkeypatch):
    campaign, leads, account, _offer = _campaign_fixture(db, paid_user.id)
    db.add(
        models.CampaignSend(
            campaign_id=campaign.id,
            owner_id=paid_user.id,
            lead_id=leads[0].id,
            account_id=account.id,
            status="sent",
            sent_at=datetime.now(UTC),
        )
    )
    campaign.sent_count = 1
    db.commit()

    sent_to: list[str] = []
    monkeypatch.setattr(
        sending,
        "send_via_client",
        lambda _owner, _acc, username, _text, **_kw: sent_to.append(username)
        or sending.SendOutcome(True),
    )
    pushes: list[str] = []
    monkeypatch.setattr(
        sending.alerts,
        "notify_user",
        lambda _tg, text, **_kw: pushes.append(text) or True,
    )
    # Возобновление открывает собственную сессию — подсовываем тестовую.
    from sqlalchemy.orm import sessionmaker

    background_db = sessionmaker(bind=db.get_bind(), autoflush=False, autocommit=False)()
    monkeypatch.setattr(campaigns_router.database, "SessionLocal", lambda: background_db)

    campaigns_router._resume_in_background(campaign.id)

    db.refresh(campaign)
    assert campaign.status == "completed"
    assert campaign.progress == 100.0
    # Дошиты только второй и третий; первый не получил повторного письма.
    assert sent_to == ["lead_1", "lead_2"]
    assert campaign.sent_count == 3
    # Пуш говорит суммарные числа, а не числа одного куска.
    assert pushes and "3 из 3" in pushes[-1]


def test_возобновление_без_остатка_закрывает_кампанию(db, paid_user, monkeypatch):
    campaign, leads, account, _offer = _campaign_fixture(db, paid_user.id, lead_count=1)
    db.add(
        models.CampaignSend(
            campaign_id=campaign.id,
            owner_id=paid_user.id,
            lead_id=leads[0].id,
            account_id=account.id,
            status="sent",
            sent_at=datetime.now(UTC),
        )
    )
    db.commit()

    interrupted, resume = campaigns_router.recover_interrupted(db)
    assert interrupted == 0
    assert resume == []
    db.refresh(campaign)
    assert campaign.status == "completed"
    assert campaign.progress == 100.0


def test_ожидавшая_остановки_кампания_закрывается_рестартом(db, paid_user):
    campaign, _leads, _account, _offer = _campaign_fixture(db, paid_user.id)
    campaign.status = "cancel_requested"
    db.commit()

    interrupted, resume = campaigns_router.recover_interrupted(db)
    assert interrupted == 0
    assert resume == []
    db.refresh(campaign)
    assert campaign.status == "cancelled"


def test_завершение_рассылки_шлёт_пуш_владельцу(db, paid_user, monkeypatch):
    campaign, leads, account, offer = _campaign_fixture(db, paid_user.id, lead_count=1)
    monkeypatch.setattr(
        sending, "send_via_client", lambda *_a, **_kw: sending.SendOutcome(True)
    )
    pushes: list[tuple[object, str]] = []
    monkeypatch.setattr(
        sending.alerts,
        "notify_user",
        lambda tg_id, text, **_kw: pushes.append((tg_id, text)) or True,
    )

    sending.run_launch(db, campaign, leads, [account], [offer])

    assert pushes and pushes[0][0] == paid_user.telegram_id
    assert "завершена" in pushes[0][1]
    assert "1 из 1" in pushes[0][1]


def test_завершение_проверки_базы_шлёт_пуш_владельцу(db, paid_user, monkeypatch):
    from app.routers import leads as leads_router

    base = models.LeadDatabase(owner_id=paid_user.id, name="Проверенная")
    db.add(base)
    db.flush()
    job = models.LeadVerificationJob(
        owner_id=paid_user.id,
        database_id=base.id,
        status="running",
        total=4,
        processed=4,
        verified=3,
    )
    db.add(job)
    db.commit()

    pushes: list[str] = []
    monkeypatch.setattr(
        leads_router.alerts,
        "notify_user",
        lambda _tg, text, **_kw: pushes.append(text) or True,
    )
    leads_router._finish_verification_job(db, job, "completed")

    assert pushes and "3" in pushes[0] and "Проверенная" in pushes[0]
