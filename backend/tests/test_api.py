"""Smoke-тесты HTTP-эндпоинтов через TestClient с БД в памяти.

Проверяем, что публичные ручки поднимаются и отвечают ожидаемой формой, а
защищённые честно требуют токен. Не мокаем внешние сервисы (YouTube,
tg-mini-client, ИИ) — те ручки, что в них ходят, здесь не дёргаем.
"""

from __future__ import annotations

import pytest

# --------------------------- публичные ручки --------------------------------


def test_health(client):
    r = client.get("/api/health", headers={"X-Request-Id": "bad request id"})
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}
    assert " " not in r.headers["x-request-id"]
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["x-frame-options"] == "SAMEORIGIN"
    assert "camera=()" in r.headers["permissions-policy"]


def test_admin_overview_counts_connected_warmup_accounts(client, db):
    from datetime import UTC, datetime

    from app import models
    from app.security import create_jwt

    admin = models.User(telegram_id=2002, username="admin_user", first_name="Admin")
    db.add(admin)
    db.add(models.AccessWhitelist(username="admin_user", note="admin test"))
    db.flush()
    db.add_all(
        [
            models.WarmupAccount(owner_id=admin.id, label="Connected by id", tg_id="101"),
            models.WarmupAccount(
                owner_id=admin.id,
                label="Connected callback",
                connected_at=datetime.now(UTC),
            ),
            models.WarmupAccount(owner_id=admin.id, label="Disconnected"),
        ]
    )
    db.commit()
    token, _ = create_jwt(admin.id)

    response = client.get(
        "/api/admin/overview",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200, response.text
    assert response.json()["connected_accounts"] == 2


def test_websocket_accepts_jwt_subprotocol_without_url_token(client, monkeypatch):
    """JWT WebSocket-а не должен оказываться в URL и access-логах прокси."""
    from app.routers import ws as ws_router

    monkeypatch.setattr(
        ws_router, "_authenticate", lambda token: 7 if token == "jwt-token" else None
    )
    with client.websocket_connect("/api/ws", subprotocols=["zephyr.jwt", "jwt-token"]) as socket:
        assert socket.accepted_subprotocol == "zephyr.jwt"
        assert socket.receive_json() == {"type": "hello"}


def test_oversized_json_is_rejected_with_security_headers(client):
    response = client.post(
        "/api/offers",
        headers={"Content-Length": "3000000"},
        content=b"{}",
    )
    assert response.status_code == 413
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "SAMEORIGIN"


def test_config_not_demo_when_bot_configured(client):
    r = client.get("/api/config")
    assert r.status_code == 200
    body = r.json()
    # BOT_TOKEN/BOT_USERNAME заданы в conftest → не демо-режим
    assert body["demo_mode"] is False
    assert body["bot_username"] == "test_bot"


def test_telegram_login_explains_whitelist_denial(client):
    import hashlib
    import hmac
    import time

    from app.config import settings

    payload = {
        "id": 990001,
        "first_name": "Outside",
        "username": "outside_user",
        "auth_date": int(time.time()),
    }
    pairs = sorted(f"{key}={value}" for key, value in payload.items())
    secret = hashlib.sha256(settings.bot_token.encode()).digest()
    payload["hash"] = hmac.new(
        secret,
        "\n".join(pairs).encode(),
        hashlib.sha256,
    ).hexdigest()

    # Регистрация закрыта — только белый список.
    settings.open_registration = False
    try:
        response = client.post("/api/auth/telegram", json=payload)
    finally:
        settings.open_registration = True
    assert response.status_code == 403
    assert "Вам не разрешено входить в Talaria" in response.json()["detail"]
    assert "списке доступа" in response.json()["detail"]


def test_open_registration_grants_trial_without_sending(client):
    """Новый человек входит сам и получает пробный доступ, но не рассылку."""
    import hashlib
    import hmac
    import time

    from app.config import settings

    payload = {
        "id": 990002,
        "first_name": "Newcomer",
        "username": "newcomer_user",
        "auth_date": int(time.time()),
    }
    pairs = sorted(f"{key}={value}" for key, value in payload.items())
    secret = hashlib.sha256(settings.bot_token.encode()).digest()
    payload["hash"] = hmac.new(secret, "\n".join(pairs).encode(), hashlib.sha256).hexdigest()

    response = client.post("/api/auth/telegram", json=payload)
    assert response.status_code == 200
    token = response.json()["access_token"]

    sub = client.get("/api/subscription", headers={"Authorization": f"Bearer {token}"})
    assert sub.status_code == 200
    body = sub.json()
    assert body["plan"] == "trial"
    assert body["is_trial"] is True
    assert body["sending"] is False
    assert body["trial_days_left"] > 0

    # Рассылка на пробном тарифе не создаётся, и отказ объясняет причину.
    denied = client.post(
        "/api/campaigns",
        json={"name": "проба", "database_name": "база"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert denied.status_code == 402
    assert "пробном тарифе отправка сообщений выключена" in denied.json()["detail"]


def test_public_plans(client):
    r = client.get("/api/plans")
    assert r.status_code == 200
    body = r.json()
    assert body["currencies"] == ["RUB", "KZT"]
    assert body["admin_contact"] == "admin_user"
    slugs = [p["slug"] for p in body["plans"]]
    assert slugs == ["zephyr"]
    assert body["plans"][0]["price_rub"] == 5000
    assert body["plans"][0]["price_kzt"] == 25000
    # у каждого тарифа есть человекочитаемое имя и список фич
    for p in body["plans"]:
        assert p["name"]
        assert isinstance(p["features"], list)


def test_legacy_parse_stub_is_removed(client):
    """Старой UI-заглушки нет: рабочий поток находится в /api/search."""
    assert client.post("/api/parse").status_code == 404


# ------------------------- защита эндпоинтов --------------------------------


def test_me_requires_token(client):
    assert client.get("/api/me").status_code == 401


def test_me_rejects_garbage_token(client):
    r = client.get("/api/me", headers={"Authorization": "Bearer nonsense"})
    assert r.status_code == 401


def test_subscription_requires_token(client):
    assert client.get("/api/subscription").status_code == 401


def test_offers_require_token(client):
    assert client.get("/api/offers").status_code == 401


# ----------------------- работа под валидным токеном ------------------------


def test_me_returns_profile(client, auth_headers):
    r = client.get("/api/me", headers=auth_headers)
    assert r.status_code == 200
    body = r.json()
    assert body["username"] == "tester"
    assert body["name"] == "Test"
    assert body["is_admin"] is False


def test_admin_flag_reflects_username(client, db):
    """is_admin=True только для ADMIN_USERNAME (задан в conftest как admin_user)."""
    from app import models
    from app.security import create_jwt

    admin = models.User(telegram_id=2002, username="admin_user", first_name="Boss")
    db.add(admin)
    db.commit()
    db.refresh(admin)
    token, _ = create_jwt(admin.id)

    r = client.get("/api/me", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    assert r.json()["is_admin"] is True


def test_admin_flag_reflects_telegram_id(client, db, monkeypatch):
    """is_admin в /api/me обязан учитывать ADMIN_TELEGRAM_IDS, а не только username.

    Регрессия: /api/me и логин звали settings.is_admin(username) без
    telegram_id — админ по ID видел сайт без раздела «Админка», хотя сами
    админские эндпоинты его пускали (dependencies.py проверял правильно).
    """
    from app import models
    from app.config import settings
    from app.security import create_jwt

    monkeypatch.setattr(settings, "admin_telegram_ids", "3003")
    admin = models.User(telegram_id=3003, username="id_admin", first_name="Ida")
    db.add(admin)
    db.commit()
    db.refresh(admin)
    token, _ = create_jwt(admin.id)

    r = client.get("/api/me", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    assert r.json()["is_admin"] is True


def test_new_user_has_no_plan(client, auth_headers):
    """Новый пользователь — без тарифа: бесплатного тарифа в продукте нет."""
    r = client.get("/api/subscription", headers=auth_headers)
    assert r.status_code == 200
    body = r.json()
    assert body["plan"] == "none"
    assert body["plan_name"] == "Без тарифа"
    assert body["blocked"] is False
    assert body["ai"] is False
    assert body["admin_contact"] == "admin_user"
    # использование инициализировано нулями
    assert body["usage"]["leads_used"] == 0


def test_offer_create_and_list(client, paid_user, auth_headers):
    created = client.post(
        "/api/offers",
        headers=auth_headers,
        json={"name": "Оффер 1", "text": "Привет"},
    )
    assert created.status_code in (200, 201)
    oid = created.json()["id"]

    listing = client.get("/api/offers", headers=auth_headers)
    assert listing.status_code == 200
    offers = listing.json()["offers"]
    assert any(o["id"] == oid and o["name"] == "Оффер 1" for o in offers)


def test_offer_rejects_svg_data_uri(client, paid_user, auth_headers):
    response = client.post(
        "/api/offers",
        headers=auth_headers,
        json={
            "name": "SVG",
            "text": "Текст",
            "image": "data:image/svg+xml;base64,PHN2Zz48L3N2Zz4=",
        },
    )
    assert response.status_code == 400


# ------------------------------- logout -------------------------------------


def test_logout_revokes_token(client, auth_headers):
    # до выхода токен работает
    assert client.get("/api/me", headers=auth_headers).status_code == 200
    # выходим
    out = client.post("/api/auth/logout", headers=auth_headers)
    assert out.status_code == 200
    assert out.json()["ok"] is True
    # тот же токен больше не пускает (jti в чёрном списке)
    assert client.get("/api/me", headers=auth_headers).status_code == 401


# ------------------------- блокировка пользователя --------------------------


def test_blocked_user_gets_403(client, db, user, auth_headers):
    from app.plans import get_subscription

    sub = get_subscription(db, user.id)
    sub.blocked = True
    db.commit()

    r = client.get("/api/me", headers=auth_headers)
    assert r.status_code == 403


# --------------------------- ИИ-оффер: настройки -----------------------------


def test_ai_offer_settings_save_and_read(client, paid_user, auth_headers):
    """Скелет ИИ-оффера настраивается один раз и хранится у пользователя.

    Раньше «шаблон» вводился прямо в форме рассылки, поэтому его приходилось
    писать заново на каждый запуск.
    """
    empty = client.get("/api/offers/ai-settings", headers=auth_headers)
    assert empty.status_code == 200
    assert empty.json()["template"] == ""

    saved = client.put(
        "/api/offers/ai-settings",
        headers=auth_headers,
        json={"template": "Привет, {first_name}! Видел {channel}…", "materials": 7},
    )
    assert saved.status_code == 200
    body = saved.json()
    assert body["template"].startswith("Привет")
    # 7 — не из набора вариантов, поэтому приводится к ближайшему допустимому.
    assert body["materials"] in (5, 10)
    assert body["allowed"] is True
    assert body["plan_names"] == "Talaria"

    again = client.get("/api/offers/ai-settings", headers=auth_headers)
    assert again.json()["template"] == body["template"]


def test_ai_offer_settings_reject_too_long(client, paid_user, auth_headers):
    r = client.put(
        "/api/offers/ai-settings",
        headers=auth_headers,
        json={"template": "х" * 4001, "materials": 5},
    )
    assert r.status_code == 400


@pytest.mark.parametrize(
    ("currency", "period_months", "promo_code", "expected_total"),
    [
        ("RUB", 1, "", 5000),
        ("KZT", 1, "", 25000),
        ("RUB", 3, "", 12750),
        ("KZT", 3, "", 63750),
        ("RUB", 1, "ZEPHYR30", 3500),
        ("KZT", 1, "ZEPHYR30", 17500),
        ("RUB", 3, "ZEPHYR30", 11250),
        ("KZT", 3, "ZEPHYR30", 56250),
    ],
)
def test_plan_quote_returns_all_server_authoritative_price_combinations(
    client, currency, period_months, promo_code, expected_total
):
    response = client.post(
        "/api/plans/quote",
        json={"currency": currency, "period_months": period_months, "promo_code": promo_code},
    )

    assert response.status_code == 200
    assert response.json()["currency"] == currency
    assert response.json()["total_amount"] == expected_total


def test_launch_regular_requires_at_least_one_offer(client, paid_user, auth_headers, db):
    """Офферы в рассылке не выбираются поштучно — но хотя бы один нужен."""
    from app import models

    base = models.LeadDatabase(owner_id=paid_user.id, name="База")
    db.add(base)
    db.commit()
    db.refresh(base)

    r = client.post(
        "/api/campaigns/launch",
        headers=auth_headers,
        json={"database_id": base.id, "offer_mode": "regular"},
    )
    assert r.status_code == 400
    assert "оффер" in r.json()["detail"].lower()


def test_launch_rotates_offers_and_accounts(client, paid_user, auth_headers, db, monkeypatch):
    """Обычный режим: офферы и аккаунты чередуются по лидам.

    Это и есть обещание интерфейса («если простой — просто чередуется»), поэтому
    проверяем не «что-то отправилось», а именно порядок текстов и отправителей.
    """
    from app import models, sending

    base = models.LeadDatabase(owner_id=paid_user.id, name="База")
    db.add(base)
    db.commit()
    db.refresh(base)
    for i in range(3):
        db.add(
            models.Lead(
                database_id=base.id,
                channel_id=f"UC{i:020d}",
                title=f"Канал {i}",
                tg_username=f"lead{i}",
                score=50,
            )
        )
    for i in range(2):
        db.add(models.Offer(owner_id=paid_user.id, name=f"Оффер {i}", text=f"Текст {i}"))
        db.add(
            models.WarmupAccount(
                owner_id=paid_user.id, label=f"Слот {i}", tg_id=f"7{i}", tg_username=f"acc{i}"
            )
        )
    db.commit()

    calls: list[tuple[int, str, str]] = []

    def fake_send(owner_id, account_id, username, text, **kwargs) -> dict:  # noqa: ANN001
        calls.append((account_id, username, text))
        return True, ""

    monkeypatch.setattr(sending, "send_via_client", fake_send)

    r = client.post(
        "/api/campaigns/launch",
        headers=auth_headers,
        json={"database_id": base.id, "offer_mode": "regular", "limit": 10, "wait": True},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["sent"] == 3
    assert len(calls) == 3
    # Тексты идут по кругу: Оффер 0, Оффер 1, снова Оффер 0.
    assert [c[2] for c in calls] == ["Текст 0", "Текст 1", "Текст 0"]
    # Аккаунты тоже по кругу — иначе Telegram считает это спамом с одного номера.
    assert calls[0][0] != calls[1][0] and calls[0][0] == calls[2][0]
    # История отправок записана — по ней работает дедупликация.
    assert db.query(models.CampaignSend).count() == 3


def test_subscription_survives_naive_paid_until(client, paid_user, auth_headers):
    """/api/subscription не падает 500 на дате без часового пояса.

    SQLite отдаёт paid_until без tzinfo, и сравнение с aware-датой роняло весь
    эндпоинт — тариф в интерфейсе не открывался вообще.
    """
    r = client.get("/api/subscription", headers=auth_headers)
    assert r.status_code == 200
    body = r.json()
    assert body["plan"] == "zephyr"
    assert body["expired"] is False
    assert body["ai_offer"] is True


def test_offer_funnel_read_interest_and_heatmap(
    client, paid_user, auth_headers, db, monkeypatch
):
    from datetime import UTC, datetime

    from app import models

    base = models.LeadDatabase(owner_id=paid_user.id, name="Воронка")
    lead = models.Lead(database=base, channel_id="UC-funnel", title="Лид", tg_id="777")
    offer = models.Offer(owner_id=paid_user.id, name="Оффер", text="Текст")
    campaign = models.Campaign(owner_id=paid_user.id, name="C", database_name="Воронка")
    db.add_all([base, lead, offer, campaign])
    db.flush()
    sent_at = datetime(2026, 8, 10, 9, tzinfo=UTC)
    send = models.CampaignSend(
        owner_id=paid_user.id,
        campaign_id=campaign.id,
        lead_id=lead.id,
        offer_id=offer.id,
        offer_kind="regular",
        account_id=9,
        status="sent",
        sent_at=sent_at,
        delivered_at=sent_at,
        replied_at=sent_at,
        telegram_message_id=12,
    )
    db.add(send)
    db.commit()

    monkeypatch.setattr("app.routers.campaigns.settings.tg_client_secret", "read-secret")
    marked = client.post(
        "/api/campaigns/read",
        headers={"X-Send-Secret": "read-secret"},
        json={
            "owner_id": paid_user.id,
            "account_id": 9,
            "peer_tg_id": "777",
            "max_message_id": 12,
        },
    )
    assert marked.status_code == 200
    assert marked.json()["marked"] == 1

    interested = client.patch(
        f"/api/leads/{lead.id}?status=interested", headers=auth_headers
    )
    assert interested.status_code == 200
    stats = client.get("/api/campaigns/offer-stats", headers=auth_headers).json()
    assert stats["total_sent"] == 1
    assert stats["total_delivered"] == 1
    assert stats["total_read"] == 1
    assert stats["total_replied"] == 1
    assert stats["total_interested"] == 1
    assert any(cell["replies"] == 1 for cell in stats["reply_heatmap"])


def test_launch_background_returns_immediately(client, paid_user, auth_headers, db, monkeypatch):
    """Запуск без wait отвечает сразу, отправка доходит в фоновом потоке.

    Это и есть защита от падения по таймауту: с паузами между сообщениями
    даже небольшая партия не помещается в один HTTP-запрос.
    """
    import time as time_mod

    from sqlalchemy.orm import sessionmaker

    from app import database, models, sending

    # Фоновый поток открывает свою сессию — в тестах она должна смотреть в ту же
    # SQLite в памяти, что и остальное. Импортировать conftest нельзя: pytest
    # уже загрузил его, а повторный импорт создал бы второй engine без таблиц.
    monkeypatch.setattr(database, "SessionLocal", sessionmaker(bind=db.get_bind(), autoflush=False))

    base = models.LeadDatabase(owner_id=paid_user.id, name="Фон")
    db.add(base)
    db.commit()
    db.refresh(base)
    db.add(
        models.Lead(
            database_id=base.id, channel_id="UCbg", title="Лид", tg_username="bglead", score=10
        )
    )
    db.add(models.Offer(owner_id=paid_user.id, name="Оффер", text="Текст"))
    db.add(
        models.WarmupAccount(owner_id=paid_user.id, label="Слот", tg_id="9", tg_username="bgacc")
    )
    db.commit()

    monkeypatch.setattr(sending, "send_via_client", lambda *a, **k: (True, ""))

    r = client.post(
        "/api/campaigns/launch",
        headers=auth_headers,
        json={"database_id": base.id, "offer_mode": "regular", "limit": 10},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["background"] is True
    assert body["sent"] == 0  # ещё ничего не ушло — ответ мгновенный

    # Фоновому потоку даём до 5 секунд — обычно хватает миллисекунд.
    campaign_id = body["campaign_id"]
    for _ in range(100):
        db.expire_all()
        campaign = db.query(models.Campaign).filter(models.Campaign.id == campaign_id).one()
        if campaign.status == "completed":
            break
        time_mod.sleep(0.05)
    assert campaign.status == "completed"
    assert campaign.sent_count == 1


def test_recover_interrupted_marks_stuck_campaigns(db, paid_user):
    """После рестарта «идущие» рассылки помечаются прерванными, а не висят вечно."""
    from app import models
    from app.routers.campaigns import recover_interrupted

    campaign = models.Campaign(
        owner_id=paid_user.id, name="Зависшая", database_name="База", status="running"
    )
    db.add(campaign)
    db.commit()

    assert recover_interrupted(db) == 1
    db.refresh(campaign)
    assert campaign.status == "interrupted"
    logs = db.query(models.CampaignLog).filter(models.CampaignLog.campaign_id == campaign.id).all()
    assert any("прервана" in log.message for log in logs)


def test_contacts_parsing_rules():
    """Правила разбора контактов: боты в запас, служебные пути мимо, deeplink работает."""
    from app.contacts import extract_email, extract_telegram

    # Бот откладывается, живой ник побеждает — даже если бот встретился раньше.
    assert extract_telegram("бот: t.me/shop_bot, автор: t.me/real_author") == "real_author"
    # Ничего кроме бота нет — возвращаем бота: это всё же контакт канала.
    assert extract_telegram("пишите t.me/support_bot") == "support_bot"
    # Служебные пути и приглашения — не контакты.
    assert extract_telegram("t.me/boost/somechannel") != "boost"
    assert extract_telegram("t.me/+AbCdEfGh") == ""
    # Диплинк из описания.
    assert extract_telegram("связь: tg://resolve?domain=author_tg") == "author_tg"
    # Имена файлов ретины — не e-mail.
    assert extract_email("иконка logo@2x.png, почта author@mail.com") == "author@mail.com"


def test_launch_preview_returns_samples_without_sending(
    client, paid_user, auth_headers, db, monkeypatch
):
    """Предпросмотр отдаёт готовые тексты тем же кодом, что запуск, но не шлёт.

    Проверяем три обещания: ротация офферов та же, что при отправке; ничего не
    записано в кампании/отправки; подключённые аккаунты не требуются.
    """
    from app import models, sending

    base = models.LeadDatabase(owner_id=paid_user.id, name="База")
    db.add(base)
    db.commit()
    db.refresh(base)
    for i in range(3):
        db.add(
            models.Lead(
                database_id=base.id,
                channel_id=f"UCpv{i:018d}",
                title=f"Канал {i}",
                tg_username=f"lead{i}",
                # score по убыванию, чтобы порядок выборки был предсказуем
                score=90 - i,
            )
        )
    for i in range(2):
        db.add(
            models.Offer(
                owner_id=paid_user.id, name=f"Оффер {i}", text=f"Привет, {{channel}}! Текст {i}"
            )
        )
    db.commit()

    def boom(*args, **kwargs) -> None:  # noqa: ANN002, ANN003
        raise AssertionError("предпросмотр не должен ничего отправлять")

    monkeypatch.setattr(sending, "send_via_client", boom)

    r = client.post(
        "/api/campaigns/launch-preview",
        headers=auth_headers,
        json={"database_id": base.id, "offer_mode": "regular", "limit": 10},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["recipients"] == 3
    samples = body["samples"]
    assert len(samples) == 3
    # Плейсхолдеры подставлены, офферы чередуются как при реальной отправке.
    assert samples[0]["text"] == "Привет, Канал 0! Текст 0"
    assert samples[1]["text"] == "Привет, Канал 1! Текст 1"
    assert samples[2]["text"] == "Привет, Канал 2! Текст 0"
    # Ничего не создано и не отправлено.
    assert db.query(models.Campaign).count() == 0
    assert db.query(models.CampaignSend).count() == 0


def test_stats_chart_window_and_prev_period(client, paid_user, auth_headers, db):
    """/api/stats?days=30 отдаёт 30 точек и сумму за прошлое окно для сравнения."""
    from datetime import UTC, datetime, timedelta

    from app import models

    base = models.LeadDatabase(owner_id=paid_user.id, name="База")
    db.add(base)
    db.commit()
    db.refresh(base)
    now = datetime.now(UTC)
    # 2 лида в текущем окне (сегодня), 1 — в прошлом окне (40 дней назад).
    for i, days_ago in enumerate([0, 0, 40]):
        db.add(
            models.Lead(
                database_id=base.id,
                channel_id=f"UCst{i:018d}",
                title=f"Канал {i}",
                created_at=now - timedelta(days=days_ago),
            )
        )
    db.commit()

    r = client.get("/api/stats?days=30", headers=auth_headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["chart_days"] == 30
    assert len(body["leads_per_day"]) == 30
    assert body["chart_leads"] == 2
    assert body["prev_chart_leads"] == 1

    # Произвольное окно не принимаем — откатываемся к неделе.
    r = client.get("/api/stats?days=9999", headers=auth_headers)
    assert r.json()["chart_days"] == 14


def test_lead_reply_notifies_owner_via_bot(client, paid_user, db, monkeypatch):
    """Ответ лида уходит владельцу в личку бота, а не только админам."""
    from app import alerts
    from app.config import settings as app_settings
    from app.models import Campaign, CampaignSend, Lead, LeadDatabase, WarmupAccount

    base = LeadDatabase(owner_id=paid_user.id, name="База")
    db.add(base)
    db.commit()
    db.refresh(base)
    lead = Lead(
        database_id=base.id,
        channel_id="UCrp0000000000000000",
        title="Канал",
        tg_id="7001",
        tg_username="replier",
    )
    account = WarmupAccount(
        owner_id=paid_user.id,
        label="Отправитель",
        tg_id="8001",
        tg_username="sender",
    )
    campaign = Campaign(owner_id=paid_user.id, name="Кампания", database_name="База")
    db.add_all([lead, account, campaign])
    db.flush()
    db.add(
        CampaignSend(
            owner_id=paid_user.id,
            campaign_id=campaign.id,
            lead_id=lead.id,
            account_id=account.id,
            status="sent",
        )
    )
    db.commit()

    sent: list[tuple[object, str]] = []
    monkeypatch.setattr(alerts, "notify_user", lambda tid, text, **kw: sent.append((tid, text)))
    monkeypatch.setattr(app_settings, "tg_client_secret", "s3cret")

    r = client.post(
        "/api/campaigns/reply",
        headers={"X-Send-Secret": "s3cret"},
        json={
            "owner_id": paid_user.id,
            "account_id": account.id,
            "tg_id": "7001",
            "username": "replier",
            "sender_name": "Получатель",
            "text": "Привет!",
        },
    )
    assert r.status_code == 200, r.text
    assert len(sent) == 1
    assert sent[0][0] == paid_user.telegram_id
    assert "Привет!" in sent[0][1]
