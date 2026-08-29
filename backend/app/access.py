"""Кто может войти, журнал попыток и защита от регистрации пачками.

Вход открыт: любой честно подписанный Telegram-вход создаёт аккаунт с пробным
тарифом (settings.open_registration). Белый список (models.AccessWhitelist) при
этом остаётся — это способ выдать доступ вручную, и единственный способ войти,
если открытую регистрацию выключить.
Здесь же пишется журнал попыток (models.LoginAttempt) — он нужен, чтобы видеть,
кто стучится в дверь, и ловить мультиаккаунты по совпадению IP и устройства.

Админ (settings.admin_username) проходит всегда: иначе, начав с пустой базы,
некому было бы наполнить белый список.
"""

from __future__ import annotations

import logging
from contextvars import ContextVar
from datetime import UTC, datetime, timedelta

from fastapi import Request
from sqlalchemy import or_
from sqlalchemy.orm import Session

from .config import settings
from .models import AccessWhitelist, LoginAttempt, PlatformSettings, User

logger = logging.getLogger("outly.access")

# Заголовки, из которых берём IP клиента. Приложение стоит за nginx (а в режиме
# tls ещё и за Caddy), поэтому request.client.host — это адрес прокси.
#
# ВАЖНО (было уязвимостью): раньше брался ПЕРВЫЙ адрес из X-Forwarded-For. Его
# полностью контролирует клиент — достаточно прислать свой X-Forwarded-For,
# чтобы подменить IP и обойти rate limit. Теперь отсчитываем адрес С КОНЦА,
# пропуская TRUSTED_PROXY_HOPS собственных прокси: элементы, которые дописали
# наши nginx/Caddy, подделать нельзя.
_IP_HEADERS = ("cf-connecting-ip", "x-real-ip")
_MAX_IP_LEN = 64


def _forwarded_for(raw: str, hops: int) -> str:
    """Взять IP клиента из X-Forwarded-For с учётом числа доверенных прокси.

    Цепочка выглядит как: client, proxy1, proxy2. Наши прокси дописывают
    адреса СПРАВА, поэтому «настоящий» клиент — это элемент на позиции
    len(chain) - hops. Если клиент прислал свой X-Forwarded-For, лишние
    элементы окажутся слева и будут проигнорированы.
    """
    chain = [part.strip() for part in raw.split(",") if part.strip()]
    if not chain:
        return ""
    index = len(chain) - max(hops, 1)
    if index < 0:
        index = 0
    return chain[index][:_MAX_IP_LEN]


def normalize_username(username: str | None) -> str:
    """К нижнему регистру, без «@» и пробелов — для сравнения имён."""
    return (username or "").strip().lstrip("@").lower()


def client_ip(request: Request | None) -> str:
    if request is None:
        return ""
    raw_xff = request.headers.get("x-forwarded-for")
    if raw_xff:
        found = _forwarded_for(raw_xff, settings.trusted_proxy_hops)
        if found:
            return found
    for header in _IP_HEADERS:
        raw = request.headers.get(header)
        if raw:
            first = raw.split(",")[0].strip()
            if first:
                return first[:_MAX_IP_LEN]
    return (request.client.host if request.client else "")[:_MAX_IP_LEN]


def user_agent(request: Request | None) -> str:
    if request is None:
        return ""
    return (request.headers.get("user-agent") or "")[:500]


def is_whitelisted(db: Session, telegram_id: int, username: str | None) -> bool:
    """Есть ли человек в белом списке. Админ — всегда да.

    Сверяем по telegram_id и по username: id надёжнее (его нельзя сменить), а
    имя удобнее, когда человек ещё ни разу не входил и его id неизвестен.
    """
    if settings.is_admin(username, telegram_id):
        return True

    uname = normalize_username(username)
    conditions = [AccessWhitelist.telegram_id == telegram_id]
    if uname:
        conditions.append(AccessWhitelist.username == uname)

    row = db.query(AccessWhitelist.id).filter(or_(*conditions)).first()
    return row is not None


def registration_open(db: Session) -> bool:
    """Открыта ли самостоятельная регистрация.

    Источник истины — строка platform_settings, если она есть: рубильник в
    админке должен срабатывать сразу, без перезапуска. Пока строки нет,
    действует значение из окружения.
    """
    try:
        row = db.query(PlatformSettings).order_by(PlatformSettings.id).first()
    except Exception:  # noqa: BLE001 — таблицы может не быть до миграции
        logger.warning("Не удалось прочитать platform_settings", exc_info=True)
        return settings.open_registration
    if row is None:
        return settings.open_registration
    return bool(row.open_registration)


def set_registration_open(db: Session, value: bool, *, admin_id: int | None = None) -> bool:
    """Включить или выключить самостоятельную регистрацию. Коммитит сама."""
    row = db.query(PlatformSettings).order_by(PlatformSettings.id).first()
    if row is None:
        row = PlatformSettings(open_registration=bool(value), updated_by=admin_id)
        db.add(row)
    else:
        row.open_registration = bool(value)
        row.updated_by = admin_id
    db.commit()
    return bool(value)


def login_allowed(db: Session, telegram_id: int, username: str | None) -> bool:
    """Пускать ли этот аккаунт внутрь.

    При открытой регистрации проверять нечего: подпись Telegram уже доказала,
    что аккаунт настоящий, а пробный тариф не умеет отправлять сообщения —
    ломать через него нечего. Белый список нужен, когда регистрацию закрыли.
    """
    if registration_open(db):
        return True
    return is_whitelisted(db, telegram_id, username)


def fresh_signups_from_ip(db: Session, ip: str, *, hours: int = 24) -> int:
    """Сколько НОВЫХ аккаунтов завели с этого адреса за последние часы.

    Считаем по журналу успешных входов: берём попытки с этого IP и оставляем
    те, чей telegram_id принадлежит пользователю, созданному в том же окне.
    Обычный повторный вход сюда не попадает — иначе один человек с рабочего
    Wi-Fi заблокировал бы сам себя.
    """
    if not ip:
        return 0
    since = datetime.now(UTC) - timedelta(hours=max(1, hours))
    ids = {
        row[0]
        for row in db.query(LoginAttempt.telegram_id)
        .filter(
            LoginAttempt.ip == ip,
            LoginAttempt.ok.is_(True),
            LoginAttempt.created_at >= since,
            LoginAttempt.telegram_id.isnot(None),
        )
        .all()
        if row[0] is not None
    }
    if not ids:
        return 0
    return (
        db.query(User.id)
        .filter(User.telegram_id.in_(ids), User.created_at >= since)
        .count()
    )


def bind_whitelist_id(db: Session, telegram_id: int, username: str | None) -> None:
    """Дописать telegram_id в запись, добавленную по одному username.

    После первого входа id известен — сохраняем его, чтобы человек не потерял
    доступ, сменив имя в Telegram. Коммит делает вызывающая сторона.
    """
    uname = normalize_username(username)
    if not uname:
        return
    row = (
        db.query(AccessWhitelist)
        .filter(
            AccessWhitelist.username == uname,
            AccessWhitelist.telegram_id.is_(None),
        )
        .first()
    )
    if row is not None:
        row.telegram_id = telegram_id


def log_attempt(
    db: Session,
    *,
    request: Request | None,
    telegram_id: int | None,
    username: str | None = None,
    first_name: str | None = None,
    last_name: str | None = None,
    ok: bool,
    reason: str,
    owner_id: int | None = None,
) -> None:
    """Записать попытку входа. Коммитит сама — журнал не должен потеряться.

    Ошибку записи глотаем: сломанный журнал не повод не пустить человека,
    который всё сделал правильно.
    """
    try:
        db.add(
            LoginAttempt(
                owner_id=owner_id,
                telegram_id=telegram_id,
                username=(username or "")[:64],
                first_name=(first_name or "")[:128],
                last_name=(last_name or "")[:128],
                ok=ok,
                reason=reason[:40],
                ip=client_ip(request),
                user_agent=user_agent(request),
            )
        )
        db.commit()
    except Exception:  # noqa: BLE001 — журнал не критичен для входа
        logger.warning("Не удалось записать попытку входа", exc_info=True)
        db.rollback()


# --------------------------- IP в контексте запроса ---------------------------
#
# Журналу действий нужен адрес, но log_event вызывается из десятков мест, куда
# request не доходит (сервисы, фоновые задачи). Тащить его параметром через все
# слои — это менять сигнатуры ради одного поля. Поэтому адрес кладётся в
# контекст запроса middleware'ом, а журнал забирает его оттуда.
#
# ContextVar, а не глобальная переменная: у каждого запроса своё значение даже
# при одновременной обработке, и в фоновой задаче оно останется пустым.

_current_ip: ContextVar[str] = ContextVar("current_ip", default="")


def bind_request_ip(request: Request | None) -> None:
    """Запомнить адрес текущего запроса для журнала действий."""
    _current_ip.set(client_ip(request))


def current_request_ip() -> str:
    """Адрес текущего запроса; пусто вне запроса (фоновые задачи, крон)."""
    return _current_ip.get()
