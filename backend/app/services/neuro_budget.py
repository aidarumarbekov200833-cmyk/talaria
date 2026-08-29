"""Сколько действий в Telegram аккаунту ещё можно сделать сегодня.

Ключевой факт, вокруг которого построен модуль: в Telegram **репутация одна на
аккаунт**. Личные сообщения, посты в группах и комментарии под постами тратят
один и тот же скрытый «спам-скор». Поэтому нейрокомментинг не может иметь
собственный независимый лимит: он обязан считаться вместе с рассылкой, иначе
две «безопасные по отдельности» фичи вместе выносят номер в бан.

Отсюда два правила, которые здесь и живут:

1. Общий дневной котёл: `отправки за сегодня + комментарии за сегодня`.
2. Взаимоисключение: если аккаунт прямо сейчас занят рассылкой, комментинг им
   не работает. Не «делим лимит пополам», а «в одном окне одна активность» —
   всплеск разнородных действий выглядит подозрительнее ровного потока.

Модуль намеренно не ходит в Telegram и ничего не пишет в базу: на входе объект
аккаунта и несколько чисел, на выходе — решение и причина. Так его можно
проверить тестами на всех сочетаниях, не поднимая ни базу, ни gateway.
"""

from __future__ import annotations

import json
import random
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

from ..models import WarmupAccount


@dataclass(frozen=True)
class Tier:
    """Пороги для одной ступени прогрева.

    Официальных цифр Telegram не публикует — сервер решает эвристикой (возраст
    номера, Premium, жалобы). Значения подобраны консервативно: цена занижения
    лимита — медленнее работа, цена завышения — потерянный номер.
    """

    slug: str
    title: str
    max_actions: int
    delay_min: int
    delay_max: int
    max_joins: int


# Границы по возрасту подключённого аккаунта в днях.
TIERS = (
    (7, Tier("fresh", "Новый аккаунт: прогрев", 3, 300, 900, 3)),
    (28, Tier("young", "Меньше месяца: осторожный режим", 15, 120, 360, 8)),
    (10**6, Tier("mature", "Зрелый аккаунт: рабочий режим", 40, 60, 180, 15)),
)

# Часы (по местному времени владельца), в которые вообще разрешено писать.
# Комментарий в 04:00 — сам по себе признак автоматизации.
ACTIVE_HOUR_FROM = 9
ACTIVE_HOUR_TO = 23


def account_age_days(account: WarmupAccount, now: datetime | None = None) -> int:
    """Возраст аккаунта в системе.

    Берём самую раннюю известную дату: подключение сессии или начало прогрева.
    Реальный возраст номера в Telegram нам недоступен, а завышать его опаснее,
    чем занижать, — поэтому при отсутствии обеих дат считаем аккаунт новым.
    """
    moment = now or datetime.now(UTC)
    candidates: list[datetime] = []
    connected = getattr(account, "connected_at", None)
    if connected:
        candidates.append(connected if connected.tzinfo else connected.replace(tzinfo=UTC))
    started = getattr(account, "started_on", None)
    if isinstance(started, date):
        candidates.append(datetime(started.year, started.month, started.day, tzinfo=UTC))
    if not candidates:
        return 0
    return max(0, (moment - min(candidates)).days)


def tier_for(account: WarmupAccount, now: datetime | None = None) -> Tier:
    age = account_age_days(account, now)
    for edge, tier in TIERS:
        if age < edge:
            return tier
    return TIERS[-1][1]


@dataclass(frozen=True)
class Decision:
    """Можно ли аккаунту оставить комментарий прямо сейчас."""

    allowed: bool
    reason: str = ""
    left_today: int = 0
    tier: str = ""
    delay_min: int = 60
    delay_max: int = 180

    def next_pause(self, rnd: random.Random | None = None) -> int:
        """Пауза до следующего действия — всегда со случайным разбросом.

        Ровный интервал между сообщениями — самый простой машинный признак,
        поэтому фиксированной задержки здесь нет даже как опции.
        """
        generator = rnd or random
        low, high = sorted((max(1, self.delay_min), max(1, self.delay_max)))
        return generator.randint(low, high)


def within_active_hours(
    now: datetime, hour_from: int = ACTIVE_HOUR_FROM, hour_to: int = ACTIVE_HOUR_TO
) -> bool:
    return hour_from <= now.hour < hour_to


def campaign_accounts(raw_account_ids: str | None) -> set[int]:
    """id аккаунтов из JSON-поля кампании. Мусор в поле не должен ронять воркер."""
    try:
        data = json.loads(raw_account_ids or "[]")
    except (TypeError, ValueError):
        return set()
    if not isinstance(data, list):
        return set()
    result: set[int] = set()
    for item in data:
        try:
            result.add(int(item))
        except (TypeError, ValueError):
            continue
    return result


def evaluate(
    account: WarmupAccount,
    *,
    used_today: int,
    run_daily_limit: int,
    busy_with_campaign: bool = False,
    now: datetime | None = None,
    respect_hours: bool = True,
) -> Decision:
    """Главное решение: комментировать сейчас или нет, и почему нет.

    Порядок проверок — от «уже наказан» к «просто рано»: человеку важнее увидеть
    причину блокировки, а не первую попавшуюся.
    """
    moment = now or datetime.now(UTC)
    tier = tier_for(account, moment)

    status = (getattr(account, "operational_status", "") or "ready").lower()
    if status == "banned":
        return Decision(False, "Аккаунт заблокирован в Telegram", tier=tier.slug)
    if status == "reauth":
        return Decision(False, "Сессия аккаунта требует повторного входа", tier=tier.slug)
    if not getattr(account, "sending_enabled", True):
        return Decision(False, "Аккаунт выключен владельцем", tier=tier.slug)

    cooldown = getattr(account, "cooldown_until", None)
    if cooldown:
        cooldown = cooldown if cooldown.tzinfo else cooldown.replace(tzinfo=UTC)
        if cooldown > moment:
            left = int((cooldown - moment).total_seconds())
            return Decision(
                False,
                f"Аккаунт на паузе после ошибки Telegram, осталось {left // 60 + 1} мин",
                tier=tier.slug,
            )

    # Взаимоисключение с рассылкой: не делим общий котёл, а разводим по времени.
    if busy_with_campaign:
        return Decision(
            False,
            "Аккаунт занят рассылкой — комментинг и рассылка не работают одновременно",
            tier=tier.slug,
        )

    # Общий котёл: минимум из тира прогрева, лимита аккаунта и настройки прогона.
    account_limit = int(getattr(account, "daily_send_limit", 0) or 0) or tier.max_actions
    cap = min(
        tier.max_actions, account_limit, max(0, int(run_daily_limit or 0)) or tier.max_actions
    )
    left = cap - max(0, int(used_today))
    if left <= 0:
        return Decision(
            False,
            f"Дневной лимит аккаунта исчерпан: {used_today} из {cap} "
            "(личные сообщения и комментарии считаются вместе)",
            left_today=0,
            tier=tier.slug,
        )

    if respect_hours and not within_active_hours(moment):
        return Decision(
            False,
            f"Вне окна активности {ACTIVE_HOUR_FROM}:00–{ACTIVE_HOUR_TO}:00",
            left_today=left,
            tier=tier.slug,
        )

    return Decision(
        True,
        "",
        left_today=left,
        tier=tier.slug,
        delay_min=tier.delay_min,
        delay_max=tier.delay_max,
    )


def slowmode_pause(target_slowmode: int, base_pause: int) -> int:
    """Пауза с учётом медленного режима чата.

    Ждём слоумод плюс небольшой запас: отправка ровно в момент истечения — тоже
    машинный признак, а отклонённое сообщение стоит ошибки в журнале.
    """
    if target_slowmode <= 0:
        return base_pause
    return max(base_pause, int(target_slowmode) + random.randint(5, 40))


def day_bounds(now: datetime | None = None) -> tuple[datetime, datetime]:
    """Границы текущих суток в UTC — для подсчёта общего котла."""
    moment = now or datetime.now(UTC)
    start = moment.replace(hour=0, minute=0, second=0, microsecond=0)
    return start, start + timedelta(days=1)
