"""Exact regression contract for the pure tariff catalog compatibility extraction."""

from __future__ import annotations

import ast
import inspect
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from app import plans  # noqa: E402
from app.main import app  # noqa: E402
from scripts.contract_gate import normalize_openapi  # noqa: E402

CATALOG_NAMES = (
    "PLAN_ORDER",
    "TRIAL_PLAN",
    "TRIAL_DAYS",
    "LEGACY_PLAN_ALIASES",
    "NO_PLAN",
    "PLANS",
    "DEFAULT_PLAN",
    "FIRST_PURCHASE_DISCOUNT_PCT",
    "QUARTER_DISCOUNT_PCT",
    "PROMO_CODE",
    "PROMO_DISCOUNT_PCT",
    "first_purchase_price",
    "get_plan",
    "quote_plan",
    "public_plan",
    "all_public_plans",
)

EXPECTED_CONSTANTS = {
    "PLAN_ORDER": ["zephyr"],
    "TRIAL_PLAN": "trial",
    "TRIAL_DAYS": 14,
    "LEGACY_PLAN_ALIASES": {"pro": "zephyr", "max": "zephyr", "ultra": "zephyr"},
    "NO_PLAN": "none",
    "DEFAULT_PLAN": "none",
    "FIRST_PURCHASE_DISCOUNT_PCT": 0,
    "QUARTER_DISCOUNT_PCT": 15,
    "PROMO_CODE": "ZEPHYR30",
    "PROMO_DISCOUNT_PCT": 30,
}

EXPECTED_PLANS = {
    "none": {
        "slug": 'none',
        "sending": False,
        "name": 'Без тарифа',
        "price_kzt": 0,
        "price_rub": 0,
        "tagline": 'Тариф не активирован',
        "monthly_leads": 0,
        "monthly_messages": 0,
        "monthly_ai_offers": 0,
        "max_per_launch": 0,
        "max_databases": 0,
        "max_offers": 0,
        "max_campaigns": 0,
        "max_warmup": 0,
        "ai": False,
        "ai_offer": False,
        "features": [],
    },
    "pro": {
        "slug": 'pro',
        "sending": True,
        "name": 'Про',
        "price_kzt": 14_900,
        "price_rub": 2_900,
        "tagline": 'Для стабильного потока',
        "monthly_leads": 10_000,
        "monthly_messages": 2_000,
        "monthly_ai_offers": 0,
        "max_per_launch": 50,
        "max_databases": 50,
        "max_offers": 50,
        "max_campaigns": 100,
        "max_warmup": 5,
        "ai": True,
        "ai_offer": False,
        "features": [
            'До 10 000 лидов в месяц',
            'До 2 000 отправок в месяц, 50 за запуск',
            'До 50 баз и 50 офферов',
            '5 аккаунтов для прогрева',
            'ИИ-помощник по вашим данным',
            'Обычные офферы со спинтаксом',
            'AI-офферы — только в Макс и Ультра',
        ],
    },
    "max": {
        "slug": 'max',
        "sending": True,
        "name": 'Макс',
        "price_kzt": 39_900,
        "price_rub": 7_900,
        "tagline": 'Для команд и агентств',
        "monthly_leads": 50_000,
        "monthly_messages": 10_000,
        "monthly_ai_offers": 2_000,
        "max_per_launch": 150,
        "max_databases": 200,
        "max_offers": 200,
        "max_campaigns": 500,
        "max_warmup": 20,
        "ai": True,
        "ai_offer": True,
        "features": [
            'До 50 000 лидов в месяц',
            'До 10 000 отправок в месяц, 150 за запуск',
            'До 200 баз и 200 офферов',
            '20 аккаунтов для прогрева',
            'AI-офферы: индивидуальный текст под каждый лид',
            'ИИ-помощник и приоритетная поддержка',
        ],
    },
    "ultra": {
        "slug": 'ultra',
        "sending": True,
        "name": 'Ультра',
        "price_kzt": 89_900,
        "price_rub": 17_900,
        "tagline": 'Максимальный объём',
        "monthly_leads": -1,
        "monthly_messages": 50_000,
        "monthly_ai_offers": 10_000,
        "max_per_launch": 200,
        "max_databases": -1,
        "max_offers": -1,
        "max_campaigns": -1,
        "max_warmup": -1,
        "ai": True,
        "ai_offer": True,
        "features": [
            'Безлимит лидов, баз и офферов; 50 000 отправок',
            'Неограниченный прогрев',
            'До 10 000 индивидуальных AI-офферов в месяц',
            'Персональный менеджер',
        ],
    },
    "trial": {
        "slug": 'trial',
        "sending": False,
        "name": 'Пробный',
        "price_kzt": 0,
        "price_rub": 0,
        "tagline": 'Поиск и проверка лидов, 14 дней бесплатно',
        "monthly_leads": 1_000,
        "monthly_messages": 0,
        "monthly_ai_offers": 0,
        "max_per_launch": 0,
        "max_databases": 3,
        "max_offers": 3,
        "max_campaigns": 0,
        "max_warmup": 1,
        "ai": False,
        "ai_offer": False,
        "features": [
            'Поиск каналов по YouTube и сбор контактов',
            'До 1 000 лидов в месяц, 3 базы и 3 оффера',
            'Один аккаунт Telegram для проверки лидов',
            'Отправка сообщений недоступна',
            'ИИ-помощник недоступен',
        ],
    },
    "zephyr": {
        "slug": 'zephyr',
        "sending": True,
        "name": 'Talaria',
        "price_kzt": 25_000,
        "price_rub": 5_000,
        "tagline": 'Все инструменты для персональных Telegram-кампаний',
        "monthly_leads": -1,
        "monthly_messages": -1,
        "monthly_ai_offers": -1,
        "max_per_launch": -1,
        "max_databases": -1,
        "max_offers": -1,
        "max_campaigns": -1,
        "max_warmup": -1,
        "ai": True,
        "ai_offer": True,
        "features": [
            'Поиск и проверка лидов по YouTube',
            'Базы лидов, офферы и Telegram-рассылки',
            'Персонализация вступлений по тематике канала',
            'Статистика доставки, прочтений, ответов и интереса',
            'Тепловая карта лучшего времени отправки',
            'Центр безопасности аккаунтов и автопауза',
            'ИИ-помощник по данным рабочего пространства',
        ],
    },
}

EXPECTED_SIGNATURES = {
    "first_purchase_price": "(full_price: 'int') -> 'int'",
    "get_plan": "(slug: 'str | None') -> 'dict'",
    "quote_plan": (
            "(months: 'int', promo_code: 'str' = '', currency: 'str' = 'RUB', *, "
            "promo_lookup: 'Callable[[str], tuple[str, int]] | None' = None) -> 'dict'"
        ),
    "public_plan": "(slug: 'str') -> 'dict'",
    "all_public_plans": "() -> 'list[dict]'",
}

PLAN_CONSUMER_MODULES = (
    "app.ai_prompt",
    "app.dependencies",
    "app.sending",
    "app.routers.admin",
    "app.routers.ai",
    "app.routers.auth",
    "app.routers.campaigns",
    "app.routers.search",
    "app.routers.subscriptions",
    "app.routers.settings",
    "app.routers.offers",
    "app.routers.warmup",
    "app.routers.ws",
    "app.services.settings",
)


def _expected_quote(
    currency: str, months: int, promo_code: str, base: int, bundle: int, promo: int, total: int
) -> dict:
    valid = promo_code.strip().upper() == "ZEPHYR30"
    return {
        "plan_slug": "zephyr",
        "plan_name": "Talaria",
        "period_months": months,
        "currency": currency,
        "base_total": base,
        "bundle_discount": bundle,
        "promo_discount": promo,
        "total_amount": total,
        "base_total_rub": base if currency == "RUB" else 0,
        "bundle_discount_rub": bundle if currency == "RUB" else 0,
        "promo_discount_rub": promo if currency == "RUB" else 0,
        "total_rub": total if currency == "RUB" else 0,
        "promo_code": "ZEPHYR30" if valid else "",
        "promo_valid": valid,
        "promo_message": "Промокод применён: −30% от первого месяца"
        if valid
        else ("Промокод не найден" if promo_code.strip() else ""),
        "admin_contact": "admin_user",
    }


@pytest.mark.parametrize(
    ("currency", "months", "promo_code", "base", "bundle", "promo", "total"),
    [
        ("RUB", 1, "", 5_000, 0, 0, 5_000),
        ("RUB", 1, "ZEPHYR30", 5_000, 0, 1_500, 3_500),
        ("RUB", 1, "bad", 5_000, 0, 0, 5_000),
        ("RUB", 3, "", 15_000, 2_250, 0, 12_750),
        ("RUB", 3, "zephyr30", 15_000, 2_250, 1_500, 11_250),
        ("RUB", 3, "bad", 15_000, 2_250, 0, 12_750),
        ("KZT", 1, "", 25_000, 0, 0, 25_000),
        ("KZT", 1, " ZEPHYR30 ", 25_000, 0, 7_500, 17_500),
        ("KZT", 1, "bad", 25_000, 0, 0, 25_000),
        ("KZT", 3, "", 75_000, 11_250, 0, 63_750),
        ("KZT", 3, "ZEPHYR30", 75_000, 11_250, 7_500, 56_250),
        ("KZT", 3, "bad", 75_000, 11_250, 0, 63_750),
    ],
)
def test_http_quote_matrix_is_exact(
    client,
    currency: str,
    months: int,
    promo_code: str,
    base: int,
    bundle: int,
    promo: int,
    total: int,
) -> None:
    assert client.get("/api/plans/quote").status_code == 405
    response = client.post(
        "/api/plans/quote",
        json={"period_months": months, "promo_code": promo_code, "currency": currency},
    )
    assert response.status_code == 200
    assert response.json() == _expected_quote(
        currency, months, promo_code, base, bundle, promo, total
    )


def test_catalog_snapshot_public_dicts_and_compatibility_identity() -> None:
    from app import plan_catalog

    assert {name: getattr(plan_catalog, name) for name in EXPECTED_CONSTANTS} == EXPECTED_CONSTANTS
    assert plan_catalog.PLANS == EXPECTED_PLANS
    assert plan_catalog.get_plan("  Ultra ") == EXPECTED_PLANS["zephyr"]
    assert plan_catalog.get_plan("unknown") == EXPECTED_PLANS["none"]
    expected_public = {
        "slug": "zephyr",
        "name": "Talaria",
        "price_kzt": 25_000,
        "price_rub": 5_000,
        "price_kzt_first": 25_000,
        "price_rub_first": 5_000,
        "tagline": "Все инструменты для персональных Telegram-кампаний",
        "monthly_leads": -1,
        "monthly_messages": -1,
        "monthly_ai_offers": -1,
        "max_per_launch": -1,
        "max_databases": -1,
        "max_offers": -1,
        "max_warmup": -1,
        "ai": True,
        "ai_offer": True,
        "sending": True,
        "features": [
            "Поиск и проверка лидов по YouTube",
            "Базы лидов, офферы и Telegram-рассылки",
            "Персонализация вступлений по тематике канала",
            "Статистика доставки, прочтений, ответов и интереса",
            "Тепловая карта лучшего времени отправки",
            "Центр безопасности аккаунтов и автопауза",
            "ИИ-помощник по данным рабочего пространства",
        ],
    }
    assert plan_catalog.public_plan("zephyr") == expected_public
    assert plan_catalog.all_public_plans() == [expected_public]
    copied = plan_catalog.public_plan("zephyr")["features"]
    copied.append("must not mutate catalog")
    assert plan_catalog.PLANS["zephyr"]["features"] == expected_public["features"]
    for name in CATALOG_NAMES:
        assert getattr(plans, name) is getattr(plan_catalog, name)


def test_catalog_purity_surface_and_exact_signatures() -> None:
    from app import plan_catalog

    tree = ast.parse(Path(plan_catalog.__file__).read_text(encoding="utf-8"))
    forbidden_roots = {"fastapi", "starlette", "sqlalchemy"}
    forbidden_app_segments = {
        "config",
        "database",
        "dependencies",
        "main",
        "models",
        "repositories",
        "routers",
        "schemas",
        "services",
        "settings",
    }
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            assert not {name.name.split(".")[0] for name in node.names} & forbidden_roots
        if isinstance(node, ast.ImportFrom):
            module_parts = (node.module or "").split(".")
            assert not ({part for part in module_parts if part} & forbidden_roots)
            relative_parts = (
                module_parts
                if node.level
                else module_parts[1:]
                if module_parts[:1] == ["app"]
                else []
            )
            aliases = {alias.name.split(".")[0] for alias in node.names}
            assert (
                not (set(relative_parts) | aliases if node.level else set(relative_parts))
                & forbidden_app_segments
            )

    assert tuple(plan_catalog.__all__) == CATALOG_NAMES
    assert {
        name: str(inspect.signature(getattr(plan_catalog, name))) for name in EXPECTED_SIGNATURES
    } == EXPECTED_SIGNATURES


def test_public_plans_http_response_and_openapi_snapshot(client) -> None:
    response = client.get("/api/plans")
    assert response.status_code == 200
    assert response.json() == {
        "plans": [plans.public_plan("zephyr")],
        "currencies": ["RUB", "KZT"],
        "admin_contact": "admin_user",
        "first_purchase_discount_pct": 0,
    }
    manifest = json.loads(
        (Path(__file__).parent / "contracts" / "openapi-contract.json").read_text(encoding="utf-8")
    )
    assert normalize_openapi(app.openapi()) == manifest


@pytest.mark.parametrize("module_name", PLAN_CONSUMER_MODULES)
def test_each_current_plans_module_cold_imports_in_its_own_fresh_subprocess(
    module_name: str,
) -> None:
    root = Path(__file__).resolve().parents[2]
    backend = root / "backend"
    environment = os.environ.copy()
    environment["PYTHONPATH"] = os.pathsep.join(
        filter(None, (str(backend), str(root), environment.get("PYTHONPATH", "")))
    )
    result = subprocess.run(
        [sys.executable, "-c", f"__import__({module_name!r})"],
        cwd=backend,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_seed_cold_imports_in_its_own_fresh_subprocess() -> None:
    root = Path(__file__).resolve().parents[2]
    backend = root / "backend"
    seed = backend / "scripts" / "seed.py"
    environment = os.environ.copy()
    environment["PYTHONPATH"] = os.pathsep.join(
        filter(None, (str(backend), str(root), environment.get("PYTHONPATH", "")))
    )
    command = "import runpy; " f"runpy.run_path({str(seed)!r}, run_name='scripts.seed')"
    result = subprocess.run(
        [sys.executable, "-c", command],
        cwd=backend / "scripts",
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_settings_monkeypatch_seam_remains_live(monkeypatch) -> None:
    from app.services import settings as settings_service

    monkeypatch.setattr(plans, "effective_plan_slug", lambda *_args: "zephyr")
    monkeypatch.setattr(plans, "get_plan", lambda _slug: {"monthly_messages": 777})

    class RepositoryStub:
        def __init__(self, _db: object) -> None:
            pass

        def get_settings(self, _owner_id: int) -> None:
            return None

        def stage_settings(self, _row: object) -> None:
            pass

    class DbStub:
        def commit(self) -> None:
            pass

        def refresh(self, _row: object) -> None:
            pass

    monkeypatch.setattr(settings_service, "SettingsRepository", RepositoryStub)
    assert settings_service.get_settings(DbStub(), 1).monthly_limit == 777
