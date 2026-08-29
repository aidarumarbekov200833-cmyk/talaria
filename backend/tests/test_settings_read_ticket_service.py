"""Characterization tests for Task 3B3 settings read extraction."""

# Test doubles deliberately mirror only the Session surface exercised here.
# ruff: noqa: ANN202, ANN204

from __future__ import annotations

import hashlib
import hmac
import inspect
from datetime import UTC, datetime

import pytest
from sqlalchemy.exc import IntegrityError


def test_settings_repository_has_only_persistence_boundary() -> None:
    from app.repositories import settings as repository

    source = inspect.getsource(repository)
    for forbidden in (".commit(", ".rollback(", ".refresh(", "fastapi", "DomainError"):
        assert forbidden not in source


def test_settings_service_is_transport_neutral() -> None:
    from app.services import settings as service

    source = inspect.getsource(service).lower()
    assert "fastapi" not in source
    assert "starlette" not in source


def test_get_settings_existing_and_new(db, user) -> None:
    from app.models import AppSettings
    from app.services.settings import get_settings

    existing = AppSettings(owner_id=user.id, monthly_limit=123)
    db.add(existing)
    db.commit()
    assert get_settings(db, user.id) is existing

    other_id = user.id + 10_000
    row = get_settings(db, other_id)
    assert row.owner_id == other_id
    assert row.monthly_limit >= 0
    assert row.id is not None


def test_get_settings_recovers_concurrent_winner_and_reraises_without_one(monkeypatch) -> None:
    from app.models import AppSettings
    from app.services.settings import get_settings

    existing = AppSettings(id=17, owner_id=1, monthly_limit=500)

    class Query:
        def __init__(self, value):
            self.value = value

        def filter(self, *_args):
            return self

        def one_or_none(self):
            return self.value

    class Session:
        def __init__(self, winner):
            self.winner = winner
            self.queries = 0
            self.rollbacks = 0

        def query(self, _model):
            self.queries += 1
            return Query(None if self.queries == 1 else self.winner)

        def add(self, _row):
            pass

        def commit(self):
            raise IntegrityError("insert", {}, Exception("duplicate"))

        def rollback(self):
            self.rollbacks += 1

        def refresh(self, _row):
            raise AssertionError("winner path must not refresh")

    monkeypatch.setattr("app.plans.effective_plan_slug", lambda *_args: "none")
    monkeypatch.setattr("app.plans.get_plan", lambda *_args: {"monthly_messages": 0})

    winner_session = Session(existing)
    assert get_settings(winner_session, 1) is existing
    assert winner_session.rollbacks == 1

    missing_session = Session(None)
    with pytest.raises(IntegrityError):
        get_settings(missing_session, 1)
    assert missing_session.rollbacks == 1


def test_roll_month_changes_only_when_period_differs(monkeypatch) -> None:
    from app.models import AppSettings
    from app.services.settings import roll_month

    class Session:
        commits = 0
        refreshes = 0

        def commit(self):
            self.commits += 1

        def refresh(self, _row):
            self.refreshes += 1

    monkeypatch.setattr("app.services.settings.current_period", lambda: "2026-08")
    changed = AppSettings(monthly_period="2026-07", monthly_used=22)
    session = Session()
    assert roll_month(session, changed) is changed
    assert (changed.monthly_period, changed.monthly_used, session.commits, session.refreshes) == (
        "2026-08", 0, 1, 1
    )

    unchanged = AppSettings(monthly_period="2026-08", monthly_used=22)
    session = Session()
    assert roll_month(session, unchanged) is unchanged
    assert (unchanged.monthly_used, session.commits, session.refreshes) == (22, 0, 0)


def test_read_settings_exact_response(client, user, auth_headers, db) -> None:
    from app.models import AppSettings

    db.add(
        AppSettings(
            owner_id=user.id,
            monthly_period=datetime.now(UTC).strftime("%Y-%m"),
            monthly_used=11,
            low_limit_threshold=23,
        )
    )
    db.commit()
    response = client.get("/api/settings", headers=auth_headers)
    assert response.status_code == 200, response.text
    assert response.json() == {
        "project_name": "Talaria", "monthly_limit": -1, "monthly_used": 11,
        "monthly_left": -1, "low_limit_threshold": 23, "low_limit_reached": False,
        "ai_available": False, "ai_ready": False,
        # Выключатель уведомлений об ответе лида. По умолчанию включён.
        "reply_notify": True,
        # Главный аккаунт для уведомлений: не выбран, доставку не проверяли.
        "notify_target_tg_id": None,
        "notify_target_ok": None,
        # Окно отправки: включено по умолчанию, рабочий день 10–20 по местному
        # времени получателя.
        "send_window_enabled": True,
        "send_window_start": 10,
        "send_window_end": 20,
    }


def test_telegram_account_card_is_owner_scoped(client, user, auth_headers, db) -> None:
    from app import models

    other = models.User(telegram_id=5555, username="other", first_name="Other")
    db.add(other)
    db.flush()
    db.add_all([
        models.WarmupAccount(owner_id=user.id, label="mine", status="active"),
        models.WarmupAccount(owner_id=other.id, label="foreign", status="active"),
        models.WarmupAccount(owner_id=other.id, label="foreign 2", status="paused"),
    ])
    db.commit()
    response = client.get("/api/telegram-accounts", headers=auth_headers)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["warmup_total"] == 1
    assert body["warmup_active"] == 1
    assert body["plan"] == "none"
    assert body["plan_status"] == "none"
    assert body["blocked"] is False


def test_ticket_is_deterministic_and_preserves_error_envelope(client, user, auth_headers, db, monkeypatch) -> None:
    from app import models
    from app.config import settings as app_config
    from app.services.settings import telegram_account_ticket

    first = models.WarmupAccount(owner_id=user.id, label="first")
    second = models.WarmupAccount(owner_id=user.id, label="second")
    foreign = models.User(telegram_id=6666, username="foreign", first_name="Foreign")
    db.add_all([first, second, foreign])
    db.flush()
    db.add(models.WarmupAccount(owner_id=foreign.id, label="foreign slot"))
    db.commit()
    monkeypatch.setattr(app_config, "tg_client_secret", " ticket-secret ")

    ticket = telegram_account_ticket(db, user, first.id, now=lambda: 1_700_000_000)
    payload = f"{user.id}.{first.id}.1.1700000300"
    signature = hmac.new(b"ticket-secret", payload.encode("utf-8"), hashlib.sha256).hexdigest()
    assert ticket.ticket == f"{payload}.{signature}"
    assert ticket.account_id == first.id
    assert telegram_account_ticket(db, user, second.id, now=lambda: 1_700_000_000).ticket.startswith(
        f"{user.id}.{second.id}.0.1700000300."
    )

    response = client.get(
        f"/api/telegram-accounts/ticket?account_id={foreign.id + 1}",
        headers={**auth_headers, "X-Request-Id": "ticket-404"},
    )
    assert response.status_code == 404
    assert response.json() == {"detail": "Аккаунт не найден", "request_id": "ticket-404"}

    monkeypatch.setattr(app_config, "tg_client_secret", "")
    response = client.get("/api/telegram-accounts/ticket", headers=auth_headers)
    assert response.status_code == 503
    assert response.json()["detail"] == (
        "Telegram-gateway не настроен: задайте TG_CLIENT_SECRET в .env и перезапустите."
    )


def test_ticket_empty_slots_uses_zero_and_primary(db, user, monkeypatch) -> None:
    from app.config import settings as app_config
    from app.services.settings import telegram_account_ticket

    monkeypatch.setattr(app_config, "tg_client_secret", "secret")
    ticket = telegram_account_ticket(db, user, None, now=lambda: 1)
    assert ticket.account_id is None
    assert ticket.ticket.startswith(f"{user.id}.0.1.301.")


def test_migrated_route_bodies_do_not_use_session_persistence_methods() -> None:
    from app.routers import settings

    for handler in (
        settings.read_settings,
        settings.read_telegram_account,
        settings.telegram_account_ticket,
    ):
        source = inspect.getsource(handler)
        for forbidden in (".query(", ".add(", ".commit(", ".rollback(", ".refresh("):
            assert forbidden not in source


def test_settings_compatibility_exports_and_legacy_is_cold_importable() -> None:
    from app import services
    from app.services import _legacy
    from app.services.settings import current_period, get_settings, roll_month

    assert services.get_settings is get_settings
    assert services.roll_month is roll_month
    assert services.current_period is current_period
    assert not hasattr(_legacy, "get_settings")
    assert not hasattr(_legacy, "roll_month")
    assert not hasattr(_legacy, "current_period")


def test_router_handlers_delegate_with_compatibility_aliases_and_never_touch_session(monkeypatch) -> None:
    from types import SimpleNamespace

    from app.routers import settings

    class PoisonedSession:
        def __getattr__(self, name):
            if name in {"query", "add", "commit", "rollback", "refresh"}:
                raise AssertionError(f"router touched session.{name}")
            raise AttributeError(name)

    db = PoisonedSession()
    user = SimpleNamespace(id=77)
    settings_result = object()
    account_result = object()
    ticket_result = object()
    seen: dict[str, tuple[tuple[object, ...], dict[str, object]]] = {}

    def settings_spy(*args, **kwargs):
        seen["settings"] = (args, kwargs)
        return settings_result

    def account_spy(*args, **kwargs):
        seen["account"] = (args, kwargs)
        return account_result

    def ticket_spy(*args, **kwargs):
        seen["ticket"] = (args, kwargs)
        return ticket_result

    monkeypatch.setattr(settings, "read_settings_service", settings_spy)
    monkeypatch.setattr(settings, "read_telegram_account_service", account_spy)
    monkeypatch.setattr(settings, "telegram_account_ticket_service", ticket_spy)

    assert settings.read_settings(user, db) is settings_result
    assert settings.read_telegram_account(user, db) is account_result
    assert settings.telegram_account_ticket(42, db, user) is ticket_result
    assert seen["settings"] == (
        (db, 77),
        {
            "get_settings_fn": settings.get_settings,
            "roll_month_fn": settings.roll_month,
            "ai_allowed_fn": settings.ai_allowed,
            "ai_provider_ready_fn": settings.ai_provider_ready,
        },
    )
    assert seen["account"] == (
        (db, user),
        {
            "get_subscription_fn": settings.get_subscription,
            "effective_plan_slug_fn": settings.effective_plan_slug,
            "get_plan_fn": settings.get_plan,
        },
    )
    assert seen["ticket"] == ((db, user, 42), {})


def test_telegram_account_full_cards_cover_none_and_active_subscription(
    client, user, auth_headers, db, monkeypatch
) -> None:
    from app import models
    from app.dependencies import get_current_user
    from app.main import app

    zero = client.get("/api/telegram-accounts", headers=auth_headers)
    assert zero.status_code == 200, zero.text
    assert zero.json() == {
        "telegram_id": user.telegram_id,
        "username": "tester",
        "name": "Test",
        "photo_url": "",
        "joined_at": user.created_at.isoformat(),
        "is_admin": False,
        "plan": "none",
        "plan_name": "Без тарифа",
        "plan_status": "none",
        "blocked": False,
        "paid_until": None,
        "warmup_total": 0,
        "warmup_active": 0,
    }

    other = models.User(telegram_id=7878, username="other", first_name="Other")
    db.add(other)
    db.flush()
    paid_until = datetime(2030, 1, 2, 3, 4, 5)
    db.add_all(
        [
            models.WarmupAccount(owner_id=user.id, label="active", status="active"),
            models.WarmupAccount(owner_id=user.id, label="paused", status="paused"),
            models.WarmupAccount(owner_id=other.id, label="foreign", status="active"),
        ]
    )
    subscription = db.query(models.Subscription).filter(models.Subscription.owner_id == user.id).one()
    subscription.plan = "zephyr"
    subscription.status = "active"
    subscription.blocked = True
    subscription.paid_until = paid_until
    db.commit()
    monkeypatch.setitem(app.dependency_overrides, get_current_user, lambda: user)
    active = client.get("/api/telegram-accounts", headers=auth_headers)
    assert active.status_code == 200, active.text
    assert active.json() == {
        "telegram_id": user.telegram_id,
        "username": "tester",
        "name": "Test",
        "photo_url": "",
        "joined_at": user.created_at.isoformat(),
        "is_admin": False,
        "plan": "zephyr",
        "plan_name": "Talaria",
        "plan_status": "active",
        "blocked": True,
        "paid_until": paid_until.isoformat(),
        "warmup_total": 2,
        "warmup_active": 1,
    }


def test_ticket_complete_matrix_and_error_envelopes(client, user, auth_headers, db, monkeypatch) -> None:
    from app import main, models
    from app.config import settings as app_config
    from app.services.settings import telegram_account_ticket

    first = models.WarmupAccount(owner_id=user.id, label="first")
    second = models.WarmupAccount(owner_id=user.id, label="second")
    foreign_user = models.User(telegram_id=9090, username="foreign", first_name="Foreign")
    db.add_all([first, second, foreign_user])
    db.flush()
    foreign = models.WarmupAccount(owner_id=foreign_user.id, label="foreign")
    db.add(foreign)
    db.commit()
    monkeypatch.setattr(app_config, "tg_client_secret", " ticket-secret ")

    def expected(chosen: int, primary: int) -> str:
        payload = f"{user.id}.{chosen}.{primary}.1700000300"
        signature = hmac.new(b"ticket-secret", payload.encode("utf-8"), hashlib.sha256).hexdigest()
        return f"{payload}.{signature}"

    assert telegram_account_ticket(db, user, None, now=lambda: 1_700_000_000).ticket == expected(first.id, 1)
    assert telegram_account_ticket(db, user, first.id, now=lambda: 1_700_000_000).ticket == expected(first.id, 1)
    assert telegram_account_ticket(db, user, second.id, now=lambda: 1_700_000_000).ticket == expected(second.id, 0)

    empty_user = models.User(telegram_id=9191, username="empty", first_name="Empty")
    db.add(empty_user)
    db.commit()
    empty_payload = f"{empty_user.id}.0.1.1700000300"
    empty_signature = hmac.new(b"ticket-secret", empty_payload.encode("utf-8"), hashlib.sha256).hexdigest()
    assert telegram_account_ticket(db, empty_user, None, now=lambda: 1_700_000_000).ticket == (
        f"{empty_payload}.{empty_signature}"
    )

    not_owned = client.get(
        f"/api/telegram-accounts/ticket?account_id={foreign.id}",
        headers={**auth_headers, "X-Request-Id": "ticket-404"},
    )
    assert not_owned.status_code == 404
    assert not_owned.json() == {"detail": "Аккаунт не найден", "request_id": "ticket-404"}

    monkeypatch.setattr(main.alerts, "report_error", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(app_config, "tg_client_secret", "   ")
    unavailable = client.get(
        "/api/telegram-accounts/ticket", headers={**auth_headers, "X-Request-Id": "ticket-503"}
    )
    assert unavailable.status_code == 503
    assert unavailable.json() == {
        "detail": "Telegram-gateway не настроен: задайте TG_CLIENT_SECRET в .env и перезапустите.",
        "request_id": "ticket-503",
    }
