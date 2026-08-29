"""Окно отправки: не писать людям ночью по их местному времени.

Зачем вообще. Сообщение в три часа ночи получает два ответа: тишину и жалобу.
Второе стоит аккаунта, с которого шла рассылка, поэтому окно — не украшение,
а защита отправителя.

Откуда берётся местное время. YouTube отдаёт у канала страну, и это всё, что о
географии лида известно. Точную зону из страны не вывести: в России их
одиннадцать, в США шесть. Поэтому берём смещение самой населённой зоны страны и
честно называем это приблизительным. Ошибка в пару часов внутри рабочего дня
безобидна, а разницу между полуднем и полуночью такой точности хватает поймать.

Страну не знаем — не задерживаем. Молча не отправить письмо хуже, чем отправить
его в неудачный час: человек ждёт рассылку, а она бы просто не ушла.
"""

from __future__ import annotations

from datetime import UTC, datetime

# Смещение от UTC в часах для самой населённой зоны страны. Дробные зоны
# (Индия, Иран) округлены к ближайшему часу — на выбор «день или ночь» это
# не влияет.
COUNTRY_OFFSETS: dict[str, int] = {
    "RU": 3,  # Москва: там живёт большая часть аудитории рунета
    "UA": 2,
    "BY": 3,
    "KZ": 5,
    "UZ": 5,
    "KG": 6,
    "AZ": 4,
    "AM": 4,
    "GE": 4,
    "MD": 2,
    "TJ": 5,
    "TM": 5,
    "US": -5,  # восточное побережье
    "CA": -5,
    "MX": -6,
    "BR": -3,
    "AR": -3,
    "GB": 0,
    "IE": 0,
    "PT": 0,
    "DE": 1,
    "FR": 1,
    "ES": 1,
    "IT": 1,
    "NL": 1,
    "BE": 1,
    "AT": 1,
    "CH": 1,
    "SE": 1,
    "NO": 1,
    "DK": 1,
    "PL": 1,
    "CZ": 1,
    "HU": 1,
    "RS": 1,
    "HR": 1,
    "SK": 1,
    "SI": 1,
    "FI": 2,
    "EE": 2,
    "LV": 2,
    "LT": 2,
    "GR": 2,
    "RO": 2,
    "BG": 2,
    "IL": 2,
    "TR": 3,
    "SA": 3,
    "AE": 4,
    "IN": 5,
    "PK": 5,
    "BD": 6,
    "TH": 7,
    "VN": 7,
    "ID": 7,
    "CN": 8,
    "SG": 8,
    "MY": 8,
    "PH": 8,
    "KR": 9,
    "JP": 9,
    "AU": 10,
    "NZ": 12,
}

DEFAULT_START = 10
DEFAULT_END = 20


def normalize_window(start: int | None, end: int | None) -> tuple[int, int]:
    """Привести окно к рабочему виду. Мусор заменяется значениями по умолчанию.

    Окно через полночь (с 22 до 6) не поддерживаем осознанно: холодная рассылка
    ночью — это ровно то, от чего окно и защищает, а «поддержать на всякий
    случай» здесь означает разрешить выстрел себе в ногу.
    """
    try:
        left = int(start)
        right = int(end)
    except (TypeError, ValueError):
        return DEFAULT_START, DEFAULT_END
    if not (0 <= left <= 23) or not (0 <= right <= 23) or left >= right:
        return DEFAULT_START, DEFAULT_END
    return left, right


def local_hour(country: str | None, moment: datetime | None = None) -> int | None:
    """Час у лида по его стране. None — страна неизвестна или не в списке."""
    code = (country or "").strip().upper()
    offset = COUNTRY_OFFSETS.get(code)
    if offset is None:
        return None
    now = moment or datetime.now(UTC)
    if now.tzinfo is None:
        now = now.replace(tzinfo=UTC)
    return (now.hour + offset) % 24


def in_window(
    country: str | None,
    start: int,
    end: int,
    moment: datetime | None = None,
) -> bool:
    """Можно ли писать этому лиду прямо сейчас."""
    hour = local_hour(country, moment)
    if hour is None:
        return True
    return start <= hour < end


def split_by_window(
    leads: list,
    start: int,
    end: int,
    moment: datetime | None = None,
) -> tuple[list, list]:
    """Разделить получателей на «можно сейчас» и «сейчас ночь».

    Возвращает (готовые, отложенные). Отложенные не теряются: они остаются в
    базе без отметки об отправке, и следующий запуск возьмёт их снова.
    """
    now = moment or datetime.now(UTC)
    ready: list = []
    waiting: list = []
    for lead in leads:
        (ready if in_window(getattr(lead, "country", ""), start, end, now) else waiting).append(
            lead
        )
    return ready, waiting


# Меньше этого числа отправок показатель не считается репрезентативным.
# Двадцать писем — не статистика, но уже и не случайность; ниже порога
# интерфейс честно говорит «данных мало» вместо выдуманного «лучший час».
MIN_SAMPLE = 20


def reply_hours(rows: list[tuple[str | None, object, object]]) -> dict:
    """Разложить отправки по местному часу получателя и посчитать долю ответов.

    На вход — тройки (страна, момент отправки, момент ответа или None). Функция
    чистая: работает со списком, а не с базой, поэтому её поведение на редких
    данных проверяется тестом, а не наблюдением в проде.
    """
    buckets: dict[int, dict[str, int]] = {hour: {"sent": 0, "replied": 0} for hour in range(24)}
    total_sent = 0
    total_replied = 0
    counted = 0

    for country, sent_at, replied_at in rows:
        if sent_at is None:
            continue
        hour = local_hour(country, sent_at)
        if hour is None:
            # Страна неизвестна — час выдумывать нельзя, но в общий счёт
            # отправка входит: иначе «всего писем» разойдётся с реальностью.
            total_sent += 1
            if replied_at is not None:
                total_replied += 1
            continue
        buckets[hour]["sent"] += 1
        total_sent += 1
        counted += 1
        if replied_at is not None:
            buckets[hour]["replied"] += 1
            total_replied += 1

    hours = [
        {
            "hour": hour,
            "sent": data["sent"],
            "replied": data["replied"],
            "rate": round(data["replied"] / data["sent"] * 100, 1) if data["sent"] else 0.0,
        }
        for hour, data in sorted(buckets.items())
    ]

    enough = counted >= MIN_SAMPLE and total_replied > 0
    best: list[int] = []
    if enough:
        # Лучшими считаем часы с максимальной долей ответов среди тех, где
        # писем хватает на осмысленную долю. Иначе один ответ на одно письмо
        # дал бы «100% в 4 утра».
        floor = max(3, counted // 24)
        candidates = [h for h in hours if h["sent"] >= floor and h["replied"]]
        candidates.sort(key=lambda h: (-h["rate"], h["hour"]))
        best = [h["hour"] for h in candidates[:3]]

    if not counted:
        note = "Пока не из чего считать: отправок с известной страной получателя нет."
    elif not enough:
        note = (
            f"Данных мало: {counted} писем с известной страной и {total_replied} ответов. "
            "Показатель станет полезным после нескольких десятков отправок."
        )
    else:
        listed = ", ".join(f"{hour}:00" for hour in best)
        note = f"Чаще всего отвечают в {listed} по местному времени получателя."

    return {
        "hours": hours,
        "total_sent": total_sent,
        "total_replied": total_replied,
        "enough_data": enough,
        "best_hours": best,
        "note": note,
    }
