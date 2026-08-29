"""Юнит-тесты чистой логики: тарифы, JWT, подпись Telegram.

Без БД и HTTP — только функции, у которых на одинаковый вход всегда один выход.
"""

from __future__ import annotations

import hashlib
import hmac
import time
from pathlib import Path
from typing import Never

from app import plans
from app.security import create_jwt, decode_jwt, verify_telegram_payload


def test_youtube_network_failure_raises_typed_error(monkeypatch):
    import urllib.error

    import pytest

    from app import youtube

    def offline(*_args: object, **_kwargs: object) -> Never:
        raise urllib.error.URLError("offline")

    monkeypatch.setattr(youtube, "RETRIES", 1)
    monkeypatch.setattr(youtube.urllib.request, "urlopen", offline)

    with pytest.raises(youtube.YouTubeError, match="offline"):
        youtube._call("search", {"part": "snippet"}, "key")


def test_csv_exports_neutralize_spreadsheet_formulas():
    from app.routers.admin import _csv_safe as admin_safe
    from app.routers.leads import _csv_safe as lead_safe

    for value in ("=1+1", "+cmd", " -2", "@SUM(A1:A2)"):
        assert str(admin_safe(value)).lstrip().startswith("'")
        assert str(lead_safe(value)).lstrip().startswith("'")
    assert lead_safe("Обычный канал") == "Обычный канал"


# ------------------------------- тарифы -------------------------------------


def test_plan_order_matches_plans():
    assert plans.PLAN_ORDER == ["zephyr"]
    for slug in plans.PLAN_ORDER:
        assert slug in plans.PLANS


def test_get_plan_unknown_falls_back_to_none():
    assert plans.get_plan("nope")["slug"] == "none"
    assert plans.get_plan(None)["slug"] == "none"
    assert plans.get_plan("")["slug"] == "none"


def test_get_plan_is_case_insensitive():
    assert plans.get_plan("ZEPHYR")["slug"] == "zephyr"
    # Старые подписки не теряют доступ после перехода на единый тариф.
    assert plans.get_plan("  Ultra ")["slug"] == "zephyr"


def test_public_plan_hides_internal_fields():
    pub = plans.public_plan("pro")
    # во внешнем виде нет max_campaigns (внутреннее поле лимита)
    assert "max_campaigns" not in pub
    assert set(pub) == {
        "slug",
        "name",
        "price_kzt",
        "price_rub",
        "price_kzt_first",
        "price_rub_first",
        "tagline",
        "monthly_leads",
        "monthly_messages",
        "monthly_ai_offers",
        "max_per_launch",
        "max_databases",
        "max_offers",
        "max_warmup",
        "ai",
        "ai_offer",
        "sending",
        "neuro_comment",
        "features",
    }


def test_all_public_plans_in_order():
    got = [p["slug"] for p in plans.all_public_plans()]
    assert got == plans.PLAN_ORDER


def test_all_paid_plans_have_ai():
    for slug in ("pro", "max", "ultra"):
        assert plans.PLANS[slug]["ai"] is True


def test_ai_offer_is_in_single_plan():
    assert plans.PLANS["zephyr"]["ai_offer"] is True
    assert plans.ai_offer_plan_names() == "Talaria"


def test_ultra_is_unlimited():
    ultra = plans.PLANS["ultra"]
    for key in ("monthly_leads", "max_databases", "max_offers", "max_campaigns", "max_warmup"):
        assert ultra[key] == -1


def test_prices_increase_with_tier():
    assert plans.PLANS["zephyr"]["price_rub"] == 5000
    assert plans.PLANS["zephyr"]["price_kzt"] == 25000


def test_promo_and_quarter_quotes():
    assert plans.FIRST_PURCHASE_DISCOUNT_PCT == 0
    assert plans.quote_plan(1, "ZEPHYR30")["total_rub"] == 3500
    quote = plans.quote_plan(3, "zephyr30")
    assert quote["base_total_rub"] == 15000
    assert quote["bundle_discount_rub"] == 2250
    assert quote["promo_discount_rub"] == 1500
    assert quote["total_rub"] == 11250
    tenge = plans.quote_plan(1, "ZEPHYR30", "KZT")
    assert tenge["total_amount"] == 17500
    assert tenge["currency"] == "KZT"


def test_first_purchase_price_in_public_plan():
    pub = plans.public_plan("zephyr")
    assert pub["price_rub"] == 5000
    assert pub["price_rub_first"] == pub["price_rub"]


# -------------------------------- JWT ---------------------------------------


def test_jwt_roundtrip():
    token, jti = create_jwt(42)
    decoded = decode_jwt(token)
    assert decoded is not None
    user_id, decoded_jti = decoded
    assert user_id == 42
    assert decoded_jti == jti


def test_jwt_each_token_has_unique_jti():
    _, jti1 = create_jwt(1)
    _, jti2 = create_jwt(1)
    assert jti1 != jti2


def test_decode_garbage_returns_none():
    assert decode_jwt("not-a-token") is None
    assert decode_jwt("") is None


def test_decode_wrong_signature_returns_none():
    import jwt as pyjwt

    forged = pyjwt.encode({"sub": "1", "jti": "x"}, "wrong-secret", algorithm="HS256")
    assert decode_jwt(forged) is None


def test_decode_token_without_jti_rejected():
    """Токен старого формата (без jti) считаем недействительным."""
    import jwt as pyjwt

    from app.config import settings

    legacy = pyjwt.encode({"sub": "1"}, settings.jwt_secret, algorithm="HS256")
    assert decode_jwt(legacy) is None


# -------------------------- подпись Telegram --------------------------------


def test_get_settings_recovers_from_concurrent_insert(monkeypatch):
    from sqlalchemy.exc import IntegrityError

    from app import services
    from app.models import AppSettings

    existing = AppSettings(id=17, owner_id=1, monthly_limit=500)

    class QueryStub:
        def __init__(self, value: AppSettings | None) -> None:
            self.value = value

        def filter(self, *_args: object) -> QueryStub:
            return self

        def one_or_none(self) -> AppSettings | None:
            return self.value

    class SessionStub:
        def __init__(self) -> None:
            self.query_count = 0
            self.rollback_count = 0

        def query(self, _model: object) -> QueryStub:
            self.query_count += 1
            return QueryStub(None if self.query_count == 1 else existing)

        def add(self, _row: object) -> None:
            return None

        def commit(self) -> None:
            raise IntegrityError("INSERT", {"owner_id": 1}, Exception("duplicate"))

        def rollback(self) -> None:
            self.rollback_count += 1

        def refresh(self, _row: object) -> None:
            raise AssertionError("проигравшую INSERT-гонку нельзя refresh")

    monkeypatch.setattr("app.plans.effective_plan_slug", lambda *_args: "pro")
    monkeypatch.setattr("app.plans.get_plan", lambda _slug: {"monthly_messages": 500})
    session = SessionStub()

    assert services.get_settings(session, 1) is existing
    assert session.rollback_count == 1


def test_telegram_widget_uses_csp_safe_redirect():
    project_root = Path(__file__).resolve().parents[2]
    auth_js = (project_root / "frontend" / "js" / "auth.js").read_text(encoding="utf-8")
    nginx = (project_root / "frontend" / "nginx.conf").read_text(encoding="utf-8")

    assert "s.setAttribute('data-auth-url'" in auth_js
    assert "s.setAttribute('data-onauth'" not in auth_js
    assert "redirected.error" in auth_js
    assert "Проверьте BOT_TOKEN и время сервера" not in auth_js
    assert "Вход не выполнен: ${redirected.error}" in auth_js
    assert "renderText(el, text)" in auth_js
    assert ".innerHTML =" not in auth_js
    assert "'unsafe-eval'" not in nginx


def test_tariff_buttons_name_the_plan_and_active_tariff():
    project_root = Path(__file__).resolve().parents[2]
    plans_js = (project_root / "frontend" / "js" / "plans.js").read_text(encoding="utf-8")
    tariffs_js = (project_root / "frontend" / "js" / "tariffs.js").read_text(encoding="utf-8")
    page = (project_root / "frontend" / "index.html").read_text(encoding="utf-8")

    assert "from './tariffs.js'" in plans_js
    assert "Оплатить тариф" in tariffs_js
    assert "Продлить тариф" in tariffs_js
    assert "У меня есть промокод" in tariffs_js
    assert "ZEPHYR30" not in tariffs_js
    assert "quote.total_amount" in tariffs_js  # итог всё равно приходит только из API
    assert "/api/plans/quote" in tariffs_js
    assert "planConfirmContinue" in tariffs_js
    assert "buildPaymentUrl(adminContact, quote)" in tariffs_js
    assert ".onclick" not in tariffs_js
    assert "window.open(" not in tariffs_js
    assert '<dialog class="plan-confirm-dialog" id="modalPlanTelegram"' in page
    assert '<form method="dialog" class="modal plan-confirm-modal">' in page
    assert "Ничего не спишется автоматически" in page
    assert "Перейти в Telegram" in page


def test_frontend_healthcheck_uses_ipv4_loopback():
    project_root = Path(__file__).resolve().parents[2]
    compose = (project_root / "docker-compose.yml").read_text(encoding="utf-8")

    assert "wget -q --spider http://127.0.0.1:80/" in compose
    assert "wget -q --spider http://localhost:80/" not in compose


def test_backup_services_create_and_restore_verified_archives():
    project_root = Path(__file__).resolve().parents[2]
    compose = (project_root / "docker-compose.yml").read_text(encoding="utf-8")
    backup = (project_root / "scripts" / "backup.sh").read_text(encoding="utf-8")
    verify = (project_root / "scripts" / "verify-backup.sh").read_text(encoding="utf-8")

    assert "./scripts/backup.sh:/usr/local/bin/outly-backup:ro" in compose
    assert "web-tg-sessions:/state/telegram-sessions:ro" in compose
    assert "backup-verify:" in compose
    assert "backup-watchdog:" in compose
    assert "condition: service_healthy" in compose
    assert ".last-ok.epoch" in compose
    assert ".last-restore-ok.epoch" in compose
    assert "name: ${OUTLY_PGDATA_VOLUME:-outly-pgdata}" in compose
    assert "pg_dump --no-owner --no-privileges >" in backup
    assert "pg_dump --no-owner --no-privileges |" not in backup
    assert "sha256sum" in backup
    assert "outly-telegram-sessions-" in backup
    assert "ON_ERROR_STOP=on" in verify
    assert "outly_restore_verify" in verify
    assert "dropdb --if-exists --force" in verify
    watchdog = (project_root / "backend" / "app" / "backup_watchdog.py").read_text(encoding="utf-8")
    assert ".last-ok.epoch" in watchdog
    assert ".last-restore-ok.epoch" in watchdog
    assert "settings.admin_ids" in watchdog


def test_guest_brand_defaults_and_active_session_identity():
    project_root = Path(__file__).resolve().parents[2]
    boot = (project_root / "frontend" / "js" / "theme-boot.js").read_text(encoding="utf-8")
    theme = (project_root / "frontend" / "js" / "theme.js").read_text(encoding="utf-8")
    auth = (project_root / "frontend" / "js" / "auth.js").read_text(encoding="utf-8")
    page = (project_root / "frontend" / "index.html").read_text(encoding="utf-8")

    assert "root.setAttribute('data-font', 'brand')" in boot
    assert "localStorage.getItem('mf_theme')" in boot
    assert "style.setProperty(" not in boot
    assert "import { get, remove, set } from './shared/storage.js';" in theme
    assert "remove(FONT_KEY)" in theme
    assert "remove(ACCENT_COLOR_KEY)" in theme
    assert 'id="sessionUserName"' in page
    assert 'id="sessionUserUsername"' in page
    assert 'id="sessionUserAvatar"' in page
    assert "sessionName.textContent = name" in auth
    assert "greeting.textContent = name ? `Добрый день, ${name}`" in auth
    assert auth.index("loadPublicPlans();") < auth.index("await consumeTelegramRedirect()")


def test_brand_name_logo_and_ai_context_are_consistent():
    """Публичный бренд — Talaria во всех трёх местах сразу.

    После ребрендинга Zephyr → Talaria тест сверяет лендинг, кабинет и контекст
    ИИ-помощника: рассинхрон между ними уже случался, поэтому проверяем вместе.
    Внутренний слаг тарифа `zephyr` в БД при этом остаётся — он про совместимость,
    а не про бренд.
    """
    project_root = Path(__file__).resolve().parents[2]
    page = (project_root / "frontend" / "index.html").read_text(encoding="utf-8")
    boot = (project_root / "frontend" / "js" / "theme-boot.js").read_text(encoding="utf-8")
    prompt = (project_root / "backend" / "app" / "ai_prompt.py").read_text(encoding="utf-8")

    assert "<title>Talaria —" in page
    assert '<span class="landing-brand-name">Talaria</span>' in page
    assert '<div class="brand-name">Talaria</div>' in page
    # Раньше здесь сверялся путь старого логотипа-«галочки». Логотип переделан
    # на три пера с отдельной стойкой T, и проверка молча устарела — тест падал,
    # а фронтенд об этом не знал, потому что гоняли только его собственные тесты.
    # Сверяем стойку и перья нового знака.
    assert 'd="M18.6 13.4h5.6' in page  # стойка буквы T
    assert page.count('class="tl-f"') >= 3  # три пера крыла
    # Favicon живёт в одном месте (index.html): boot больше не подменяет его
    # старой градиентной Z после первого paint.
    assert 'id="appFavicon"' in page
    assert "favicon.href" not in boot
    assert "Outly" not in page
    assert "Zephyr" not in page
    assert "помощник Talaria" in prompt
    assert "Outly" not in prompt
    assert "Zephyr" not in prompt


def test_telegram_account_ui_uses_one_button_and_auto_warmup():
    project_root = Path(__file__).resolve().parents[2]
    page = (project_root / "frontend" / "index.html").read_text(encoding="utf-8")
    settings_js = (project_root / "frontend" / "js" / "settings.js").read_text(encoding="utf-8")

    assert "Добавить аккаунт" in page
    assert "newAccountLabel" not in page
    assert "newAccountPlanDays" not in page
    assert "newAccountDailyLimit" not in page
    assert "newAccountDelayMin" not in page
    assert "data-wm-field" not in settings_js
    assert "data-wm-day" not in settings_js
    assert "api('POST', '/api/warmup/quick', {})" in settings_js
    assert "`/api/warmup/${id}/pending`" in settings_js
    assert "cancelTgConnect" in settings_js
    assert "<b>Прогрев</b>" in settings_js
    assert "Настроен автоматически" in settings_js
    assert "Аккаунт входа" in settings_js
    assert "Подключить для работы" in settings_js
    assert "План по дням, ручные отметки" not in page
    assert "Ручные лимиты, дни и паузы заполнять не нужно" in page


def test_telegram_connect_explains_delivery_and_can_resend_code():
    project_root = Path(__file__).resolve().parents[2]
    server = (project_root / "tg-mini-client" / "server.js").read_text(encoding="utf-8")
    page = (project_root / "tg-mini-client" / "public" / "index.html").read_text(encoding="utf-8")
    connect = (project_root / "tg-mini-client" / "public" / "connect.js").read_text(encoding="utf-8")
    package = (project_root / "tg-mini-client" / "package.json").read_text(encoding="utf-8")

    assert '"login_start", "login_qr", "code", "resend", "password", "logout"' in server
    assert 'delivery = isCodeViaApp ? "app" : "sms"' in server
    assert 'restart.errorMessage = "RESTART_AUTH"' in server
    assert 'case "resend":' in server
    assert "без SMS" in page
    assert 'id="resend"' in page
    assert "data.delivery === 'sms'" in connect
    assert "send({ type: 'resend' })" in connect
    assert '"qrcode": "1.5.4"' in package
    assert 'case "login_qr":' in server
    assert 'app.post("/session/status"' in server
    assert 'client.isUserAuthorized()' in server
    assert 'app.post("/session/drop"' in server
    assert 'tg://login?token=${encoded}' in server
    assert 'id="qrLogin"' in page
    assert "send({ type: 'login_qr' })" in connect


def test_spambot_clear_reply_and_real_reply_watch_are_wired_end_to_end():
    project_root = Path(__file__).resolve().parents[2]
    spambot = (project_root / "tg-mini-client" / "spambot.js").read_text(encoding="utf-8")
    gateway = (project_root / "tg-mini-client" / "server.js").read_text(encoding="utf-8")
    gateway_client = (project_root / "backend" / "app" / "telegram_gateway.py").read_text(encoding="utf-8")
    campaigns = (project_root / "frontend" / "js" / "campaigns.js").read_text(encoding="utf-8")
    live = (project_root / "frontend" / "js" / "live.js").read_text(encoding="utf-8")
    page = (project_root / "frontend" / "index.html").read_text(encoding="utf-8")
    campaign_consumers = [
        (project_root / "frontend" / "js" / name).read_text(encoding="utf-8")
        for name in ("main.js", "auth.js", "databases.js", "offers.js", "live.js")
    ]

    # Положительный ответ содержит слово «ограничений», но должен быть clear.
    assert "свобод[а-я]* от (?:каких[- ]?либо )?ограничен[а-я]*" in spambot
    assert 'require("./spambot")' in gateway
    assert "bindHandlers(entry)" in gateway
    assert "entry.holdUntil" in gateway
    assert "watch_seconds" in gateway
    assert "watch_seconds" in gateway_client
    assert "replyWatches: new Map()" in gateway
    assert "watchReplyPeer(entry, peer, username, watchSeconds)" in gateway
    # Правило «что считать ответом лида» переехало в reply-filter.js: в
    # server.js его нельзя было проверить тестами, а ложный ответ гасит окно
    # ожидания и теряет настоящий. Здесь следим, что модуль подключён и что
    # отсев неличных чатов никуда не делся.
    reply_filter = (project_root / "tg-mini-client" / "reply-filter.js").read_text(
        encoding="utf-8"
    )
    assert 'require("./reply-filter")' in gateway
    assert "classifyIncoming(msg, sender, Boolean(watch))" in gateway
    assert "msg.isPrivate === true" in reply_filter
    assert 'reason: "не личный диалог"' in reply_filter
    dockerfile = (project_root / "tg-mini-client" / "Dockerfile").read_text(encoding="utf-8")
    assert (
        "COPY reply-filter.js" in dockerfile
    ), "модуль не попадёт в образ — контейнер упадёт при старте"

    # Строка сессии — полный доступ к аккаунту без пароля и кода, поэтому на
    # диске она обязана лежать зашифрованной. Следим, что модуль подключён,
    # что запись идёт через него и что он попадает в образ.
    assert 'require("./session-crypto")' in gateway
    assert "sessionCrypto.encrypt(sessionString, SESSION_KEY)" in gateway
    assert "sessionCrypto.decrypt(raw, SESSION_KEY)" in gateway
    assert "encryptExistingSessions()" in gateway, "старые открытые файлы должны перешифроваться"
    assert (
        "COPY session-crypto.js" in dockerfile
    ), "модуль не попадёт в образ — контейнер упадёт при старте"
    assert "const watch = watchedReply(entry, senderId)" in gateway
    assert "sender_name: senderName" in gateway
    assert "entry.replyWatches.delete(senderId)" in gateway
    send_handler = gateway.index('app.post("/send"')
    sent_message = gateway.index("sent = await client.sendMessage", send_handler)
    register_watch = gateway.index(
        "watchReplyPeer(entry, peer, username, watchSeconds)",
        send_handler,
    )
    callback = gateway.index('fetch(`${SITE_API_URL}/api/campaigns/reply`')
    require_watch = gateway.index("const watch = watchedReply(entry, senderId)")
    assert sent_message < register_watch
    assert require_watch < callback

    # Тест не подменяется локальным таймером: успех ставит только reply из WS.
    assert 'id="testReplyWatch"' in page
    assert "startTestReplyWatch" in campaigns
    assert "export function applyTestReplyUpdate" in campaigns
    assert "./campaigns.js?v=zephyr3" in live
    assert all("./campaigns.js?v=zephyr3" in source for source in campaign_consumers)
    assert all("from './campaigns.js';" not in source for source in campaign_consumers)
    main = campaign_consumers[0]
    auth = campaign_consumers[1]
    assert "./databases.js?v=note1" in main and "./databases.js?v=note1" in auth
    assert "./offers.js?v=aioffer1" in main and "./offers.js?v=aioffer1" in auth
    assert "applyTestReplyUpdate(msg)" in live
    assert "message?.text" in campaigns


def test_ai_project_overview_is_scoped_to_current_owner(db, user):
    from app import models
    from app.ai_tools import run_tool

    other = models.User(telegram_id=900002, username="other_workspace", first_name="Other")
    db.add(other)
    db.flush()
    db.add(models.LeadDatabase(owner_id=user.id, name="Своя база"))
    db.add(models.Offer(owner_id=user.id, name="Свой оффер", text="Текст"))
    db.add(models.LeadDatabase(owner_id=other.id, name="Чужая база"))
    db.add(models.Offer(owner_id=other.id, name="Чужой оффер", text="Секрет"))
    db.commit()

    result = run_tool(db, user.id, "project_overview", {})

    assert result["workspace"]["databases"] == 1
    assert result["workspace"]["offers"] == 1
    serialized = str(result)
    assert "Чужая база" not in serialized
    assert "Чужой оффер" not in serialized
    assert "Секрет" not in serialized


def test_ai_interface_control_is_reversible_and_never_saves(db, user):
    from app import models
    from app.ai_tools import run_tool

    before = db.query(models.Offer).filter(models.Offer.owner_id == user.id).count()
    result = run_tool(
        db,
        user.id,
        "control_interface",
        {"action": "draft_offer", "offer_name": "N" * 500, "offer_text": "T" * 9000},
    )

    assert result["saved"] is False
    assert len(result["ui_action"]["offer_name"]) == 200
    assert len(result["ui_action"]["offer_text"]) == 5000
    assert db.query(models.Offer).filter(models.Offer.owner_id == user.id).count() == before
    assert run_tool(db, user.id, "control_interface", {"action": "delete_everything"}).get("error")


def test_ai_chat_payload_has_hard_context_limits():
    import pytest
    from pydantic import ValidationError

    from app.schemas import AiChatIn

    with pytest.raises(ValidationError):
        AiChatIn.model_validate({"messages": [{"role": "system", "content": "x"}]})
    with pytest.raises(ValidationError):
        AiChatIn.model_validate({"messages": [{"role": "user", "content": "x" * 8001}]})
    with pytest.raises(ValidationError):
        AiChatIn.model_validate({
            "messages": [{"role": "user", "content": "x" * 3000} for _ in range(17)],
        })


def _sign(data: dict, token: str) -> str:
    pairs = [f"{k}={v}" for k, v in data.items() if k != "hash" and v is not None]
    pairs.sort()
    secret = hashlib.sha256(token.encode()).digest()
    return hmac.new(secret, "\n".join(pairs).encode(), hashlib.sha256).hexdigest()


def test_valid_telegram_payload_accepted():
    from app.config import settings

    data = {"id": 5, "first_name": "A", "auth_date": int(time.time())}
    data["hash"] = _sign(data, settings.bot_token)
    assert verify_telegram_payload(data) is True


def test_tampered_telegram_payload_rejected():
    from app.config import settings

    data = {"id": 5, "first_name": "A", "auth_date": int(time.time())}
    data["hash"] = _sign(data, settings.bot_token)
    data["id"] = 6  # подменили после подписи
    assert verify_telegram_payload(data) is False


def test_expired_telegram_payload_rejected():
    from app.config import settings

    old = int(time.time()) - settings.telegram_auth_max_age_seconds - 10
    data = {"id": 5, "first_name": "A", "auth_date": old}
    data["hash"] = _sign(data, settings.bot_token)
    assert verify_telegram_payload(data) is False


def test_missing_hash_rejected():
    assert verify_telegram_payload({"id": 5, "auth_date": int(time.time())}) is False
