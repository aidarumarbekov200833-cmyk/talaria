"""Допуск к комментированию: общий котёл, прогрев, взаимоисключение.

Тесты намеренно проверяют не «функция что-то вернула», а именно те правила,
ради которых модуль существует: комментарии и личные сообщения тратят один и
тот же лимит, а рассылка и комментинг не работают на аккаунте одновременно.
"""

from __future__ import annotations

import random
from datetime import UTC, date, datetime, timedelta

import pytest

from app.models import WarmupAccount
from app.services import neuro_budget

NOON = datetime(2026, 8, 26, 12, 0, tzinfo=UTC)


def account(**kwargs) -> WarmupAccount:
    defaults = dict(
        id=1,
        owner_id=1,
        label="Основной",
        connected_at=NOON - timedelta(days=90),
        daily_send_limit=30,
        sending_enabled=True,
        operational_status="ready",
        cooldown_until=None,
    )
    defaults.update(kwargs)
    return WarmupAccount(**defaults)


# --- прогрев ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("age_days", "slug", "max_actions"),
    [(0, "fresh", 3), (6, "fresh", 3), (7, "young", 15), (27, "young", 15), (28, "mature", 40)],
)
def test_tier_follows_account_age(age_days, slug, max_actions):
    acc = account(connected_at=NOON - timedelta(days=age_days))
    tier = neuro_budget.tier_for(acc, NOON)
    assert tier.slug == slug
    assert tier.max_actions == max_actions


def test_account_without_dates_is_treated_as_new():
    """Неизвестный возраст трактуем как «новый»: занижать лимит дешевле."""
    acc = account(connected_at=None, started_on=None)
    assert neuro_budget.tier_for(acc, NOON).slug == "fresh"


def test_started_on_counts_when_session_not_connected():
    acc = account(connected_at=None, started_on=date(2026, 5, 1))
    assert neuro_budget.tier_for(acc, NOON).slug == "mature"


# --- общий котёл -----------------------------------------------------------


def test_daily_pool_is_shared_between_dm_and_comments():
    """Главное правило фичи: 30 отправок съедают лимит комментариев целиком."""
    acc = account(daily_send_limit=30)
    decision = neuro_budget.evaluate(acc, used_today=30, run_daily_limit=40, now=NOON)
    assert not decision.allowed
    assert "лимит" in decision.reason.lower()
    assert "вместе" in decision.reason


def test_limit_is_minimum_of_tier_account_and_run():
    """Берём самый строгий из трёх лимитов, а не последний заданный."""
    acc = account(connected_at=NOON - timedelta(days=3), daily_send_limit=30)  # тир fresh = 3
    decision = neuro_budget.evaluate(acc, used_today=2, run_daily_limit=40, now=NOON)
    assert decision.allowed
    assert decision.left_today == 1

    decision = neuro_budget.evaluate(acc, used_today=3, run_daily_limit=40, now=NOON)
    assert not decision.allowed


def test_run_limit_can_be_stricter_than_tier():
    acc = account()  # mature, 40
    decision = neuro_budget.evaluate(acc, used_today=5, run_daily_limit=6, now=NOON)
    assert decision.allowed
    assert decision.left_today == 1


# --- взаимоисключение с рассылкой -----------------------------------------


def test_campaign_blocks_commenting_on_same_account():
    acc = account()
    decision = neuro_budget.evaluate(
        acc, used_today=0, run_daily_limit=10, busy_with_campaign=True, now=NOON
    )
    assert not decision.allowed
    assert "рассылк" in decision.reason


def test_campaign_accounts_parses_json_and_survives_garbage():
    assert neuro_budget.campaign_accounts("[1, 2, 3]") == {1, 2, 3}
    assert neuro_budget.campaign_accounts('["4"]') == {4}
    assert neuro_budget.campaign_accounts("не json") == set()
    assert neuro_budget.campaign_accounts(None) == set()
    assert neuro_budget.campaign_accounts('{"a": 1}') == set()


# --- состояние аккаунта ----------------------------------------------------


@pytest.mark.parametrize(
    ("field", "value", "marker"),
    [
        ("operational_status", "banned", "заблокирован"),
        ("operational_status", "reauth", "повторного входа"),
        ("sending_enabled", False, "выключен"),
    ],
)
def test_broken_account_is_refused(field, value, marker):
    decision = neuro_budget.evaluate(
        account(**{field: value}), used_today=0, run_daily_limit=10, now=NOON
    )
    assert not decision.allowed
    assert marker in decision.reason


def test_cooldown_blocks_and_reports_minutes_left():
    acc = account(cooldown_until=NOON + timedelta(minutes=30))
    decision = neuro_budget.evaluate(acc, used_today=0, run_daily_limit=10, now=NOON)
    assert not decision.allowed
    assert "31 мин" in decision.reason


def test_expired_cooldown_does_not_block():
    acc = account(cooldown_until=NOON - timedelta(minutes=1))
    assert neuro_budget.evaluate(acc, used_today=0, run_daily_limit=10, now=NOON).allowed


# --- окно активности и паузы ----------------------------------------------


def test_night_hours_are_refused():
    night = NOON.replace(hour=4)
    decision = neuro_budget.evaluate(account(), used_today=0, run_daily_limit=10, now=night)
    assert not decision.allowed
    assert "окна активности" in decision.reason


def test_active_hours_boundaries():
    assert not neuro_budget.within_active_hours(NOON.replace(hour=8))
    assert neuro_budget.within_active_hours(NOON.replace(hour=9))
    assert neuro_budget.within_active_hours(NOON.replace(hour=22))
    assert not neuro_budget.within_active_hours(NOON.replace(hour=23))


def test_pause_is_random_inside_tier_range():
    """Фиксированная пауза — машинный признак, поэтому разброс обязателен."""
    decision = neuro_budget.evaluate(account(), used_today=0, run_daily_limit=10, now=NOON)
    values = {decision.next_pause(random.Random(seed)) for seed in range(50)}
    assert len(values) > 5
    assert all(decision.delay_min <= v <= decision.delay_max for v in values)


def test_slowmode_extends_pause_with_margin():
    assert neuro_budget.slowmode_pause(0, 90) == 90
    # Слоумод длиннее обычной паузы — ждём его плюс запас, а не ровно его.
    assert neuro_budget.slowmode_pause(600, 90) > 600
