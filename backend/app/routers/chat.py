"""Переписка Telegram в кабинете.

Роутер — прослойка между браузером и GramJS-шлюзом. Ни одно сообщение здесь не
сохраняется: строка сессии живёт только в шлюзе, а личная переписка владельца
проходит насквозь. Копия чужих личных сообщений в нашей базе была бы обузой,
которую пришлось бы защищать, вывозить в бэкапах и удалять по запросу, — при
том что показывать переписку можно и без неё.

Всё, что делает бэкенд сверх проксирования:
  * проверяет, что аккаунт принадлежит тому, кто спрашивает;
  * подмешивает к диалогам признак «это лид из моей базы»;
  * ограничивает частоту, чтобы вкладка в цикле не поймала флуд-лимит Telegram.
"""

from __future__ import annotations

import re
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from ..database import get_db
from ..dependencies import get_current_user
from ..models import Lead, LeadDatabase, User, WarmupAccount
from ..ratelimit import limiter
from ..schemas import (
    ChatAccountOut,
    ChatDialogCursorOut,
    ChatDialogOut,
    ChatDialogsOut,
    ChatHistoryOut,
    ChatImageOut,
    ChatMessageOut,
    ChatReadIn,
    ChatSendIn,
    ChatSendOut,
    OkOut,
)
from ..telegram_gateway import TelegramGatewayError, gateway

router = APIRouter(prefix="/api/chat", tags=["chat"], dependencies=[Depends(limiter("chat"))])

# Идентификатор собеседника из Telegram: возможен минус (группа, канал), но
# ничего кроме цифр. Проверяем здесь, а не только в шлюзе, чтобы произвольная
# строка не доехала до MTProto-вызова.
PEER_RE = re.compile(r"^-?\d{1,20}$")

MAX_TEXT = 4096


def _peer(peer_id: str) -> str:
    value = str(peer_id or "").strip()
    if not PEER_RE.match(value):
        raise HTTPException(400, "Некорректный идентификатор диалога")
    return value


def _account(db: Session, user: User, account_id: int | None) -> WarmupAccount:
    """Выбрать аккаунт владельца и убедиться, что он подключён.

    Без явного account_id берём основной, иначе первый подключённый: у
    большинства аккаунт один, и заставлять фронт каждый раз его называть —
    лишний повод передать чужой номер.
    """
    query = db.query(WarmupAccount).filter(WarmupAccount.owner_id == user.id)
    if account_id is not None:
        account = query.filter(WarmupAccount.id == int(account_id)).first()
        if not account:
            raise HTTPException(404, "Аккаунт не найден")
    else:
        connected = [row for row in query.order_by(WarmupAccount.id).all() if row.connected_at]
        account = next((row for row in connected if row.is_primary), None) or (
            connected[0] if connected else None
        )
        if not account:
            raise HTTPException(400, "Не подключён ни один Telegram-аккаунт")
    if not account.connected_at:
        raise HTTPException(400, "Этот аккаунт не подключён к Telegram")
    return account


def _fail(exc: TelegramGatewayError) -> HTTPException:
    """Ошибку Telegram отдаём как есть, но с понятным кодом ответа.

    Флуд-лимит — это 429 с указанием паузы, а не «что-то пошло не так»: клиент
    по коду понимает, что запрос надо повторить, а не что чат сломан.
    """
    if exc.retry_after:
        return HTTPException(
            429,
            str(exc),
            headers={"Retry-After": str(int(exc.retry_after))},
        )
    return HTTPException(502, str(exc))


def _lead_ids(db: Session, user: User, tg_ids: list[str]) -> dict[str, int]:
    """Кто из собеседников есть в базе лидов.

    Нужно, чтобы в списке диалогов было видно рабочие контакты среди личных:
    иначе переписка с лидом ничем не отличается от переписки с однокурсником.
    """
    wanted = [value for value in tg_ids if value]
    if not wanted:
        return {}
    # У лида нет владельца: он принадлежит базе, а база — пользователю.
    # Соединение обязательно, иначе в чужой переписке всплыли бы чужие лиды.
    rows = (
        db.query(Lead.id, Lead.tg_id)
        .join(LeadDatabase, Lead.database_id == LeadDatabase.id)
        .filter(LeadDatabase.owner_id == user.id, Lead.tg_id.in_(wanted))
        .all()
    )
    return {str(tg_id): int(lead_id) for lead_id, tg_id in rows if tg_id}


@router.get("/accounts", response_model=list[ChatAccountOut])
def chat_accounts(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> list[ChatAccountOut]:
    """Между какими аккаунтами можно переключаться в переписке."""
    rows = (
        db.query(WarmupAccount)
        .filter(WarmupAccount.owner_id == user.id)
        .order_by(WarmupAccount.is_primary.desc(), WarmupAccount.id)
        .all()
    )
    return [
        ChatAccountOut(
            id=row.id,
            label=row.label or (f"@{row.tg_username}" if row.tg_username else f"Аккаунт {row.id}"),
            username=row.tg_username or "",
            connected=bool(row.connected_at),
        )
        for row in rows
    ]


@router.get("/dialogs", response_model=ChatDialogsOut)
def chat_dialogs(
    account_id: int | None = None,
    limit: int = Query(40, ge=1, le=100),
    offset_date: int = 0,
    offset_id: int = 0,
    offset_peer: str = "",
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> ChatDialogsOut:
    account = _account(db, user, account_id)
    try:
        body = gateway.chat_dialogs(
            user.id,
            account.id,
            limit=limit,
            offset_date=max(0, int(offset_date)),
            offset_id=max(0, int(offset_id)),
            offset_peer=str(offset_peer or "").strip(),
        )
    except TelegramGatewayError as exc:
        raise _fail(exc) from exc

    raw: list[dict[str, Any]] = [item for item in (body.get("items") or []) if isinstance(item, dict)]
    leads = _lead_ids(db, user, [str(item.get("peer_id") or "") for item in raw])
    items = []
    for item in raw:
        dialog = ChatDialogOut(**item)
        dialog.lead_id = leads.get(dialog.peer_id)
        items.append(dialog)

    cursor = body.get("next")
    return ChatDialogsOut(
        items=items,
        next=ChatDialogCursorOut(**cursor) if isinstance(cursor, dict) else None,
        account_id=account.id,
    )


@router.get("/history", response_model=ChatHistoryOut)
def chat_history(
    peer_id: str,
    account_id: int | None = None,
    limit: int = Query(50, ge=1, le=100),
    offset_id: int = 0,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> ChatHistoryOut:
    account = _account(db, user, account_id)
    try:
        body = gateway.chat_history(
            user.id,
            account.id,
            _peer(peer_id),
            limit=limit,
            offset_id=max(0, int(offset_id)),
        )
    except TelegramGatewayError as exc:
        raise _fail(exc) from exc
    items = [
        ChatMessageOut(**item)
        for item in (body.get("items") or [])
        if isinstance(item, dict)
    ]
    return ChatHistoryOut(items=items, next_offset_id=int(body.get("next_offset_id") or 0))


@router.post("/send", response_model=ChatSendOut)
def chat_send(
    payload: ChatSendIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> ChatSendOut:
    account = _account(db, user, payload.account_id)
    text = (payload.text or "").strip()
    if not text:
        raise HTTPException(400, "Пустое сообщение")
    if len(text) > MAX_TEXT:
        raise HTTPException(400, f"Сообщение длиннее {MAX_TEXT} символов")
    try:
        body = gateway.chat_send(
            user.id,
            account.id,
            _peer(payload.peer_id),
            text,
            reply_to=max(0, int(payload.reply_to or 0)),
        )
    except TelegramGatewayError as exc:
        raise _fail(exc) from exc
    # В журнал попадает факт отправки, но не текст: журнал видит поддержка, а
    # личная переписка — не её дело.
    return ChatSendOut(
        message_id=int(body.get("message_id") or 0),
        date=int(body.get("date") or 0),
    )


@router.post("/read", response_model=OkOut)
def chat_read(
    payload: ChatReadIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> OkOut:
    account = _account(db, user, payload.account_id)
    try:
        gateway.chat_read(user.id, account.id, _peer(payload.peer_id))
    except TelegramGatewayError as exc:
        raise _fail(exc) from exc
    return OkOut()


@router.get("/avatar", response_model=ChatImageOut)
def chat_avatar(
    peer_id: str,
    account_id: int | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> ChatImageOut:
    account = _account(db, user, account_id)
    try:
        return ChatImageOut(image=gateway.chat_avatar(user.id, account.id, _peer(peer_id)))
    except TelegramGatewayError as exc:
        raise _fail(exc) from exc


@router.get("/media", response_model=ChatImageOut)
def chat_media(
    peer_id: str,
    message_id: int,
    account_id: int | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> ChatImageOut:
    account = _account(db, user, account_id)
    if message_id <= 0:
        raise HTTPException(400, "Не указано сообщение")
    try:
        image = gateway.chat_media(user.id, account.id, _peer(peer_id), int(message_id))
    except TelegramGatewayError as exc:
        raise _fail(exc) from exc
    return ChatImageOut(image=image)
