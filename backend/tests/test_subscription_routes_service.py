"""Thin subscription/admin HTTP adapters over the accepted plan service."""

from __future__ import annotations

import ast
import inspect
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from app import models, plans
from app.schemas import AdminActivateIn, SubscriptionOut
from app.security import create_jwt

USAGE = {
    "leads_used": 1,
    "leads_limit": -1,
    "databases": 2,
    "databases_limit": -1,
    "offers": 3,
    "offers_limit": -1,
    "campaigns": 4,
    "campaigns_limit": -1,
    "warmup": 5,
    "warmup_limit": -1,
    "messages_used": 6,
    "messages_limit": -1,
    "ai_offers_used": 0,
    "ai_offers_limit": -1,
    "max_per_launch": -1,
}


def test_subscription_application_services_exist() -> None:
    from app.services import plans as service

    assert hasattr(service, "read_subscription"), "RED: subscription read service is missing"
    assert hasattr(
        service, "activate_subscription_for_admin"
    ), "RED: admin activation service is missing"


@pytest.mark.parametrize(
    (
        "stored",
        "status",
        "blocked",
        "paid_until",
        "effective",
        "expired",
        "first_purchase",
    ),
    [
        ("none", "none", False, None, "none", False, True),
        ("zephyr", "active", False, datetime(2035, 1, 1, tzinfo=UTC), "zephyr", False, False),
        ("zephyr", "active", True, datetime(2035, 1, 1, tzinfo=UTC), "zephyr", False, False),
        ("pro", "active", False, None, "zephyr", False, False),
        ("zephyr", "active", False, datetime(2020, 1, 1, tzinfo=UTC), "none", True, False),
        # Пробный доступ: истёк — фактический план «none», но интерфейс должен
        # отличать «триал закончился» от «тарифа никогда не было».
        ("trial", "trial", False, datetime(2020, 1, 1, tzinfo=UTC), "none", True, True),
    ],
)
def test_read_subscription_exact_state_matrix(
    stored: str,
    status: str,
    blocked: bool,
    paid_until: datetime | None,
    effective: str,
    expired: bool,
    first_purchase: bool,
) -> None:
    from app.services import plans as service

    sub = SimpleNamespace(
        plan=stored,
        status=status,
        blocked=blocked,
        paid_until=paid_until,
        note="manual note",
    )
    before = vars(sub).copy()
    result = service.read_subscription(
        object(),
        11,
        get_subscription_fn=lambda *_args: sub,
        effective_plan_slug_fn=lambda *_args: effective,
        get_plan_fn=plans.get_plan,
        is_first_purchase_fn=lambda *_args: first_purchase,
        is_expired_fn=lambda *_args: expired,
        usage_summary_fn=lambda *_args: dict(USAGE),
        all_public_plans_fn=plans.all_public_plans,
        admin_contact="admin_user",
        first_purchase_discount_pct=40,
    )
    assert isinstance(result, SubscriptionOut)
    assert result.model_dump() == {
        "plan": effective,
        "plan_name": plans.get_plan(effective)["name"],
        "stored_plan": stored,
        "status": status,
        "blocked": blocked,
        "paid_until": paid_until,
        "expired": expired,
        "ai": bool(plans.get_plan(effective)["ai"]),
        "ai_offer": bool(plans.get_plan(effective)["ai_offer"]),
        "is_trial": effective == plans.TRIAL_PLAN,
        "trial_expired": stored == plans.TRIAL_PLAN and effective == "none",
        "trial_days_left": 0,
        "sending": bool(plans.get_plan(effective).get("sending")),
        "note": "manual note",
        "admin_contact": "admin_user",
        "usage": USAGE,
        "plans": plans.all_public_plans(),
        "first_purchase": first_purchase,
        "first_purchase_discount_pct": 40 if first_purchase else 0,
    }
    assert vars(sub) == before


def test_read_subscription_preserves_legacy_fallback_fields() -> None:
    from app.services import plans as service

    sub = SimpleNamespace(
        plan="",
        status="",
        blocked=False,
        paid_until=None,
        note="",
    )
    result = service.read_subscription(
        object(),
        12,
        get_subscription_fn=lambda *_args: sub,
        effective_plan_slug_fn=lambda *_args: "none",
        get_plan_fn=plans.get_plan,
        is_first_purchase_fn=lambda *_args: True,
        is_expired_fn=lambda *_args: False,
        usage_summary_fn=lambda *_args: dict(USAGE),
        all_public_plans_fn=plans.all_public_plans,
        admin_contact="admin_user",
        first_purchase_discount_pct=0,
    )
    assert result.stored_plan == "free"
    assert result.status == "active"


class _PoisonDb:
    def __getattribute__(self, name: str) -> object:
        if name.startswith("_"):
            return object.__getattribute__(self, name)
        raise AssertionError(f"router touched persistence: {name}")


def test_my_subscription_is_a_thin_adapter_with_live_aliases(monkeypatch) -> None:
    from app.routers import subscriptions

    user = SimpleNamespace(id=91)
    db = _PoisonDb()
    expected = SubscriptionOut(
        plan="none",
        plan_name="Без тарифа",
        stored_plan="none",
        status="none",
        usage=USAGE,
    )
    seen: dict[str, object] = {}

    def read_subscription(db_arg: object, owner_id: int, **kwargs: object) -> SubscriptionOut:
        seen.update({"db": db_arg, "owner_id": owner_id, "kwargs": kwargs})
        return expected

    monkeypatch.setattr(subscriptions, "read_subscription", read_subscription)
    assert subscriptions.my_subscription(user, db) is expected
    assert seen["db"] is db
    assert seen["owner_id"] == 91
    kwargs = seen["kwargs"]
    assert kwargs["get_subscription_fn"] is subscriptions.get_subscription
    assert kwargs["effective_plan_slug_fn"] is subscriptions.effective_plan_slug
    assert kwargs["get_plan_fn"] is subscriptions.get_plan
    assert kwargs["is_first_purchase_fn"] is subscriptions.is_first_purchase
    assert kwargs["is_expired_fn"] is subscriptions.is_expired
    assert kwargs["usage_summary_fn"] is subscriptions.usage_summary
    assert kwargs["all_public_plans_fn"] is subscriptions.all_public_plans


class _ActivationRepository:
    def __init__(self, user: object | None) -> None:
        self.user = user
        self.calls: list[int] = []

    def get_user(self, user_id: int) -> object | None:
        self.calls.append(user_id)
        return self.user


def test_admin_activation_service_missing_success_and_failure_order() -> None:
    from app.services import plans as service
    from app.services.errors import DomainError

    repository = _ActivationRepository(None)
    touched: list[str] = []
    with pytest.raises(DomainError) as missing:
        service.activate_subscription_for_admin(
            object(),
            404,
            "zephyr",
            1,
            7,
            "",
            repository_factory=lambda _db: repository,
            activate_plan_fn=lambda *_args, **_kwargs: touched.append("activate"),
            get_plan_fn=plans.get_plan,
            log_event_fn=lambda *_args, **_kwargs: touched.append("event"),
        )
    assert missing.value.status_code == 404
    assert missing.value.detail == "Пользователь не найден"
    assert repository.calls == [404]
    assert touched == []

    repository = _ActivationRepository(SimpleNamespace(id=5))
    sub = SimpleNamespace(
        id=17,
        plan="zephyr",
        paid_until=datetime(2026, 9, 21, tzinfo=UTC),
    )
    calls: list[object] = []

    def activate(
        db: object,
        owner_id: int,
        plan_slug: str,
        months: int,
        admin_id: int,
        note: str,
    ) -> object:
        calls.append(("activate", db, owner_id, plan_slug, months, admin_id, note))
        return sub

    def event(
        db: object,
        owner_id: int,
        scope: str,
        message: str,
        level: str,
        ref_type: str,
        ref_id: int,
    ) -> None:
        calls.append(("event", db, owner_id, scope, message, level, ref_type, ref_id))

    db = object()
    assert (
        service.activate_subscription_for_admin(
            db,
            5,
            "zephyr",
            2,
            7,
            "paid manually",
            repository_factory=lambda _db: repository,
            activate_plan_fn=activate,
            get_plan_fn=plans.get_plan,
            log_event_fn=event,
        )
        is sub
    )
    assert calls == [
        ("activate", db, 5, "zephyr", 2, 7, "paid manually"),
        (
            "event",
            db,
            5,
            "settings",
            "Админ активировал тариф «Talaria» до 2026-09-21 — paid manually",
            "success",
            "subscription",
            17,
        ),
    ]

    calls.clear()
    with pytest.raises(RuntimeError, match="activation failed"):
        service.activate_subscription_for_admin(
            db,
            5,
            "zephyr",
            1,
            7,
            "",
            repository_factory=lambda _db: repository,
            activate_plan_fn=lambda *_args, **_kwargs: (_ for _ in ()).throw(
                RuntimeError("activation failed")
            ),
            get_plan_fn=plans.get_plan,
            log_event_fn=lambda *_args, **_kwargs: calls.append("event"),
        )
    assert calls == []

    durable: list[str] = []
    with pytest.raises(RuntimeError, match="event failed"):
        service.activate_subscription_for_admin(
            db,
            5,
            "zephyr",
            1,
            7,
            "",
            repository_factory=lambda _db: repository,
            activate_plan_fn=lambda *_args, **_kwargs: (durable.append("activated") or sub),
            get_plan_fn=plans.get_plan,
            log_event_fn=lambda *_args, **_kwargs: (_ for _ in ()).throw(
                RuntimeError("event failed")
            ),
        )
    assert durable == ["activated"]


def test_admin_activation_router_is_thin_and_preserves_patch_symbols(monkeypatch) -> None:
    from app.routers import admin as admin_router

    db = _PoisonDb()
    admin = SimpleNamespace(id=7)
    payload = AdminActivateIn(plan="zephyr", months=1, note="manual")
    expected = object()
    seen: dict[str, object] = {}

    def activate_service(
        db_arg: object,
        user_id: int,
        plan: str,
        months: int,
        admin_id: int,
        note: str,
        **kwargs: object,
    ) -> object:
        seen.update(
            {
                "db": db_arg,
                "user_id": user_id,
                "plan": plan,
                "months": months,
                "admin_id": admin_id,
                "note": note,
                "kwargs": kwargs,
            }
        )
        return SimpleNamespace(id=1)

    monkeypatch.setattr(
        admin_router,
        "activate_subscription_for_admin",
        activate_service,
    )
    monkeypatch.setattr(admin_router, "user_detail", lambda *_args: expected)
    assert admin_router.activate_subscription(5, payload, admin, db) is expected
    assert seen == {
        "db": db,
        "user_id": 5,
        "plan": "zephyr",
        "months": 1,
        "admin_id": 7,
        "note": "manual",
        "kwargs": {
            "activate_plan_fn": admin_router.activate_plan,
            "get_plan_fn": admin_router.get_plan,
            "log_event_fn": admin_router.log_event,
        },
    }


def test_route_handlers_contain_no_direct_persistence_calls() -> None:
    from app.routers import admin as admin_router
    from app.routers import subscriptions

    for handler in (subscriptions.my_subscription, admin_router.activate_subscription):
        tree = ast.parse(inspect.getsource(handler))
        function = next(node for node in tree.body if isinstance(node, ast.FunctionDef))
        function.decorator_list = []
        calls = {
            node.func.attr
            for node in ast.walk(function)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
        }
        assert not (calls & {"get", "query", "add", "flush", "commit", "rollback", "refresh"})


def _admin_headers(db) -> dict[str, str]:
    admin = models.User(telegram_id=92001, username="admin_user", first_name="Admin")
    db.add(admin)
    db.commit()
    db.refresh(admin)
    token, _jti = create_jwt(admin.id)
    return {"Authorization": f"Bearer {token}"}


def test_admin_activation_real_http_missing_unknown_and_success(client, db, user) -> None:
    headers = _admin_headers(db)
    missing = client.post(
        "/api/admin/users/999999/subscription",
        json={"plan": "zephyr", "months": 1, "note": ""},
        headers={**headers, "X-Request-Id": "subscription-missing"},
    )
    assert missing.status_code == 404
    assert missing.json() == {
        "detail": "Пользователь не найден",
        "request_id": "subscription-missing",
    }

    unknown = client.post(
        f"/api/admin/users/{user.id}/subscription",
        json={"plan": "mystery", "months": 1, "note": ""},
        headers={**headers, "X-Request-Id": "subscription-unknown"},
    )
    assert unknown.status_code == 400
    assert unknown.json() == {
        "detail": "Неизвестный тариф: mystery",
        "request_id": "subscription-unknown",
    }

    success = client.post(
        f"/api/admin/users/{user.id}/subscription",
        json={"plan": "zephyr", "months": 1, "note": "manual receipt"},
        headers=headers,
    )
    assert success.status_code == 200, success.text
    body = success.json()
    assert body["plan"] == "zephyr"
    assert body["stored_plan"] == "zephyr"
    assert body["sub_status"] == "active"
    assert body["sub_note"] == "manual receipt"
    event = (
        db.query(models.EventLog)
        .filter(models.EventLog.owner_id == user.id, models.EventLog.scope == "settings")
        .one()
    )
    assert event.level == "success"
    assert event.message.startswith("Админ активировал тариф «Talaria» до ")
    assert event.message.endswith(" — manual receipt")
    assert event.ref_type == "subscription"
    assert event.ref_id is not None


def test_subscription_router_service_alias_intercepts_real_request(
    client, user, auth_headers, monkeypatch
) -> None:
    from app.routers import subscriptions

    expected = SubscriptionOut(
        plan="none",
        plan_name="Intercepted",
        stored_plan="none",
        status="none",
        usage=USAGE,
        admin_contact="admin_user",
    )
    calls: list[int] = []

    def intercepted(_db, owner_id: int, **_kwargs) -> SubscriptionOut:
        calls.append(owner_id)
        return expected

    monkeypatch.setattr(subscriptions, "read_subscription", intercepted)
    response = client.get("/api/subscription", headers=auth_headers)
    assert response.status_code == 200
    assert response.json()["plan_name"] == "Intercepted"
    assert calls == [user.id]


def test_active_subscription_http_contract_stays_exact(client, db, user, auth_headers) -> None:
    db.add(
        models.Subscription(
            owner_id=user.id,
            plan="pro",
            status="active",
            paid_until=datetime.now(UTC) + timedelta(days=30),
            note="legacy",
        )
    )
    db.commit()
    response = client.get("/api/subscription", headers=auth_headers)
    assert response.status_code == 200
    body = response.json()
    assert body["plan"] == "zephyr"
    assert body["plan_name"] == "Talaria"
    assert body["stored_plan"] == "pro"
    assert body["status"] == "active"
    assert body["expired"] is False
    assert body["ai"] is True
    assert body["ai_offer"] is True
    assert body["note"] == "legacy"
    assert body["admin_contact"] == "admin_user"
    assert body["plans"] == plans.all_public_plans()
    assert body["usage"]["messages_limit"] == -1
