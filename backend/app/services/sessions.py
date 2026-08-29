"""Сеансы входа: список устройств и принудительный выход.

Раньше «выход» закрывал только тот браузер, в котором на кнопку нажали. Токен
живёт неделями, и человек, забывший выйти на чужом компьютере, не мог сделать
ничего — кроме как просить администратора. Здесь появляется список входов и
возможность закрыть любой из них.

Устройство мы не «определяем», а угадываем по User-Agent — и подписываем это
честно. Строка приходит от браузера, её легко подделать, и выдавать догадку за
факт в разделе про безопасность нельзя.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta

from sqlalchemy import or_
from sqlalchemy.orm import Session

from ..models import TokenBlacklist, UserSession

# Как часто обновлять «последнюю активность». Писать в базу на каждый запрос
# незачем: точность до минут здесь ничего не решает, а лишняя запись случается
# на каждое движение в интерфейсе.
TOUCH_INTERVAL = timedelta(minutes=5)

# Порядок важен: Edge представляется и как Chrome, и как Safari, Chrome — как
# Safari. Кто раньше в списке, тот и выигрывает.
_BROWSERS = (
    ("YaBrowser", "Яндекс.Браузер"),
    ("Edg/", "Edge"),
    ("OPR/", "Opera"),
    ("Firefox", "Firefox"),
    ("Chrome", "Chrome"),
    ("Safari", "Safari"),
)

_PLATFORMS = (
    ("iPhone", "iPhone"),
    ("iPad", "iPad"),
    ("Android", "Android"),
    ("Windows", "Windows"),
    ("Mac OS X", "macOS"),
    ("Macintosh", "macOS"),
    ("Linux", "Linux"),
)


def describe_agent(user_agent: str | None) -> str:
    """Человеческая подпись входа: «Chrome, Windows».

    Ничего не выдумываем: не распознали — так и пишем. «Неизвестное устройство»
    честнее, чем подставленный наугад «Chrome».
    """
    raw = str(user_agent or "").strip()
    if not raw:
        return "Неизвестное устройство"
    browser = next((name for token, name in _BROWSERS if token in raw), "")
    platform = next((name for token, name in _PLATFORMS if token in raw), "")
    parts = [part for part in (browser, platform) if part]
    return ", ".join(parts) if parts else "Неизвестное устройство"


def mask_ip(value: str | None) -> str:
    """Показать IP, но не целиком.

    Полный адрес в интерфейсе не нужен: чтобы узнать свой вход, хватает города
    и начала адреса, а вот утечка списка адресов на скриншоте — уже лишнее.
    IPv4 теряет последний октет, IPv6 — всё после третьей группы.
    """
    raw = str(value or "").strip()
    if not raw:
        return ""
    if re.fullmatch(r"\d{1,3}(\.\d{1,3}){3}", raw):
        return raw.rsplit(".", 1)[0] + ".×"
    if ":" in raw:
        groups = [part for part in raw.split(":") if part]
        return ":".join(groups[:3]) + ":×" if groups else ""
    return raw[:12]


def register(
    db: Session,
    owner_id: int,
    jti: str,
    *,
    ip: str = "",
    user_agent: str = "",
    ttl_days: int,
) -> UserSession:
    """Записать выданный токен как сеанс.

    Вызывается ровно там же, где выпускается JWT: если запись появится позже
    или по другому поводу, список входов разойдётся с реальностью, и «выйти
    везде» перестанет что-либо гарантировать.
    """
    now = datetime.now(UTC)
    row = UserSession(
        owner_id=owner_id,
        jti=jti,
        ip=(ip or "")[:64],
        user_agent=(user_agent or "")[:256],
        expires_at=now + timedelta(days=ttl_days),
        last_seen_at=now,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def touch(db: Session, jti: str) -> None:
    """Отметить активность сеанса, но не чаще раза в несколько минут."""
    row = db.query(UserSession).filter(UserSession.jti == jti).first()
    if not row or row.revoked_at:
        return
    now = datetime.now(UTC)
    last = row.last_seen_at
    if last is not None:
        # Значения из SQLite приходят без часового пояса — сравнение упало бы.
        if last.tzinfo is None:
            last = last.replace(tzinfo=UTC)
        if now - last < TOUCH_INTERVAL:
            return
    row.last_seen_at = now
    db.commit()


def active(db: Session, owner_id: int) -> list[UserSession]:
    """Живые сеансы: не отозванные и не истёкшие, свежие сверху."""
    now = datetime.now(UTC)
    rows = (
        db.query(UserSession)
        .filter(
            UserSession.owner_id == owner_id,
            UserSession.revoked_at.is_(None),
            or_(UserSession.expires_at.is_(None), UserSession.expires_at > now),
        )
        .order_by(UserSession.created_at.desc())
        .all()
    )
    return rows


def revoke(db: Session, owner_id: int, jti: str) -> bool:
    """Закрыть один сеанс.

    Доступ реально закрывает запись в чёрном списке — её проверяет каждый
    запрос. Отметка в user_sessions нужна для списка и истории; одной её было бы
    недостаточно, и «выход» оказался бы косметическим.
    """
    row = (
        db.query(UserSession)
        .filter(UserSession.owner_id == owner_id, UserSession.jti == jti)
        .first()
    )
    if not row or row.revoked_at:
        return False
    now = datetime.now(UTC)
    row.revoked_at = now
    _blacklist(db, owner_id, row.jti, row.expires_at)
    db.commit()
    return True


def revoke_all(db: Session, owner_id: int, *, keep_jti: str = "") -> int:
    """Закрыть все сеансы, кроме указанного. Возвращает число закрытых.

    Текущий сеанс по умолчанию сохраняем: кнопка «выйти везде» нужна, чтобы
    выгнать чужих, а не чтобы выкинуть себя же на экран входа посреди работы.
    """
    closed = 0
    for row in active(db, owner_id):
        if keep_jti and row.jti == keep_jti:
            continue
        row.revoked_at = datetime.now(UTC)
        _blacklist(db, owner_id, row.jti, row.expires_at)
        closed += 1
    if closed:
        db.commit()
    return closed


def _blacklist(db: Session, owner_id: int, jti: str, expires_at: datetime | None) -> None:
    if db.query(TokenBlacklist).filter(TokenBlacklist.jti == jti).first():
        return
    db.add(
        TokenBlacklist(
            jti=jti,
            owner_id=owner_id,
            # Срок неизвестен — берём заведомо больший: запись в чёрном списке
            # дешёвая, а токен, переживший свою отметку, снова заработал бы.
            expires_at=expires_at or datetime.now(UTC) + timedelta(days=30),
        )
    )
