"""Repository/service/facade contracts for tariff and subscription policy."""

from __future__ import annotations

import ast
import inspect
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError

from app import models, plans
from app.schemas import AdminActivateIn
from app.security import create_jwt

BACKEND_ROOT = Path(__file__).resolve().parents[1]
APP_ROOT = BACKEND_ROOT / "app"

FACADE_SIGNATURES = {
    "get_subscription": "(db: 'Session', owner_id: 'int')",
    "_as_utc": "(value: 'datetime | None') -> 'datetime | None'",
    "effective_plan_slug": "(db: 'Session', owner_id: 'int') -> 'str'",
    "is_blocked": "(db: 'Session', owner_id: 'int') -> 'bool'",
    "leads_this_month": "(db: 'Session', owner_id: 'int') -> 'int'",
    "check_object_limit": "(db: 'Session', owner_id: 'int', kind: 'str') -> 'None'",
    "check_leads_budget": "(db: 'Session', owner_id: 'int', want: 'int') -> 'int'",
    "is_first_purchase": "(db: 'Session', owner_id: 'int') -> 'bool'",
    "ai_allowed": "(db: 'Session', owner_id: 'int') -> 'bool'",
    "ai_offer_allowed": "(db: 'Session', owner_id: 'int') -> 'bool'",
    "ai_offer_plan_names": "() -> 'str'",
    "campaign_limits": "(db: 'Session', owner_id: 'int') -> 'tuple[int, int, int]'",
    "check_campaign_budget": (
        "(db: 'Session', owner_id: 'int', requested: 'int', *, ai_mode: 'bool') -> 'int'"
    ),
    "usage_summary": "(db: 'Session', owner_id: 'int') -> 'dict'",
    "is_expired": "(db: 'Session', owner_id: 'int') -> 'bool'",
    "activate_plan": (
        "(db: 'Session', owner_id: 'int', plan_slug: 'str', months: 'int', "
        "admin_id: 'int | None', note: 'str' = '') -> 'object'"
    ),
}


def _tree(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"))


def _called_attributes(tree: ast.AST) -> set[str]:
    return {
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }


def test_plan_layers_exist_and_enforce_transaction_boundaries() -> None:
    repository_path = APP_ROOT / "repositories" / "plans.py"
    service_path = APP_ROOT / "services" / "plans.py"
    assert repository_path.exists(), "RED: concrete plan repository is missing"
    assert service_path.exists(), "RED: transport-neutral plan service is missing"

    repository_tree = _tree(repository_path)
    service_tree = _tree(service_path)
    facade_tree = _tree(APP_ROOT / "plans.py")
    services_init_tree = _tree(APP_ROOT / "services" / "__init__.py")

    def roots(tree: ast.AST) -> set[str]:
        found: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                found.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                found.add(node.module.split(".")[0])
        return found

    assert not (roots(repository_tree) & {"fastapi", "starlette"})
    assert not (_called_attributes(repository_tree) & {"commit", "rollback", "refresh"})
    assert not (roots(service_tree) & {"fastapi", "starlette"})
    assert not (_called_attributes(service_tree) & {"query", "add", "flush"})
    assert not (
        _called_attributes(facade_tree) & {"query", "add", "flush", "commit", "rollback", "refresh"}
    )
    assert not any(
        isinstance(node, ast.ImportFrom) and node.module == "plans" and node.level == 1
        for node in ast.walk(services_init_tree)
    )


def test_facade_preserves_exact_db_surface() -> None:
    assert {
        name: str(inspect.signature(getattr(plans, name))) for name in FACADE_SIGNATURES
    } == FACADE_SIGNATURES


@pytest.mark.parametrize(
    "statement",
    [
        "import app.plans; import app.services.settings; import app.services.plans",
        "import app.services.plans; import app.plans; import app.services.settings",
        "import app.services.settings; import app.services.plans; import app.plans",
        "import app.routers.admin; import app.routers.subscriptions; import app.services.plans",
    ],
)
def test_plan_service_cold_import_permutations(statement: str) -> None:
    result = subprocess.run(
        [sys.executable, "-c", statement],
        cwd=BACKEND_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_repository_named_aggregates_are_owner_and_month_scoped(db, user) -> None:
    from app.repositories.plans import PlanRepository

    other = models.User(telegram_id=91002, username="plan-other", first_name="Other")
    db.add(other)
    db.flush()
    own_database = models.LeadDatabase(owner_id=user.id, name="own")
    foreign_database = models.LeadDatabase(owner_id=other.id, name="foreign")
    db.add_all([own_database, foreign_database])
    db.flush()
    month_start = datetime(2026, 8, 1, tzinfo=UTC)
    db.add_all(
        [
            models.Lead(
                database_id=own_database.id,
                channel_id="own-current",
                created_at=month_start + timedelta(days=1),
            ),
            models.Lead(
                database_id=own_database.id,
                channel_id="own-old",
                created_at=month_start - timedelta(seconds=1),
            ),
            models.Lead(
                database_id=foreign_database.id,
                channel_id="foreign-current",
                created_at=month_start + timedelta(days=1),
            ),
            models.Offer(owner_id=user.id, name="own"),
            models.Offer(owner_id=other.id, name="foreign"),
            models.Campaign(owner_id=user.id, name="own", database_name="own"),
            models.Campaign(owner_id=other.id, name="foreign", database_name="foreign"),
            models.WarmupAccount(owner_id=user.id, label="own"),
            models.WarmupAccount(owner_id=other.id, label="foreign"),
            models.CampaignSend(
                owner_id=user.id,
                campaign_id=101,
                lead_id=201,
                status="sent",
                offer_kind="ai",
                sent_at=month_start + timedelta(days=2),
            ),
            models.CampaignSend(
                owner_id=user.id,
                campaign_id=102,
                lead_id=202,
                status="failed",
                offer_kind="ai",
                sent_at=month_start + timedelta(days=2),
            ),
            models.CampaignSend(
                owner_id=user.id,
                campaign_id=103,
                lead_id=203,
                status="sent",
                offer_kind="regular",
                sent_at=month_start + timedelta(days=2),
            ),
            models.CampaignSend(
                owner_id=user.id,
                campaign_id=104,
                lead_id=204,
                status="sent",
                offer_kind="ai",
                sent_at=month_start - timedelta(seconds=1),
            ),
            models.CampaignSend(
                owner_id=other.id,
                campaign_id=105,
                lead_id=205,
                status="sent",
                offer_kind="ai",
                sent_at=month_start + timedelta(days=2),
            ),
        ]
    )
    db.commit()

    repository = PlanRepository(db)
    assert repository.count_databases(user.id) == 1
    assert repository.count_offers(user.id) == 1
    assert repository.count_campaigns(user.id) == 1
    assert repository.count_warmup(user.id) == 1
    assert repository.count_leads_since(user.id, month_start) == 1
    assert repository.count_sent_ai_since(user.id, month_start) == 1


class _DbSpy:
    def __init__(self, failure: Exception | None = None) -> None:
        self.calls: list[object] = []
        self.failure = failure

    def commit(self) -> None:
        self.calls.append("commit")
        if self.failure is not None:
            raise self.failure

    def rollback(self) -> None:
        self.calls.append("rollback")

    def refresh(self, row: object) -> None:
        self.calls.append(("refresh", row))


class _SubscriptionRepositoryStub:
    def __init__(self, rows: list[object | None]) -> None:
        self.rows = list(rows)
        self.calls: list[object] = []

    def get_subscription(self, owner_id: int) -> object | None:
        self.calls.append(("get", owner_id))
        return self.rows.pop(0)

    def require_subscription(self, owner_id: int) -> object:
        self.calls.append(("require", owner_id))
        row = self.rows.pop(0)
        assert row is not None
        return row

    def stage_subscription(self, row: object) -> None:
        self.calls.append(("stage", row))


def test_get_subscription_existing_new_race_and_generic_failure() -> None:
    from app.services import plans as service

    existing = SimpleNamespace(owner_id=7)
    repository = _SubscriptionRepositoryStub([existing])
    db = _DbSpy()
    assert service.get_subscription(db, 7, repository_factory=lambda _db: repository) is existing
    assert repository.calls == [("get", 7)]
    assert db.calls == []

    repository = _SubscriptionRepositoryStub([None])
    db = _DbSpy()
    created = service.get_subscription(db, 8, repository_factory=lambda _db: repository)
    assert created.owner_id == 8
    assert created.plan == "none"
    assert created.status == "none"
    assert repository.calls == [("get", 8), ("stage", created)]
    assert db.calls == ["commit", ("refresh", created)]

    winner = SimpleNamespace(owner_id=9, plan="zephyr")
    repository = _SubscriptionRepositoryStub([None, winner])
    integrity = IntegrityError("insert", {}, RuntimeError("duplicate"))
    db = _DbSpy(integrity)
    assert service.get_subscription(db, 9, repository_factory=lambda _db: repository) is winner
    assert repository.calls[0] == ("get", 9)
    assert repository.calls[1][0] == "stage"
    assert repository.calls[2] == ("require", 9)
    assert db.calls == ["commit", "rollback"]

    repository = _SubscriptionRepositoryStub([None])
    db = _DbSpy(RuntimeError("database offline"))
    with pytest.raises(RuntimeError, match="database offline"):
        service.get_subscription(db, 10, repository_factory=lambda _db: repository)
    assert db.calls == ["commit", "rollback"]

    class StageFailureRepository(_SubscriptionRepositoryStub):
        def stage_subscription(self, row: object) -> None:
            super().stage_subscription(row)
            raise RuntimeError("stage failed")

    repository = StageFailureRepository([None])
    db = _DbSpy()
    with pytest.raises(RuntimeError, match="stage failed"):
        service.get_subscription(db, 11, repository_factory=lambda _db: repository)
    assert db.calls == ["rollback"]


@pytest.mark.parametrize(
    ("stored", "paid_until", "effective", "expired"),
    [
        ("", None, "none", False),
        ("free", None, "none", False),
        ("none", None, "none", False),
        ("pro", None, "zephyr", False),
        (" MAX ", datetime(2035, 1, 1), "zephyr", False),
        ("ultra", datetime(2020, 1, 1, tzinfo=UTC), "none", True),
        ("zephyr", datetime(2035, 1, 1, tzinfo=UTC), "zephyr", False),
        ("unknown", None, "none", False),
    ],
)
def test_effective_and_expired_state_matrix_is_non_mutating(
    monkeypatch, stored: str, paid_until: datetime | None, effective: str, expired: bool
) -> None:
    sub = SimpleNamespace(
        plan=stored,
        paid_until=paid_until,
        blocked=False,
        activated_at=None,
    )
    before = vars(sub).copy()
    monkeypatch.setattr(plans, "get_subscription", lambda *_args: sub)
    assert plans.effective_plan_slug(object(), 1) == effective
    assert plans.is_expired(object(), 1) is expired
    assert vars(sub) == before


def test_is_expired_keeps_legacy_short_circuit_without_paid_until(monkeypatch) -> None:
    sub = SimpleNamespace(plan="free")
    monkeypatch.setattr(plans, "get_subscription", lambda *_args: sub)

    assert plans.is_expired(object(), 1) is False


def test_limit_errors_unlimited_paths_and_live_facade_collaborators(monkeypatch) -> None:
    from app.services import plans as service
    from app.services.errors import DomainError

    class Repository:
        def __init__(self, count: int = 0) -> None:
            self.count = count
            self.calls: list[int] = []

        def count_offers(self, owner_id: int) -> int:
            self.calls.append(owner_id)
            return self.count

    no_plan = {"slug": "none", "name": "Без тарифа", "max_offers": 0}
    repository = Repository(99)
    with pytest.raises(DomainError) as no_plan_error:
        service.check_object_limit(
            object(),
            4,
            "offers",
            effective_plan_slug_fn=lambda *_args: "none",
            get_plan_fn=lambda _slug: no_plan,
            repository_factory=lambda _db: repository,
        )
    assert no_plan_error.value.status_code == 402
    assert no_plan_error.value.detail == (
        "Тариф не активирован. Откройте раздел «Тариф» — после оплаты доступ откроется."
    )
    assert repository.calls == []

    finite = {"slug": "finite", "name": "Тест", "max_offers": 1}
    repository = Repository(1)
    with pytest.raises(DomainError) as limit_error:
        service.check_object_limit(
            object(),
            4,
            "offers",
            effective_plan_slug_fn=lambda *_args: "finite",
            get_plan_fn=lambda _slug: finite,
            repository_factory=lambda _db: repository,
        )
    assert limit_error.value.detail == (
        "Достигнут лимит тарифа «Тест»: офферов не больше 1. " "Проверьте доступ в разделе «Тариф»."
    )
    assert repository.calls == [4]

    unlimited = {"slug": "zephyr", "name": "Talaria", "max_offers": -1}
    repository = Repository(99)
    service.check_object_limit(
        object(),
        4,
        "offers",
        effective_plan_slug_fn=lambda *_args: "zephyr",
        get_plan_fn=lambda _slug: unlimited,
        repository_factory=lambda _db: repository,
    )
    assert repository.calls == []

    monkeypatch.setattr(plans, "effective_plan_slug", lambda *_args: "finite")
    monkeypatch.setattr(plans, "get_plan", lambda _slug: finite)
    monkeypatch.setattr(
        service,
        "PlanRepository",
        lambda _db: Repository(1),
    )
    with pytest.raises(HTTPException) as facade_error:
        plans.check_object_limit(object(), 4, "offers")
    assert facade_error.value.status_code == 402
    assert facade_error.value.detail == limit_error.value.detail


def test_lead_and_campaign_budgets_preserve_exact_clamping_and_messages(monkeypatch) -> None:
    from app.services import plans as service
    from app.services.errors import DomainError

    finite = {"slug": "finite", "name": "Тест", "monthly_leads": 10}
    assert (
        service.check_leads_budget(
            object(),
            3,
            8,
            effective_plan_slug_fn=lambda *_args: "finite",
            get_plan_fn=lambda _slug: finite,
            leads_this_month_fn=lambda *_args: 7,
        )
        == 3
    )
    with pytest.raises(DomainError) as exhausted:
        service.check_leads_budget(
            object(),
            3,
            8,
            effective_plan_slug_fn=lambda *_args: "finite",
            get_plan_fn=lambda _slug: finite,
            leads_this_month_fn=lambda *_args: 10,
        )
    assert exhausted.value.status_code == 402
    assert exhausted.value.detail == (
        "Исчерпан месячный лимит тарифа «Тест»: 10 лидов. "
        "Лимит обновится в начале месяца или оплатите тариф выше."
    )
    unlimited = {"slug": "zephyr", "name": "Talaria", "monthly_leads": -1}
    assert (
        service.check_leads_budget(
            object(),
            3,
            0,
            effective_plan_slug_fn=lambda *_args: "zephyr",
            get_plan_fn=lambda _slug: unlimited,
            leads_this_month_fn=lambda *_args: pytest.fail("must not count"),
        )
        == 0
    )

    monkeypatch.setattr(plans, "effective_plan_slug", lambda *_args: "finite")
    monkeypatch.setattr(plans, "get_plan", lambda _slug: finite)
    monkeypatch.setattr(plans, "leads_this_month", lambda *_args: 7)
    assert plans.check_leads_budget(object(), 3, 8) == 3

    monkeypatch.setattr(plans, "campaign_limits", lambda *_args: (4, 3, 2))
    monkeypatch.setattr(
        plans,
        "get_plan",
        lambda _slug: {"slug": "finite", "sending": True},
    )
    assert plans.check_campaign_budget(object(), 3, 9, ai_mode=False) == 3
    assert plans.check_campaign_budget(object(), 3, 9, ai_mode=True) == 2

    monkeypatch.setattr(plans, "campaign_limits", lambda *_args: (0, 3, 2))
    with pytest.raises(HTTPException) as messages:
        plans.check_campaign_budget(object(), 3, 9, ai_mode=False)
    assert messages.value.detail == "Месячный лимит отправок исчерпан"
    monkeypatch.setattr(plans, "campaign_limits", lambda *_args: (4, 3, 0))
    with pytest.raises(HTTPException) as ai:
        plans.check_campaign_budget(object(), 3, 9, ai_mode=True)
    assert ai.value.detail == "Месячный лимит персональных AI-офферов исчерпан"


def test_campaign_limits_and_usage_summary_keep_exact_aggregates() -> None:
    from app.services import plans as service

    seen: dict[str, object] = {}

    class Repository:
        def count_sent_ai_since(self, owner_id: int, start: datetime) -> int:
            seen["ai"] = (owner_id, start)
            return 2

        def count_databases(self, owner_id: int) -> int:
            return 1

        def count_offers(self, owner_id: int) -> int:
            return 2

        def count_campaigns(self, owner_id: int) -> int:
            return 3

        def count_warmup(self, owner_id: int) -> int:
            return 4

    settings = SimpleNamespace(monthly_used=7)
    plan = {
        "slug": "finite",
        "monthly_messages": 10,
        "monthly_ai_offers": 5,
        "max_per_launch": 4,
        "monthly_leads": 20,
        "max_databases": 5,
        "max_offers": 6,
        "max_campaigns": 7,
        "max_warmup": 8,
    }
    now = datetime(2026, 8, 22, 12, tzinfo=UTC)
    limits = service.campaign_limits(
        object(),
        12,
        effective_plan_slug_fn=lambda *_args: "finite",
        get_plan_fn=lambda _slug: plan,
        get_settings_fn=lambda *_args: settings,
        roll_month_fn=lambda _db, row: row,
        repository_factory=lambda _db: Repository(),
        now_fn=lambda: now,
    )
    assert limits == (3, 4, 3)
    assert seen["ai"] == (12, datetime(2026, 8, 1, tzinfo=UTC))

    summary = service.usage_summary(
        object(),
        12,
        effective_plan_slug_fn=lambda *_args: "finite",
        get_plan_fn=lambda _slug: plan,
        get_settings_fn=lambda *_args: settings,
        roll_month_fn=lambda _db, row: row,
        campaign_limits_fn=lambda *_args: (3, 4, 3),
        leads_this_month_fn=lambda *_args: 9,
        repository_factory=lambda _db: Repository(),
    )
    assert summary == {
        "leads_used": 9,
        "leads_limit": 20,
        "databases": 1,
        "databases_limit": 5,
        "offers": 2,
        "offers_limit": 6,
        "campaigns": 3,
        "campaigns_limit": 7,
        "warmup": 4,
        "warmup_limit": 8,
        "messages_used": 7,
        "messages_limit": 10,
        "ai_offers_used": 2,
        "ai_offers_limit": 5,
        "max_per_launch": 4,
    }


def test_activation_normalizes_aliases_extends_terms_and_rolls_back() -> None:
    from app.services import plans as service
    from app.services.errors import DomainError

    now = datetime(2026, 8, 22, 12, tzinfo=UTC)
    settings = SimpleNamespace(monthly_limit=0)
    sub = SimpleNamespace(
        plan="none",
        status="none",
        paid_until=now + timedelta(days=10),
        note="old note",
        activated_by=None,
        activated_at=None,
    )
    order: list[str] = []
    db = _DbSpy()

    result = service.activate_plan(
        db,
        owner_id=5,
        plan_slug=" pro ",
        months=3,
        admin_id=77,
        note="",
        get_settings_fn=lambda *_args: (order.append("settings") or settings),
        get_subscription_fn=lambda *_args: (order.append("subscription") or sub),
        now_fn=lambda: now,
    )
    assert result is sub
    assert order == ["settings", "subscription"]
    assert sub.plan == "zephyr"
    assert sub.status == "active"
    assert sub.paid_until == now + timedelta(days=100)
    assert sub.note == "old note"
    assert sub.activated_by == 77
    assert sub.activated_at == now
    assert settings.monthly_limit == -1
    assert db.calls == ["commit", ("refresh", sub)]

    sub.paid_until = datetime(2030, 1, 1)
    db = _DbSpy()
    service.activate_plan(
        db,
        owner_id=5,
        plan_slug="zephyr",
        months=1,
        admin_id=None,
        get_settings_fn=lambda *_args: settings,
        get_subscription_fn=lambda *_args: sub,
        now_fn=lambda: now,
    )
    assert sub.paid_until == datetime(2030, 1, 31, tzinfo=UTC)

    db = _DbSpy()
    service.activate_plan(
        db,
        owner_id=5,
        plan_slug="free",
        months=0,
        admin_id=1,
        note="removed",
        get_settings_fn=lambda *_args: settings,
        get_subscription_fn=lambda *_args: sub,
        now_fn=lambda: now,
    )
    assert (sub.plan, sub.status, sub.paid_until, settings.monthly_limit) == (
        "none",
        "none",
        None,
        0,
    )

    touched: list[str] = []
    with pytest.raises(DomainError) as unknown:
        service.activate_plan(
            _DbSpy(),
            owner_id=5,
            plan_slug="mystery",
            months=1,
            admin_id=1,
            get_settings_fn=lambda *_args: touched.append("settings"),
            get_subscription_fn=lambda *_args: touched.append("subscription"),
            now_fn=lambda: now,
        )
    assert unknown.value.status_code == 400
    assert unknown.value.detail == "Неизвестный тариф: mystery"
    assert touched == []

    failing_sub = SimpleNamespace(
        plan="none",
        status="none",
        paid_until=None,
        note="",
        activated_by=None,
        activated_at=None,
    )
    failing_db = _DbSpy(RuntimeError("commit failed"))
    with pytest.raises(RuntimeError, match="commit failed"):
        service.activate_plan(
            failing_db,
            owner_id=5,
            plan_slug="zephyr",
            months=1,
            admin_id=1,
            get_settings_fn=lambda *_args: settings,
            get_subscription_fn=lambda *_args: failing_sub,
            now_fn=lambda: now,
        )
    assert failing_db.calls == ["commit", "rollback"]


def test_unknown_activation_keeps_real_http_error_envelope(client, db, user) -> None:
    admin = models.User(
        telegram_id=91003,
        username="admin_user",
        first_name="Admin",
    )
    db.add(admin)
    db.add(models.AccessWhitelist(username="admin_user", note="D2 admin"))
    db.commit()
    db.refresh(admin)
    token, _jti = create_jwt(admin.id)
    response = client.post(
        f"/api/admin/users/{user.id}/subscription",
        json={"plan": "mystery", "months": 1, "note": ""},
        headers={
            "Authorization": f"Bearer {token}",
            "X-Request-Id": "plans-unknown",
        },
    )
    assert response.status_code == 400
    assert response.json() == {
        "detail": "Неизвестный тариф: mystery",
        "request_id": "plans-unknown",
    }


def test_admin_event_failure_leaves_activation_durable(db, user, monkeypatch) -> None:
    from app.routers import admin as admin_router

    admin = models.User(telegram_id=91004, username="admin_user", first_name="Admin")
    db.add(admin)
    db.commit()
    db.refresh(admin)

    def fail_event(*_args, **_kwargs) -> None:
        raise RuntimeError("event unavailable")

    monkeypatch.setattr(admin_router, "log_event", fail_event)
    with pytest.raises(RuntimeError, match="event unavailable"):
        admin_router.activate_subscription(
            user.id,
            AdminActivateIn(plan="zephyr", months=1, note="paid manually"),
            admin,
            db,
        )

    db.expire_all()
    stored = db.query(models.Subscription).filter(models.Subscription.owner_id == user.id).one()
    assert stored.plan == "zephyr"
    assert stored.status == "active"
    assert stored.note == "paid manually"
    assert stored.activated_by == admin.id
    assert stored.paid_until is not None
