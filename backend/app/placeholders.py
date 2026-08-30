"""Плейсхолдеры для текста оффера.

Источников данных два, и они разные:

* **YouTube** — всё, что относится к каналу (название, подписчики, страна…).
  Имени человека там нет — только название канала.
* **Telegram** — имя, фамилия и @username получателя. Именно туда уходит
  сообщение, поэтому {first_name}/{last_name} берём оттуда (поля
  `Lead.tg_first_name` / `Lead.tg_last_name`, заполняются MTProto-клиентом).

Если профиль Telegram ещё не подтянут, имя не выдумываем: такой токен
попадает в `unresolved`, и интерфейс честно предупреждает об этом до отправки.
"""

from __future__ import annotations

import re

from .countries import country_name
from .models import Lead

TOKEN_RE = re.compile(r"\{([a-z_]+)\}")

PLACEHOLDERS: list[dict] = [
    {
        "token": "{channel}",
        "title": "\u041d\u0430\u0437\u0432\u0430\u043d\u0438\u0435 \u043a\u0430\u043d\u0430\u043b\u0430",
        "field": "title",
        "example": "\u041a\u0430\u043d\u0430\u043b \u043e \u043c\u0430\u0440\u043a\u0435\u0442\u0438\u043d\u0433\u0435",
        "available": True,
        "note": "",
        "source": "youtube",
    },
    {
        "token": "{first_name}",
        "title": "\u0418\u043c\u044f",
        "field": "tg_first_name",
        "example": "\u0410\u043b\u0438\u0448\u0435\u0440",
        "available": True,
        "source": "telegram",
        "note": "\u0418\u0437 \u043f\u0440\u043e\u0444\u0438\u043b\u044f Telegram \u043f\u043e\u043b\u0443\u0447\u0430\u0442\u0435\u043b\u044f. \u0415\u0441\u043b\u0438 \u043f\u0440\u043e\u0444\u0438\u043b\u044c \u0435\u0449\u0451 \u043d\u0435 \u043f\u043e\u0434\u0442\u044f\u043d\u0443\u0442, "
        "\u043d\u0430\u0436\u043c\u0438\u0442\u0435 \u00ab\u041f\u043e\u0434\u0442\u044f\u043d\u0443\u0442\u044c \u0438\u0437 Telegram\u00bb \u0432 \u0431\u0430\u0437\u0435 \u043b\u0438\u0434\u043e\u0432.",
    },
    {
        "token": "{last_name}",
        "title": "\u0424\u0430\u043c\u0438\u043b\u0438\u044f",
        "field": "tg_last_name",
        "example": "\u041d\u0443\u0440\u043b\u0430\u043d\u043e\u0432",
        "available": True,
        "source": "telegram",
        "note": "\u0418\u0437 \u043f\u0440\u043e\u0444\u0438\u043b\u044f Telegram. \u0423 \u043c\u043d\u043e\u0433\u0438\u0445 \u0444\u0430\u043c\u0438\u043b\u0438\u044f \u043d\u0435 \u0443\u043a\u0430\u0437\u0430\u043d\u0430 \u2014 \u0442\u043e\u0433\u0434\u0430 \u043f\u043e\u0434\u0441\u0442\u0430\u0432\u0438\u0442\u0441\u044f \u043f\u0443\u0441\u0442\u043e\u0435 \u043c\u0435\u0441\u0442\u043e.",
    },
    {
        "token": "{username}",
        "title": "@username \u0432 Telegram",
        "field": "tg_username",
        "example": "@alisher",
        "available": True,
        "source": "telegram",
        "note": "\u0418\u0437 \u043f\u0440\u043e\u0444\u0438\u043b\u044f Telegram \u043f\u043e\u043b\u0443\u0447\u0430\u0442\u0435\u043b\u044f.",
    },
    {
        "token": "{subscribers}",
        "title": "\u041f\u043e\u0434\u043f\u0438\u0441\u0447\u0438\u043a\u0438",
        "field": "subscribers",
        "example": "12 400",
        "available": True,
        "source": "youtube",
        "note": "\u0415\u0441\u043b\u0438 \u043a\u0430\u043d\u0430\u043b \u0441\u043a\u0440\u044b\u043b \u0441\u0447\u0451\u0442\u0447\u0438\u043a, \u043f\u043e\u0434\u0441\u0442\u0430\u0432\u0438\u0442\u0441\u044f \u00ab\u2014\u00bb.",
    },
    {
        "token": "{videos}",
        "title": "\u0427\u0438\u0441\u043b\u043e \u0432\u0438\u0434\u0435\u043e",
        "field": "video_count",
        "example": "87",
        "available": True,
        "note": "",
        "source": "youtube",
    },
    {
        "token": "{views}",
        "title": "\u041f\u0440\u043e\u0441\u043c\u043e\u0442\u0440\u044b \u043a\u0430\u043d\u0430\u043b\u0430",
        "field": "view_count",
        "example": "1 240 000",
        "available": True,
        "note": "",
        "source": "youtube",
    },
    {
        "token": "{country}",
        "title": "\u0421\u0442\u0440\u0430\u043d\u0430",
        "field": "country",
        "example": "\u041a\u0430\u0437\u0430\u0445\u0441\u0442\u0430\u043d",
        "available": True,
        "source": "youtube",
        "note": "\u041a\u0430\u043d\u0430\u043b \u0443\u043a\u0430\u0437\u044b\u0432\u0430\u0435\u0442 \u0441\u0442\u0440\u0430\u043d\u0443 \u043d\u0435 \u0432\u0441\u0435\u0433\u0434\u0430.",
    },
    {
        "token": "{link}",
        "title": "\u0421\u0441\u044b\u043b\u043a\u0430 \u043d\u0430 \u043a\u0430\u043d\u0430\u043b",
        "field": "link",
        "example": "https://youtube.com/@example",
        "available": True,
        "note": "",
        "source": "youtube",
    },
]

AVAILABLE_TOKENS = {p["token"] for p in PLACEHOLDERS if p["available"]}

# Токены, значение которых известно только после того, как подтянут профиль
# Telegram. До этого момента они считаются неразрешёнными — отправить «Привет, !»
# хуже, чем увидеть предупреждение в предпросмотре.
TELEGRAM_TOKENS = {p["token"] for p in PLACEHOLDERS if p.get("source") == "telegram"}


def _group(value: int) -> str:
    """12400 -> «12 400» (узкий пробел, как в остальном интерфейсе)."""
    return f"{value:,}".replace(",", "\u2009")


def channel_link(lead: Lead) -> str:
    if lead.custom_url:
        handle = lead.custom_url if lead.custom_url.startswith("@") else f"@{lead.custom_url}"
        return f"https://www.youtube.com/{handle}"
    return f"https://www.youtube.com/channel/{lead.channel_id}"


def telegram_ready(lead: Lead | None) -> bool:
    """Есть ли у лида подтянутое имя из Telegram."""
    return bool(lead and (lead.tg_first_name or lead.tg_username))


def values_for(lead: Lead) -> dict[str, str]:
    """Настоящие значения лида. Неизвестное — «—», а не ноль и не пустота.

    Исключение — токены из Telegram: их вообще нет в словаре, пока профиль
    не подтянут, чтобы `render` оставил токен видимым, а `unresolved_tokens`
    показал его в предупреждении.
    """
    values = {
        "{channel}": lead.title or "\u2014",
        "{subscribers}": _group(lead.subscribers) if lead.subscribers is not None else "\u2014",
        "{videos}": _group(lead.video_count) if lead.video_count is not None else "\u2014",
        "{views}": _group(lead.view_count) if lead.view_count is not None else "\u2014",
        # Страна словами: в письме «KZ» выглядит как служебный код.
        "{country}": country_name(lead.country) or "\u2014",
        "{link}": channel_link(lead),
        # Из списка подсказок {keyword} убран (в письме он выглядел как
        # служебная пометка), но подстановка осталась: в старых офферах
        # токен уже мог быть сохранён.
        "{keyword}": lead.matched_keyword or "\u2014",
    }
    if lead.tg_first_name:
        values["{first_name}"] = lead.tg_first_name
    if lead.tg_first_name or lead.tg_last_name:
        # Фамилия часто не указана, но если имя уже есть — профиль прочитан,
        # и пустая фамилия это факт, а не пробел в данных.
        values["{last_name}"] = lead.tg_last_name or ""
    if lead.tg_username:
        handle = lead.tg_username.lstrip("@")
        values["{username}"] = f"@{handle}"
    return values


def render(text: str, lead: Lead | None) -> str:
    """Подставить значения. Без лида текст возвращается как есть."""
    if lead is None:
        return text
    out = text
    for token, value in values_for(lead).items():
        out = out.replace(token, value)
    # Двойные пробелы после пустой фамилии выглядят как опечатка в письме.
    out = re.sub(r"[ \t]{2,}", " ", out)
    return re.sub(r" +([,.!?:;])", r"\1", out)


def unresolved_tokens(text: str, lead: Lead | None = None) -> list[str]:
    """Плейсхолдеры в тексте, которые мы подставить не можем.

    Сюда попадают опечатки (`{chanel}`) и telegram-токены у лида, чей профиль
    ещё не подтянут — в обоих случаях это надо увидеть до отправки.
    """
    known = AVAILABLE_TOKENS
    if lead is not None:
        known = set(values_for(lead))
    out: list[str] = []
    for match in TOKEN_RE.finditer(text or ""):
        token = f"{{{match.group(1)}}}"
        if token not in known and token not in out:
            out.append(token)
    return out
