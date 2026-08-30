"""Поддержка: переписка клиента с владельцем сервиса.

Клиентские ручки и админские лежат в одном модуле сознательно: это две стороны
одного диалога, и расходиться в правилах (длина текста, порядок сообщений,
отметка прочтения) им нельзя.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from ..database import get_db
from ..dependencies import get_current_user, require_admin
from ..models import User
from ..ratelimit import limiter
from ..schemas import (
    AdminSupportListOut,
    AdminSupportReplyIn,
    AdminSupportThreadOut,
    SupportMessageOut,
    SupportSendIn,
    SupportThreadOut,
    SupportUnreadOut,
)
from ..services import support as support_service

router = APIRouter(prefix="/api", tags=["support"])


@router.get("/support/messages", response_model=SupportThreadOut)
def read_support_thread(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> SupportThreadOut:
    """Переписка с поддержкой. Открытие гасит непрочитанные ответы."""
    return support_service.read_thread(db, user)


@router.get("/support/unread", response_model=SupportUnreadOut)
def read_support_unread(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> SupportUnreadOut:
    """Сколько ответов ждёт прочтения. Отдельно от переписки: точку на аккаунте
    надо показать, не открывая диалог и не гася при этом непрочитанное."""
    return SupportUnreadOut(unread=support_service.unread_for_user(db, user.id))


@router.post(
    "/support/messages",
    response_model=SupportMessageOut,
    dependencies=[Depends(limiter("support"))],
)
def send_support_message(
    payload: SupportSendIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> SupportMessageOut:
    """Написать владельцу сервиса. Уведомление ему уходит в Telegram."""
    return support_service.send_message(db, user, payload.text)


@router.get("/admin/support", response_model=AdminSupportListOut)
def list_support_threads(
    limit: int = Query(50, ge=1, le=200),
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> AdminSupportListOut:
    """Кто писал в поддержку, начиная с самых свежих обращений."""
    threads = support_service.admin_threads(db, limit)
    return AdminSupportListOut(
        threads=[AdminSupportThreadOut(**row) for row in threads],
        unread_total=sum(row["unread"] for row in threads),
    )


@router.get("/admin/support/{user_id}", response_model=list[SupportMessageOut])
def read_support_thread_as_admin(
    user_id: int,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> list[SupportMessageOut]:
    """Диалог одного клиента. Открытие помечает его обращения прочитанными."""
    return support_service.admin_thread(db, user_id)


@router.post("/admin/support/{user_id}", response_model=SupportMessageOut)
def reply_support_thread(
    user_id: int,
    payload: AdminSupportReplyIn,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> SupportMessageOut:
    """Ответ клиенту: виден в его кабинете и приходит ему в Telegram."""
    return support_service.admin_reply(db, admin, user_id, payload.text)
