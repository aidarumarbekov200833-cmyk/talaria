"""Окно отправки и показатель «когда чаще отвечают».

Логика чистая и потому проверяется на данных, а не на наблюдении в проде:
именно здесь легко получить «письма не уходят, и непонятно почему».
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.services import send_window
from app.services.errors import DomainError
from app.services.settings import update_settings


class _Lead:
    """Заглушка лида: функции окна нужна только страна."""

    def __init__(self, country: str) -> None:
        self.country = country


def test_местный_час_считается_по_стране():
    # 09:00 UTC — это полдень в Москве и 14:00 в Алматы.
    moment = datetime(2026, 8, 24, 9, 0, tzinfo=UTC)
    assert send_window.local_hour("RU", moment) == 12
    assert send_window.local_hour("KZ", moment) == 14
    assert send_window.local_hour("US", moment) == 4
    # Страна неизвестна — час не выдумываем.
    assert send_window.local_hour("", moment) is None
    assert send_window.local_hour("ZZ", moment) is None


def test_переход_через_полночь_не_ломает_час():
    """23:00 UTC + 5 часов = 4 утра следующего дня, а не 28 часов."""
    moment = datetime(2026, 8, 24, 23, 0, tzinfo=UTC)
    assert send_window.local_hour("KZ", moment) == 4
    # И в обратную сторону: 02:00 UTC в Нью-Йорке — ещё вчерашние 21:00.
    assert send_window.local_hour("US", datetime(2026, 8, 24, 2, 0, tzinfo=UTC)) == 21


def test_ночью_не_пишем_а_днём_пишем():
    night = datetime(2026, 8, 24, 23, 0, tzinfo=UTC)  # 02:00 в Москве
    day = datetime(2026, 8, 24, 9, 0, tzinfo=UTC)  # 12:00 в Москве
    assert send_window.in_window("RU", 10, 20, night) is False
    assert send_window.in_window("RU", 10, 20, day) is True
    # Неизвестная страна не задерживается: молча не отправить хуже, чем
    # отправить в неудачный час.
    assert send_window.in_window("", 10, 20, night) is True


def test_граница_окна_включает_начало_и_исключает_конец():
    """Окно «с 10 до 20» — это 10:00 включительно и 19:59 включительно."""
    ten = datetime(2026, 8, 24, 7, 0, tzinfo=UTC)  # 10:00 МСК
    twenty = datetime(2026, 8, 24, 17, 0, tzinfo=UTC)  # 20:00 МСК
    assert send_window.in_window("RU", 10, 20, ten) is True
    assert send_window.in_window("RU", 10, 20, twenty) is False


def test_разделение_получателей_ничего_не_теряет():
    night_moment = datetime(2026, 8, 24, 23, 0, tzinfo=UTC)
    leads = [_Lead("RU"), _Lead("US"), _Lead(""), _Lead("KZ")]
    ready, waiting = send_window.split_by_window(leads, 10, 20, night_moment)
    assert len(ready) + len(waiting) == len(leads)
    # В США 19:00 — ещё можно; в России 02:00 и в Казахстане 04:00 — нет.
    assert {lead.country for lead in ready} == {"US", ""}
    assert {lead.country for lead in waiting} == {"RU", "KZ"}


def test_мусорное_окно_заменяется_умолчанием():
    assert send_window.normalize_window(10, 20) == (10, 20)
    assert send_window.normalize_window(None, None) == (10, 20)
    assert send_window.normalize_window(25, 30) == (10, 20)
    # Окно через полночь не поддерживаем осознанно — это ровно то, от чего
    # окно защищает.
    assert send_window.normalize_window(22, 6) == (10, 20)
    assert send_window.normalize_window(12, 12) == (10, 20)


def test_настройка_окна_проверяет_границы_парой(db, user):
    update_settings(db, user.id, send_window_start=9, send_window_end=18, user=user)
    saved = update_settings(db, user.id, send_window_enabled=False, user=user)
    assert saved.send_window_start == 9
    assert saved.send_window_end == 18
    assert saved.send_window_enabled is False

    # Отдельное поле проверяется против сохранённого второго, а не против
    # умолчания: иначе настройка молча откатывалась бы.
    with pytest.raises(DomainError) as err:
        update_settings(db, user.id, send_window_start=20, user=user)
    assert err.value.status_code == 400
    assert update_settings(db, user.id, user=user).send_window_start == 9


def test_показатель_молчит_пока_данных_мало():
    moment = datetime(2026, 8, 24, 9, 0, tzinfo=UTC)
    result = send_window.reply_hours([("RU", moment, moment)] * 3)
    assert result["enough_data"] is False
    assert result["best_hours"] == []
    assert "мало" in result["note"]

    # Совсем пусто — отдельная формулировка, а не «мало данных».
    empty = send_window.reply_hours([])
    assert empty["total_sent"] == 0
    assert "не из чего считать" in empty["note"]


def test_показатель_находит_лучшие_часы():
    day = datetime(2026, 8, 24, 9, 0, tzinfo=UTC)  # 12:00 МСК
    night = datetime(2026, 8, 24, 23, 0, tzinfo=UTC)  # 02:00 МСК
    rows = [("RU", day, day) for _ in range(15)]  # 15 писем в полдень, все с ответом
    rows += [("RU", night, None) for _ in range(15)]  # 15 ночью, без ответов

    result = send_window.reply_hours(rows)
    assert result["enough_data"] is True
    assert result["best_hours"][0] == 12
    assert result["total_sent"] == 30
    assert result["total_replied"] == 15
    noon = next(h for h in result["hours"] if h["hour"] == 12)
    assert noon["rate"] == 100.0
    two_am = next(h for h in result["hours"] if h["hour"] == 2)
    assert two_am["rate"] == 0.0


def test_неизвестная_страна_не_попадает_в_часы_но_входит_в_итог():
    """Иначе «всего писем» в показателе разошлось бы с реальностью."""
    moment = datetime(2026, 8, 24, 9, 0, tzinfo=UTC)
    result = send_window.reply_hours([("", moment, moment), ("RU", moment, None)])
    assert result["total_sent"] == 2
    assert result["total_replied"] == 1
    assert sum(h["sent"] for h in result["hours"]) == 1
