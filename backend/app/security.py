"""Telegram login verification and JWT issuing/decoding."""

import hashlib
import hmac
import secrets
import time
from datetime import UTC, datetime, timedelta

import jwt

from .config import settings


class SecurityError(Exception):
    """Сбой авторизации с понятным сообщением (проверка чёрного списка и т.п.)."""


# Допуск на расхождение часов. Если часы сервера отстают от Telegram, его
# auth_date оказывается «в будущем», возраст уходит в минус — и строгая
# проверка `0 <= age` отвергала бы совершенно честный вход. Это не подделка,
# а рассинхрон времени (обычное дело в Docker Desktop/WSL2 после сна хоста),
# поэтому небольшой минус допускаем. Подделать подпись это не помогает:
# HMAC уже проверен выше, а окно сдвигается лишь на минуты.
_CLOCK_SKEW_TOLERANCE_SECONDS = 300


def _check_value(value) -> str:
    """Привести значение к строке так же, как его отдал Telegram.

    f"{v}" ломается на bool: Python печатает `True`, а в строке подписи у
    Telegram стоит `true`, и HMAC не сходится. Виджет сейчас присылает только
    строки и числа, но `extra="allow"` пропускает любые новые поля, поэтому
    приводим значения честно, по правилам JSON.
    """
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def check_telegram_payload(data: dict) -> tuple[bool, str]:
    """Проверить подпись и свежесть данных. Возвращает (успех, причина).

    Причина нужна, чтобы не показывать «подпись невалидна» на самом деле из-за
    ушедших часов или просроченного входа: это разные поломки и лечатся
    по-разному. Значения причины: ok, bot_token_missing, hash_missing,
    hash_mismatch, auth_date_invalid, expired, clock_skew.

    Per Telegram's spec the check string is every received field except `hash`,
    formatted as `key=value` and sorted **by key**, joined with newlines; the
    HMAC key is SHA256 of the bot token. Unknown fields are included rather
    than dropped, so a field Telegram adds later still hashes correctly.
    """
    received_hash = data.get("hash")
    if not settings.bot_token:
        return False, "bot_token_missing"
    if not received_hash:
        return False, "hash_missing"

    pairs = [f"{k}={_check_value(v)}" for k, v in data.items() if k != "hash" and v is not None]
    pairs.sort()
    data_check_string = "\n".join(pairs)

    secret = hashlib.sha256(settings.bot_token.encode()).digest()
    expected = hmac.new(secret, data_check_string.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, str(received_hash)):
        return False, "hash_mismatch"

    try:
        auth_date = int(data.get("auth_date", 0))
    except (TypeError, ValueError):
        return False, "auth_date_invalid"

    age = time.time() - auth_date
    if age > settings.telegram_auth_max_age_seconds:
        return False, "expired"
    if age < -_CLOCK_SKEW_TOLERANCE_SECONDS:
        return False, "clock_skew"
    return True, "ok"


def verify_telegram_payload(data: dict) -> bool:
    """Да/нет по подписи. Обёртка над check_telegram_payload для совместимости."""
    ok, _reason = check_telegram_payload(data)
    return ok


def create_jwt(user_id: int) -> tuple[str, str]:
    """Выпустить токен. Возвращает (token, jti) — jti нужен для logout."""
    now = datetime.now(UTC)
    jti = secrets.token_urlsafe(24)
    payload = {
        "sub": str(user_id),
        "jti": jti,
        "iat": now,
        "exp": now + timedelta(days=settings.jwt_ttl_days),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm="HS256"), jti


def create_view_token(admin_id: int, target_id: int, ttl_minutes: int = 30) -> tuple[str, str]:
    """Токен для просмотра чужого кабинета глазами администратора.

    Отдельная функция, а не флаг у обычного входа: у такого токена другие
    правила (только чтение, короткий срок), и смешивать их с обычной выдачей
    опасно — однажды флаг забудут выставить.

    `act` (actor) хранит, КТО смотрит: без этого в журнале останется только
    «пользователь сделал», и разобрать инцидент будет нечем. `ro` включает
    запрет на любые изменения.
    """
    now = datetime.now(UTC)
    jti = secrets.token_urlsafe(24)
    payload = {
        "sub": str(target_id),
        "act": str(admin_id),
        "ro": True,
        "jti": jti,
        "iat": now,
        # Тридцать минут: этого хватает разобраться в проблеме, а забытая
        # вкладка сама перестаёт быть доступом к чужому кабинету.
        "exp": now + timedelta(minutes=max(1, int(ttl_minutes))),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm="HS256"), jti


def decode_claims(token: str) -> dict | None:
    """Все поля токена. Нужен там, где важен не только владелец: режим
    просмотра проверяется по `ro` и `act`."""
    try:
        claims = jwt.decode(token, settings.jwt_secret, algorithms=["HS256"])
    except jwt.PyJWTError:
        return None
    return claims if claims.get("jti") else None


def decode_jwt(token: str) -> tuple[int, str] | None:
    """Раскодировать токен. Возвращает (user_id, jti) или None.

    jti возвращается, чтобы get_current_user мог сверить его с чёрным списком.
    """
    try:
        claims = jwt.decode(token, settings.jwt_secret, algorithms=["HS256"])
        jti = claims.get("jti", "")
        if not jti:
            return None  # токен старого формата (без jti) — считаем недействительным
        return int(claims["sub"]), jti
    except (jwt.PyJWTError, KeyError, TypeError, ValueError):
        return None
