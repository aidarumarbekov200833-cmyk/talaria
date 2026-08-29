"""Кого будить, а кого нет: политика уведомлений об ошибках.

Раньше в Telegram улетала каждая 5xx и каждое необработанное исключение. На
практике это означало поток сообщений вида «Необработанное исключение, тип
KeyError, след (конец): ...» — по такому сообщению владелец сайта не может ни
понять, где сломалось, ни что-то починить. Полезные алерты тонули в шуме.

Правило владельца, дословно: сообщение в Telegram приходит, только если понятно,
**где** ошибка, или известно, **что делать**, и при этом она **мешает работе
сайта**. Всё остальное просто лежит в логах.

Отсюда два независимых признака у каждого правила:

* ``where``/``fix`` — можем ли мы назвать место и решение человеческими словами;
* ``impacting``     — ломает ли это то, чем люди пользуются.

Уведомление уходит только когда есть и то, и другое. Ошибка, которая понятна, но
ни на что не влияет (например, необязательная дозагрузка контактов), остаётся в
логах. Ошибка, которая всё сломала, но непонятна, — тоже: будить человека
сообщением, с которым он ничего не сделает, вредно. Такие случаи видно в логах,
и правило под них добавляется сюда осознанно, после первого разбора.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


# Кому адресована поломка.
#   OWNER  — сломалось у нас: база, диск, сервис. Чинит владелец сервиса.
#   CLIENT — сломалось у конкретного пользователя: ЕГО ключ YouTube, ЕГО аккаунт
#            Telegram. Владельцу сервиса такое сообщение бесполезно — он не
#            может ни добавить чужой ключ, ни переподключить чужой аккаунт.
#            Писать надо тому, у кого сломалось.
#   NOBODY — в логи.
OWNER = "owner"
CLIENT = "client"
NOBODY = "nobody"


@dataclass(frozen=True)
class Verdict:
    """Решение по одной ошибке."""

    notify: bool
    reason: str
    where: str | None = None
    fix: str | None = None
    audience: str = NOBODY


@dataclass(frozen=True)
class Rule:
    """Одно распознаваемое падение.

    ``patterns`` сверяются с уже собранной подписью ошибки в нижнем регистре:
    модуль, этап, тип исключения, текст и описание в одной строке. Достаточно
    совпадения любого из шаблонов.
    """

    name: str
    patterns: tuple[str, ...]
    where: str
    fix: str
    impacting: bool
    audience: str


# Порядок важен: первое совпавшее правило и решает.
RULES: tuple[Rule, ...] = (
    Rule(
        name="db-down",
        patterns=(
            r"could not connect to server",
            r"connection refused",
            r"operationalerror",
            r"server closed the connection unexpectedly",
            r"too many connections",
        ),
        where="База данных (контейнер zapp-db-1)",
        fix=(
            "Зайдите на сервер и посмотрите состояние: `docker compose ps`. "
            "Если база не поднята — `docker compose up -d db`. "
            "Пока база лежит, сайт не работает целиком."
        ),
        impacting=True,
        audience=OWNER,
    ),
    Rule(
        name="disk-full",
        patterns=(r"no space left on device", r"disk quota exceeded"),
        where="Диск сервера",
        fix=(
            "Место кончилось: `df -h`. Освободите его "
            "(`docker system prune -a`) — иначе перестанут писаться и бэкапы."
        ),
        impacting=True,
        audience=OWNER,
    ),
    Rule(
        name="youtube-quota",
        patterns=(r"quotaexceeded", r"dailylimitexceeded", r"quota exceeded"),
        where="Ключ YouTube API",
        fix=(
            "Дневная квота ключа исчерпана. Добавьте второй ключ в Настройках → "
            "Ключи YouTube API, либо дождитесь сброса квоты (в 10:00 по Москве). "
            "Поиск лидов до этого работать не будет."
        ),
        impacting=True,
        audience=CLIENT,
    ),
    Rule(
        name="youtube-key-invalid",
        patterns=(r"api key not valid", r"keyinvalid", r"api_key_invalid"),
        where="Ключ YouTube API",
        fix="Ключ недействителен. Замените его в Настройках → Ключи YouTube API.",
        impacting=True,
        audience=CLIENT,
    ),
    Rule(
        name="telegram-session-dead",
        patterns=(
            r"authkeyunregistered",
            r"sessionrevoked",
            r"sessionexpired",
            r"auth_key_unregistered",
            r"user_deactivated",
        ),
        where="Подключённый аккаунт Telegram",
        fix=(
            "Сессия аккаунта слетела — Telegram разлогинил его. "
            "Переподключите аккаунт в Настройках → Аккаунты Telegram. "
            "Рассылка с этого аккаунта стоит."
        ),
        impacting=True,
        audience=CLIENT,
    ),
    Rule(
        name="telegram-flood",
        patterns=(r"floodwait", r"flood_wait", r"peerflood", r"too many requests"),
        where="Ограничение Telegram на отправку",
        fix=(
            "Telegram временно придержал аккаунт за темп отправки. Это лечится "
            "паузой: снизьте скорость в настройках рассылки. Аккаунт не забанен."
        ),
        impacting=True,
        audience=CLIENT,
    ),
    Rule(
        name="sender-unreachable",
        patterns=(r"web-tg", r"tg-mini-client", r"connecterror", r"connect call failed"),
        where="Сервис отправки (контейнер zapp-web-tg-1)",
        fix=(
            "Сервис отправки не отвечает: `docker compose ps`, при необходимости "
            "`docker compose up -d web-tg`. Сообщения не уходят."
        ),
        impacting=True,
        audience=OWNER,
    ),
    Rule(
        name="ai-provider-auth",
        patterns=(
            r"insufficient.quota",
            r"insufficient balance",
            r"invalid api key",
            r"incorrect api key",
            r"unauthorized.*(openai|anthropic|openrouter)",
        ),
        where="Ключ провайдера ИИ",
        fix=(
            "Провайдер отказал: кончился баланс или ключ недействителен. "
            "Проверьте ключ и баланс в Настройках → ИИ. "
            "Генерация персональных вступлений не работает."
        ),
        impacting=True,
        audience=OWNER,
    ),
    # Понятно и место, и причина — но на работу сайта не влияет: лиды сохраняются,
    # просто без Telegram-контакта. В логи, без сообщения.
    Rule(
        name="contacts-optional",
        patterns=(r"\bcontacts\b",),
        where="Дозагрузка Telegram-контактов",
        fix="Необязательный этап: лиды сохранены, у части не найден Telegram.",
        impacting=False,
        audience=NOBODY,
    ),
)


# Обрывы соединения с клиентом — это не поломка сервиса: человек закрыл вкладку
# или у него моргнула сеть. В Telegram такое не отправляем никогда.
CLIENT_NOISE = (
    r"clientdisconnect",
    r"connectionreseterror",
    r"brokenpipeerror",
    r"cancellederror",
    r"timeouterror.*client",
    r"response ended prematurely",
)


def build_signature(
    module: str,
    stage: str,
    description: str,
    exc: BaseException | None = None,
    entity: str | None = None,
) -> str:
    """Собрать одну строку, по которой сверяются правила."""
    parts = [module or "", stage or "", description or "", entity or ""]
    if exc is not None:
        parts.append(type(exc).__name__)
        try:
            parts.append(str(exc))
        except Exception:  # noqa: BLE001 - строковое представление не должно ронять алерт
            pass
    return " | ".join(p for p in parts if p).lower()


def classify(
    module: str,
    stage: str,
    description: str,
    exc: BaseException | None = None,
    entity: str | None = None,
) -> Verdict:
    """Решить, будить ли владельца этой ошибкой."""
    signature = build_signature(module, stage, description, exc, entity)

    for pattern in CLIENT_NOISE:
        if re.search(pattern, signature):
            return Verdict(
                notify=False,
                reason="обрыв соединения с клиентом, сервис работает",
            )

    for rule in RULES:
        for pattern in rule.patterns:
            if re.search(pattern, signature):
                if not rule.impacting or rule.audience == NOBODY:
                    return Verdict(
                        notify=False,
                        reason=f"{rule.name}: понятно, но на работу сайта не влияет",
                        where=rule.where,
                        fix=rule.fix,
                        audience=NOBODY,
                    )
                return Verdict(
                    notify=True,
                    reason=rule.name,
                    where=rule.where,
                    fix=rule.fix,
                    audience=rule.audience,
                )

    return Verdict(
        notify=False,
        reason="причина не распознана — без места и решения сообщение бесполезно",
    )
