"""Shared FastAPI dependencies."""

import logging

from fastapi import Depends, Header, HTTPException, Request
from sqlalchemy.orm import Session

from .access import client_ip, login_allowed
from .config import settings
from .database import get_db
from .logging_setup import set_current_user_id
from .models import TokenBlacklist, User
from .plans import is_blocked
from .security import decode_claims, decode_jwt
from .services.sessions import touch as touch_session

logger = logging.getLogger("outly.admin")


def _token_from_header(authorization: str | None) -> str:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "Нет токена авторизации")
    return authorization.removeprefix("Bearer ").strip()


def current_jti(authorization: str = Header(None)) -> str:
    """jti текущего токена — нужен ручке logout, чтобы отозвать именно его."""
    decoded = decode_jwt(_token_from_header(authorization))
    if not decoded:
        raise HTTPException(401, "Невалидный токен")
    return decoded[1]


def _claims_cached(request: Request | None, authorization: str | None) -> dict | None:
    """Разобрать JWT один раз на запрос.

    Подпись проверяют и middleware readonly_view_guard, и get_current_user, и
    current_view_actor — раньше это было три HMAC-проверки на каждый вызов.
    Результат живёт в request.state; ключ — сам токен, чтобы подмена
    заголовка внутри одного запроса (тесты, внутренние вызовы) не отдавала
    чужие claims.
    """
    token = _token_from_header(authorization)
    if request is None:
        return decode_claims(token)
    cached = getattr(request.state, "jwt_claims", None)
    if cached is not None and cached[0] == token:
        return cached[1]
    claims = decode_claims(token)
    request.state.jwt_claims = (token, claims)
    return claims


def current_view_actor(
    request: Request = None, authorization: str = Header(None)
) -> int | None:
    """id администратора, если кабинет открыт в режиме просмотра, иначе None.

    Читает заголовок сама, а не берёт значение из contextvars: синхронные
    зависимости и обработчики FastAPI выполняются в разных потоках пула, и
    установленное в зависимости значение до обработчика не доезжает — признак
    молча оказывался бы «обычный вход».
    """
    claims = _claims_cached(request, authorization) or {}
    if not claims.get("ro"):
        return None
    actor = claims.get("act")
    try:
        return int(actor) if actor else None
    except (TypeError, ValueError):
        return None


def get_current_user(
    request: Request = None,
    authorization: str = Header(None),
    db: Session = Depends(get_db),
) -> User:
    claims = _claims_cached(request, authorization)
    try:
        user_id, jti = int(claims["sub"]), str(claims["jti"])
    except (TypeError, KeyError, ValueError):
        raise HTTPException(401, "Невалидный токен") from None

    # Токен отозван (logout)? Подпись всё ещё верна, но доступ закрыт.
    if db.query(TokenBlacklist).filter(TokenBlacklist.jti == jti).first():
        raise HTTPException(401, "Сессия завершена — войдите снова")

    user = db.get(User, user_id)
    if not user:
        raise HTTPException(401, "Невалидный токен")

    # Заблокированный админом пользователь дальше не проходит.
    if is_blocked(db, user.id):
        raise HTTPException(403, "Доступ к аккаунту приостановлен. Напишите в поддержку.")

    # Закрыли регистрацию и убрали из белого списка — старый токен перестаёт
    # работать сразу, а не через две недели, когда истечёт JWT.
    if not login_allowed(db, user.telegram_id, user.username):
        raise HTTPException(
            403,
            f"Доступ закрыт: аккаунта нет в списке допущенных. "
            f"Напишите {settings.admin_contact}.",
        )

    # Режим просмотра: у такого токена нет записи сеанса и он не должен
    # оживлять чужой список входов.
    if not claims.get("ro"):
        # Отметка активности сеанса. Внутри стоит защёлка на несколько минут,
        # поэтому запись в базу случается редко, а не на каждый запрос.
        touch_session(db, jti)

    # С этого момента все записи в лог в рамках запроса получают user_id.
    # Без этого разбор инцидента сводится к угадыванию, кто именно что делал.
    set_current_user_id(user.id)
    return user


def require_admin(
    request: Request,
    user: User = Depends(get_current_user),
    view_actor: int | None = Depends(current_view_actor),
) -> User:
    """Пускать дальше только администратора.

    Живёт здесь, а не в routers/admin.py, потому что общая проверка прав нужна
    всем административным маршрутам и должна иметь один источник истины.
    """
    # Главный ключ — telegram_id, а не username. Username в Telegram меняется за
    # пару кликов и освобождается для других: стоило админу сменить имя — и
    # чужой человек, занявший освободившийся username, получал админку.
    if view_actor is not None:
        raise HTTPException(403, "Админка недоступна в режиме просмотра")

    if not settings.is_admin(user.username, user.telegram_id):
        raise HTTPException(403, "Доступно только администратору")

    # IP-allowlist — второй независимый рубеж: даже если чьи-то JWT админа
    # утёк, войти в админку можно только с разрешённого IP/сети. Пустой
    # ADMIN_IP_ALLOWLIST отключает эту проверку — см. is_ip_allowed_for_admin.
    ip = client_ip(request)
    if not settings.is_ip_allowed_for_admin(ip):
        logger.warning("Админ-доступ заблокирован IP-allowlist: user_id=%s ip=%s", user.id, ip)
        raise HTTPException(403, f"Доступ с этого IP запрещён ({ip})")

    return user
