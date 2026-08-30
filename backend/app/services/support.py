"""Переписка клиента с владельцем сервиса.

Почему это не чат Telegram и не журнал событий. Владелец попросил «поддержка —
то есть именно мне сообщение, в системе сделай нормально». Значит, обращение
должно быть видно внутри кабинета обеим сторонам: клиент пишет из аккаунт-меню,
владелец отвечает из админки. Telegram здесь только канал уведомления — чтобы
владелец не узнавал об обращении, случайно зайдя в админку.

Правила, ради которых логика вынесена в сервис, а не в роутер:

* уведомление в Telegram отправляется в фоновом потоке. Telegram отвечает за
  сотни миллисекунд, а иногда таймаутом; держать на этом HTTP-ответ клиенту
  нельзя — человек уже нажал «Отправить»;
* сообщение сохраняется даже когда бот не настроен: обращение важнее
  уведомления;
* прочтение отмечается тем, кто читает противоположную сторону, — иначе
  «непрочитано» никогда не гаснет.
"""

from __future__ import annotations

import logging
import threading
from datetime import UTC, datetime

from sqlalchemy import func
from sqlalchemy.orm import Session

from .. import alerts
from ..models import SupportMessage, User
from ..schemas import SupportMessageOut, SupportThreadOut
from .errors import DomainError
from .events import record_event

logger = logging.getLogger("outly.support")

# Потолок длины обращения. Telegram режет сообщение на 4096 символах, а поле в
# интерфейсе рассчитано на несколько абзацев: всё, что длиннее, — это скорее
# вставленный лог, и его место в файле, а не в переписке.
MAX_TEXT = 2000

# Сколько сообщений отдаём в диалоге. Переписка с поддержкой не бывает длинной,
# а без предела ответ однажды вырастет до мегабайта.
THREAD_LIMIT = 200

# Сколько обращений один человек может отправить за сутки. Ограничение считается
# по базе, а не по счётчику в памяти: перезапуск процесса не должен обнулять его.
DAILY_LIMIT = 30


def _out(row: SupportMessage) -> SupportMessageOut:
    return SupportMessageOut(
        id=row.id,
        author=row.author,
        text=row.text,
        created_at=row.created_at,
        read=row.read_at is not None,
    )


def _notify_admins(user_label: str, user_id: int, text: str) -> None:
    """Сообщить владельцу сервиса, что пришло обращение."""
    alerts.send_alert(
        f"Поддержка Talaria\n\nОт: {user_label} (id {user_id})\n\n{text[:1500]}",
        # Ключ разный у каждого обращения: подавлять здесь нечего, два разных
        # вопроса от одного человека — два разных сообщения.
        dedupe_key=f"support:{user_id}:{datetime.now(UTC).timestamp()}",
    )


def _spawn(target, *args) -> None:
    threading.Thread(target=target, args=args, name="support-notify", daemon=True).start()


def send_message(db: Session, user: User, text: str) -> SupportMessageOut:
    """Обращение клиента. Возвращает сохранённое сообщение."""
    body = (text or "").strip()
    if not body:
        raise DomainError(400, "Напишите вопрос — пустое сообщение отправить нельзя")
    if len(body) > MAX_TEXT:
        raise DomainError(400, f"Слишком длинное сообщение: максимум {MAX_TEXT} символов")

    since = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    sent_today = (
        db.query(func.count(SupportMessage.id))
        .filter(
            SupportMessage.owner_id == user.id,
            SupportMessage.author == "user",
            SupportMessage.created_at >= since,
        )
        .scalar()
        or 0
    )
    if sent_today >= DAILY_LIMIT:
        raise DomainError(
            429,
            "Сегодня отправлено слишком много сообщений в поддержку. "
            "Ответ придёт в этот же диалог — дождитесь его.",
        )

    row = SupportMessage(owner_id=user.id, author="user", text=body)
    db.add(row)
    db.commit()
    db.refresh(row)

    label = user.display_name or (f"@{user.username}" if user.username else "без имени")
    _spawn(_notify_admins, label, user.id, body)
    return _out(row)


def read_thread(db: Session, user: User) -> SupportThreadOut:
    """Переписка клиента. Ответы владельца при чтении помечаются прочитанными."""
    rows = (
        db.query(SupportMessage)
        .filter(SupportMessage.owner_id == user.id)
        .order_by(SupportMessage.created_at.asc(), SupportMessage.id.asc())
        .limit(THREAD_LIMIT)
        .all()
    )
    unread = [row for row in rows if row.author == "admin" and row.read_at is None]
    if unread:
        now = datetime.now(UTC)
        for row in unread:
            row.read_at = now
        db.commit()
    return SupportThreadOut(
        messages=[_out(row) for row in rows],
        # Считаем до отметки: интерфейсу нужно знать, что именно человек только
        # что открыл, чтобы погасить мигающую точку.
        unread=len(unread),
        can_write=True,
    )


def unread_for_user(db: Session, owner_id: int) -> int:
    """Сколько ответов владельца клиент ещё не читал."""
    return (
        db.query(func.count(SupportMessage.id))
        .filter(
            SupportMessage.owner_id == owner_id,
            SupportMessage.author == "admin",
            SupportMessage.read_at.is_(None),
        )
        .scalar()
        or 0
    )


def admin_threads(db: Session, limit: int = 50) -> list[dict]:
    """Список обращений для админки: по одной строке на клиента.

    Сортировка — по времени последнего сообщения: сверху то, что ждёт ответа
    дольше всех остальных, а не то, что заведено раньше.
    """
    last = (
        db.query(
            SupportMessage.owner_id.label("owner_id"),
            func.max(SupportMessage.created_at).label("last_at"),
            func.count(SupportMessage.id).label("total"),
        )
        .group_by(SupportMessage.owner_id)
        .subquery()
    )
    unread = (
        db.query(
            SupportMessage.owner_id.label("owner_id"),
            func.count(SupportMessage.id).label("unread"),
        )
        .filter(SupportMessage.author == "user", SupportMessage.read_at.is_(None))
        .group_by(SupportMessage.owner_id)
        .subquery()
    )
    rows = (
        db.query(
            User.id,
            User.username,
            User.first_name,
            User.last_name,
            User.telegram_id,
            last.c.last_at,
            last.c.total,
            unread.c.unread,
        )
        .join(last, last.c.owner_id == User.id)
        .outerjoin(unread, unread.c.owner_id == User.id)
        .order_by(last.c.last_at.desc())
        .limit(limit)
        .all()
    )
    threads = []
    for row in rows:
        name = f"{row.first_name or ''} {row.last_name or ''}".strip()
        threads.append(
            {
                "user_id": row.id,
                "name": name or (f"@{row.username}" if row.username else f"id {row.telegram_id}"),
                "username": row.username or "",
                "telegram_id": row.telegram_id,
                "messages": int(row.total or 0),
                "unread": int(row.unread or 0),
                "last_at": row.last_at,
            }
        )
    return threads


def admin_thread(db: Session, owner_id: int) -> list[SupportMessageOut]:
    """Переписка одного клиента для админки. Обращения помечаются прочитанными."""
    rows = (
        db.query(SupportMessage)
        .filter(SupportMessage.owner_id == owner_id)
        .order_by(SupportMessage.created_at.asc(), SupportMessage.id.asc())
        .limit(THREAD_LIMIT)
        .all()
    )
    now = datetime.now(UTC)
    changed = False
    for row in rows:
        if row.author == "user" and row.read_at is None:
            row.read_at = now
            changed = True
    if changed:
        db.commit()
    return [_out(row) for row in rows]


def admin_reply(db: Session, admin: User, owner_id: int, text: str) -> SupportMessageOut:
    """Ответ владельца сервиса. Клиент видит его в кабинете и получает в Telegram."""
    body = (text or "").strip()
    if not body:
        raise DomainError(400, "Пустой ответ отправить нельзя")
    if len(body) > MAX_TEXT:
        raise DomainError(400, f"Слишком длинный ответ: максимум {MAX_TEXT} символов")
    target = db.get(User, owner_id)
    if not target:
        raise DomainError(404, "Пользователь не найден")

    row = SupportMessage(owner_id=owner_id, author="admin", admin_id=admin.id, text=body)
    db.add(row)
    db.commit()
    db.refresh(row)

    # Событие в журнал клиента: ответ поддержки виден в колокольчике, даже если
    # человек не открывал аккаунт-меню.
    record_event(
        db,
        owner_id,
        "notification",
        f"Ответ поддержки: {body[:300]}",
        "info",
        "support",
        row.id,
    )
    if target.telegram_id:
        _spawn(
            alerts.notify_user,
            target.telegram_id,
            f"Ответ поддержки Talaria\n\n{body[:1500]}",
        )
    return _out(row)
