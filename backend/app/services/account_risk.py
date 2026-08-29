"""Насколько аккаунт близок к блокировке — понятным языком.

Данные о флудах, паузах и ошибках в системе были и раньше, но лежали россыпью:
код ошибки в одном месте, дата паузы в другом, счётчик риска в третьем. Человек,
который просто рассылает офферы, не обязан складывать это в голове — а цена
ошибки здесь потеря номера.

Модуль ничего не запрашивает и ничего не пишет: на входе объект аккаунта и
несколько чисел, на выходе — уровень, причины и что делать. Так его можно
проверить тестами на всех сочетаниях, не поднимая ни базу, ни Telegram.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

from ..models import WarmupAccount

# Уровни по возрастанию тревоги. Порядок используется при сортировке списка:
# сначала то, с чем надо что-то делать.
LEVELS = ("blocked", "danger", "watch", "ok")

# Ошибки, после которых аккаунт уже наказан, а не «может быть наказан».
HARD_CODES = {"PHONE_NUMBER_BANNED", "USER_DEACTIVATED_BAN"}
# Ограничение на переписку с незнакомыми — прямой предвестник бана.
SPAM_CODES = {"PEER_FLOOD", "FLOOD_PREMIUM_WAIT"}

# Сколько подряд ошибок считать тревожным. Одна-две — обычное дело (закрытые
# личные сообщения у получателя), три подряд означают, что дело в аккаунте.
ERRORS_WATCH = 3
ERRORS_DANGER = 6

# Возраст аккаунта, до которого рассылка особенно рискованна. Telegram строже
# всего смотрит на новые номера, которые сразу начинают писать незнакомым.
YOUNG_DAYS = 14
# Дневной объём, безопасный для молодого аккаунта.
YOUNG_SAFE_LIMIT = 20

# Доля дневного лимита, после которой стоит притормозить.
NEAR_LIMIT_RATIO = 0.9


@dataclass
class Risk:
    """Вердикт по одному аккаунту."""

    account_id: int
    label: str
    level: str = "ok"
    title: str = ""
    reasons: list[str] = field(default_factory=list)
    advice: list[str] = field(default_factory=list)
    # Когда снимется пауза, если она есть. Пустое поле означает «не на паузе»,
    # а не «неизвестно»: неопределённость здесь хуже отсутствия.
    cooldown_until: datetime | None = None
    can_send: bool = True

    @property
    def order(self) -> int:
        return LEVELS.index(self.level) if self.level in LEVELS else len(LEVELS)


TITLES = {
    "blocked": "Аккаунт заблокирован",
    "danger": "Близко к блокировке",
    "watch": "Стоит быть осторожнее",
    "ok": "Всё в порядке",
}


def _aware(value: datetime | None) -> datetime | None:
    """SQLite отдаёт время без пояса — сравнение с now() иначе падает."""
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def _age_days(account: WarmupAccount, now: datetime) -> int | None:
    started = _aware(account.connected_at)
    if started is None and account.started_on is not None:
        started = datetime.combine(account.started_on, datetime.min.time(), tzinfo=UTC)
    if started is None:
        return None
    return max(0, (now - started).days)


def assess(
    account: WarmupAccount,
    *,
    sent_today: int = 0,
    now: datetime | None = None,
) -> Risk:
    """Собрать вердикт по одному аккаунту.

    Уровень определяется худшим найденным признаком, но причины показываем все:
    человеку важно не только «плохо», а что именно к этому привело.
    """
    now = now or datetime.now(UTC)
    risk = Risk(account_id=account.id, label=account.label or f"Аккаунт {account.id}")
    reasons: list[str] = []
    advice: list[str] = []
    level = "ok"

    def raise_to(new_level: str) -> None:
        nonlocal level
        if LEVELS.index(new_level) < LEVELS.index(level):
            level = new_level

    code = (account.last_tg_error_code or "").upper()
    status = (account.operational_status or "ready").lower()

    if status == "banned" or code in HARD_CODES:
        raise_to("blocked")
        reasons.append("Telegram заблокировал этот номер")
        advice.append(
            "Напишите в @SpamBot и отправьте апелляцию. Рассылку с этого аккаунта не запускайте."
        )
    elif status == "reauth":
        raise_to("danger")
        reasons.append("Telegram разлогинил сессию")
        advice.append("Подключите аккаунт заново в настройках — до этого отправка не работает.")

    if (account.spambot_status or "") in {"limited", "appeal_available"}:
        raise_to("danger")
        reasons.append("@SpamBot сообщает об ограничении на сообщения незнакомым")
        advice.append("Отправьте апелляцию через @SpamBot и сделайте паузу на несколько дней.")

    if code in SPAM_CODES:
        raise_to("danger")
        reasons.append("Telegram ограничил переписку с незнакомыми (PEER_FLOOD)")
        advice.append(
            "Остановите рассылку минимум на сутки. Дальше начинайте с 5–10 сообщений в день."
        )
    elif code.startswith("FLOOD_WAIT"):
        raise_to("watch")
        reasons.append("Были ответы «слишком часто» — темп выше, чем нравится Telegram")
        advice.append("Увеличьте паузу между сообщениями в настройках аккаунта.")

    cooldown = _aware(account.cooldown_until)
    if cooldown and cooldown > now:
        raise_to("watch")
        hours = max(1, int((cooldown - now).total_seconds() // 3600))
        reasons.append(f"Аккаунт на паузе ещё примерно {hours} ч")
        risk.cooldown_until = cooldown

    errors = int(account.consecutive_errors or 0)
    if errors >= ERRORS_DANGER:
        raise_to("danger")
        reasons.append(f"Подряд {errors} ошибок отправки")
        advice.append("Проверьте аккаунт через @SpamBot, прежде чем продолжать.")
    elif errors >= ERRORS_WATCH:
        raise_to("watch")
        reasons.append(f"Подряд {errors} ошибок отправки")

    age = _age_days(account, now)
    limit = int(account.daily_send_limit or 0)
    if age is not None and age < YOUNG_DAYS and limit > YOUNG_SAFE_LIMIT:
        raise_to("watch")
        reasons.append(f"Аккаунту {age} дн., а дневной лимит {limit} сообщений")
        advice.append(
            f"Для аккаунта младше {YOUNG_DAYS} дней безопаснее не больше "
            f"{YOUNG_SAFE_LIMIT} сообщений в день."
        )

    if limit and sent_today >= limit * NEAR_LIMIT_RATIO:
        raise_to("watch")
        reasons.append(f"Сегодня отправлено {sent_today} из {limit}")
        advice.append("Остаток дня лучше не догонять — перенесите на завтра.")

    growth = int(account.unread_growth or 0)
    if growth >= 20:
        raise_to("watch")
        reasons.append(f"После рассылки резко выросли непрочитанные: +{growth}")
        advice.append("Загляните в Telegram: среди них могут быть жалобы или ответы.")

    risk.level = level
    risk.title = TITLES[level]
    risk.reasons = reasons
    # Совет нужен даже когда всё хорошо: пустая карточка выглядит как ошибка
    # загрузки, а не как «беспокоиться не о чем».
    risk.advice = advice or (
        ["Продолжайте в том же темпе — признаков риска нет."] if level == "ok" else []
    )
    risk.can_send = level != "blocked" and status != "reauth"
    if not reasons:
        risk.reasons = ["Ошибок и ограничений за последнее время не было"]
    return risk


def worst(items: list[Risk]) -> str:
    """Общий уровень по всем аккаунтам — по худшему из них.

    Усреднять нельзя: один заблокированный номер среди пяти здоровых — это
    проблема, а не «в основном всё хорошо».
    """
    if not items:
        return "ok"
    return min(items, key=lambda item: item.order).level


def sort_key(item: Risk) -> tuple[int, int]:
    """Сначала то, с чем надо что-то делать."""
    return (item.order, item.account_id)
