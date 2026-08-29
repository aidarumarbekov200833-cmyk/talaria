"""Telegram login and current-user endpoints."""

import logging
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from .. import access, plans
from ..config import settings
from ..database import get_db
from ..dependencies import current_jti, current_view_actor, get_current_user
from ..models import TokenBlacklist, User
from ..ratelimit import limiter
from ..schemas import (
    OkOut,
    SessionRevokeIn,
    SessionsOut,
    TokenOut,
    UserOut,
)
from ..security import check_telegram_payload, create_jwt
from ..services import log_event
from ..services import sessions as sessions_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["auth"])


class TelegramAuthPayload(BaseModel):
    """Fields posted by Telegram's login widget.

    `model_config.extra = "allow"` keeps any additional field Telegram sends,
    because every received field participates in the signature check.
    """

    model_config = {"extra": "allow"}

    id: int
    first_name: str | None = None
    last_name: str | None = None
    username: str | None = None
    photo_url: str | None = None
    auth_date: int
    hash: str


@router.post(
    "/auth/telegram",
    response_model=TokenOut,
    dependencies=[Depends(limiter("auth"))],
)
def auth_telegram(
    payload: TelegramAuthPayload,
    request: Request,
    db: Session = Depends(get_db),
):
    def reject(reason: str, status: int, detail: str, owner_id: int | None = None) -> HTTPException:
        """Отклонить вход: записать в журнал и отдать понятную причину."""
        access.log_attempt(
            db,
            request=request,
            telegram_id=payload.id,
            username=payload.username,
            first_name=payload.first_name,
            last_name=payload.last_name,
            ok=False,
            reason=reason,
            owner_id=owner_id,
        )
        logger.warning(
            "Вход через Telegram отклонён: %s (tg_id=%s, username=%s, бот=@%s)",
            reason,
            payload.id,
            payload.username or "—",
            settings.public_bot_username,
        )
        return HTTPException(status, detail)

    if settings.demo_mode:
        raise reject("demo_mode", 400, "Telegram-бот не настроен: BOT_TOKEN не задан")

    ok, reason = check_telegram_payload(payload.model_dump(exclude_none=True))
    if not ok:
        # Разные поломки лечатся по-разному, поэтому не сваливаем всё в
        # «подпись невалидна»: раньше из-за этого ушедшие часы и просроченный
        # вход выглядели как подделка подписи.
        messages = {
            "bot_token_missing": "BOT_TOKEN не задан на сервере",
            "hash_missing": "Telegram не передал подпись (hash)",
            "hash_mismatch": (
                "Подпись не сходится: BOT_TOKEN на сервере не от того бота, "
                f"через которого выполнен вход (@{settings.public_bot_username}). "
                "Проверьте токен в @BotFather → /mybots → API Token."
            ),
            "auth_date_invalid": "Некорректный auth_date от Telegram",
            "expired": "Данные входа устарели — нажмите кнопку входа заново",
            "clock_skew": (
                "Часы сервера сильно расходятся с Telegram. "
                "Синхронизируйте время на машине с Docker и повторите вход."
            ),
        }
        raise reject(reason, 401, messages.get(reason, "Подпись Telegram невалидна"))

    # Подпись честная. Дальше решаем, пускать ли: при открытой регистрации —
    # всех, при закрытой — только белый список.
    user = db.query(User).filter(User.telegram_id == payload.id).first()

    if not access.login_allowed(db, payload.id, payload.username):
        raise reject(
            "not_whitelisted",
            403,
            "Вам не разрешено входить в Talaria: вашего Telegram-аккаунта нет в списке доступа. "
            f"Обратитесь к администратору {settings.admin_contact}.",
            owner_id=user.id if user else None,
        )

    # Регистрация пачками с одного адреса. Проверяем только перед созданием
    # НОВОГО аккаунта: тот, кто уже зарегистрирован, входит из любого места
    # сколько угодно раз, включая общий Wi-Fi и офисный NAT.
    open_signup = access.registration_open(db)
    if (
        user is None
        and open_signup
        and settings.trial_max_per_ip > 0
        and not access.is_whitelisted(db, payload.id, payload.username)
    ):
        ip = access.client_ip(request)
        if access.fresh_signups_from_ip(db, ip) >= settings.trial_max_per_ip:
            raise reject(
                "signup_ip_limit",
                429,
                "С этого адреса сегодня уже завели несколько новых аккаунтов. "
                f"Если это вы и доступ нужен, напишите {settings.admin_contact} — откроем вручную.",
            )

    if user is not None and plans.is_blocked(db, user.id):
        raise reject(
            "blocked",
            403,
            f"Доступ приостановлен. Напишите {settings.admin_contact}.",
            owner_id=user.id,
        )

    if not user:
        user = User(telegram_id=payload.id)
        db.add(user)

    user.username = payload.username
    user.first_name = payload.first_name
    user.last_name = payload.last_name
    user.photo_url = payload.photo_url
    # Запись могли добавить по одному имени — закрепляем за ней telegram_id,
    # чтобы смена username в Telegram не отрезала человеку доступ.
    access.bind_whitelist_id(db, payload.id, payload.username)
    db.commit()
    db.refresh(user)

    # Пробный доступ выдаётся один раз и только тому, у кого ещё ничего не
    # было. Внутри start_trial стоит защита от «вышел — вошёл — снова триал».
    if open_signup:
        plans.start_trial(db, user.id)

    access.log_attempt(
        db,
        request=request,
        telegram_id=payload.id,
        username=payload.username,
        first_name=payload.first_name,
        last_name=payload.last_name,
        ok=True,
        reason="ok",
        owner_id=user.id,
    )

    token, jti = create_jwt(user.id)
    sessions_service.register(
        db,
        user.id,
        jti,
        ip=access.client_ip(request),
        user_agent=request.headers.get("user-agent", "") if request else "",
        ttl_days=settings.jwt_ttl_days,
    )
    return TokenOut(
        access_token=token,
        user=UserOut.from_user(user, is_admin=settings.is_admin(user.username, user.telegram_id)),
    )


@router.get("/me", response_model=UserOut)
def me(
    user: User = Depends(get_current_user),
    view_actor: int | None = Depends(current_view_actor),
):
    view_only = view_actor is not None
    return UserOut.from_user(
        user,
        # В режиме просмотра прав администратора нет, даже если смотрят кабинет
        # администратора: иначе в интерфейсе появились бы кнопки, которые всё
        # равно упрутся в запрет.
        is_admin=(not view_only) and settings.is_admin(user.username, user.telegram_id),
        view_only=view_only,
    )


@router.post("/auth/logout", response_model=OkOut)
def logout(
    jti: str = Depends(current_jti),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Отозвать текущий токен: заносим его jti в чёрный список.

    Подпись у JWT остаётся валидной, поэтому «выход» — это отметка в
    token_blacklist, которую проверяет get_current_user. Запись живёт до конца
    срока токена; `expires_at` берём с запасом в срок жизни токена.
    """
    if not db.query(TokenBlacklist).filter(TokenBlacklist.jti == jti).first():
        db.add(
            TokenBlacklist(
                jti=jti,
                owner_id=user.id,
                expires_at=datetime.now(UTC) + timedelta(days=settings.jwt_ttl_days),
            )
        )
        db.commit()
    sessions_service.revoke(db, user.id, jti)
    return OkOut()


@router.get("/auth/sessions", response_model=SessionsOut)
def list_sessions(
    jti: str = Depends(current_jti),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> SessionsOut:
    """Где сейчас открыт вход."""
    rows = sessions_service.active(db, user.id)
    return SessionsOut(
        items=[
            {
                "jti": row.jti,
                "device": sessions_service.describe_agent(row.user_agent),
                "ip": sessions_service.mask_ip(row.ip),
                "created_at": row.created_at,
                "last_seen_at": row.last_seen_at,
                "current": row.jti == jti,
            }
            for row in rows
        ]
    )


@router.post("/auth/sessions/revoke", response_model=OkOut)
def revoke_session(
    payload: SessionRevokeIn,
    jti: str = Depends(current_jti),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> OkOut:
    """Закрыть один вход. Свой собственный закрывать не даём.

    Кнопка «закрыть» в списке нужна, чтобы выгнать чужое устройство. Если
    разрешить закрыть текущую строку, самый частый исход — человек случайно
    выкидывает себя же и думает, что сайт сломался; для выхода есть отдельная
    кнопка, которая ещё и очищает токен в браузере.
    """
    target = (payload.jti or "").strip()
    if not target:
        raise HTTPException(400, "Не указан сеанс")
    if target == jti:
        raise HTTPException(400, "Это текущий вход — используйте кнопку «Выйти»")
    if not sessions_service.revoke(db, user.id, target):
        raise HTTPException(404, "Сеанс не найден или уже закрыт")
    log_event(db, user.id, "security", "Закрыт один из входов в аккаунт", level="warn")
    return OkOut()


@router.post("/auth/sessions/revoke-others", response_model=OkOut)
def revoke_other_sessions(
    jti: str = Depends(current_jti),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> OkOut:
    """Выйти на всех устройствах, кроме текущего."""
    closed = sessions_service.revoke_all(db, user.id, keep_jti=jti)
    if closed:
        log_event(
            db,
            user.id,
            "security",
            f"Закрыты все входы, кроме текущего: {closed}",
            level="warn",
        )
    return OkOut()
