"""Настройки, ключи YouTube API и квота."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from ..database import get_db
from ..dependencies import get_current_user
from ..models import User
from ..plans import ai_allowed, effective_plan_slug, get_plan, get_subscription
from ..ratelimit import limiter
from ..schemas import (
    ApiKeyIn,
    ApiKeyListOut,
    ApiKeyUpdate,
    NotifyTargetOut,
    NotifyTargetTestOut,
    QuotaOut,
    SettingsOut,
    SettingsPatchIn,
    TelegramAccountOut,
    TelegramAccountTicketOut,
)
from ..services import ai_provider_ready, get_settings, log_event, roll_month
from ..services.api_keys import (
    check_api_key as check_api_key_service,
)
from ..services.api_keys import (
    check_key_live as check_key_live_service,
)
from ..services.api_keys import (
    create_api_key as create_api_key_service,
)
from ..services.api_keys import (
    delete_api_key as delete_api_key_service,
)
from ..services.api_keys import (
    list_api_keys as list_api_keys_service,
)
from ..services.api_keys import (
    read_quota as read_quota_service,
)
from ..services.api_keys import (
    update_api_key as update_api_key_service,
)
from ..services.settings import (
    read_settings as read_settings_service,
)
from ..services.settings import (
    list_notify_targets as list_notify_targets_service,
)
from ..services.settings import (
    test_notify_target as test_notify_target_service,
)
from ..services.settings import (
    update_settings as update_settings_service,
)
from ..services.settings import (
    read_telegram_account as read_telegram_account_service,
)
from ..services.settings import (
    telegram_account_ticket as telegram_account_ticket_service,
)

router = APIRouter(prefix="/api", tags=["settings"])


@router.get("/settings", response_model=SettingsOut)
def read_settings(
    user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> SettingsOut:
    """Только чтение: статистика использования и состояние ИИ-провайдера."""
    return read_settings_service(
        db,
        user.id,
        get_settings_fn=get_settings,
        roll_month_fn=roll_month,
        ai_allowed_fn=ai_allowed,
        ai_provider_ready_fn=ai_provider_ready,
    )


@router.patch("/settings", response_model=SettingsOut)
def patch_settings(
    payload: SettingsPatchIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> SettingsOut:
    """Изменить то немногое, что пользователь вправе менять сам.

    Сейчас это только уведомление об ответе лида. Лимиты и ИИ остаются
    read-only: их задаёт админ и владелец сервиса.
    """
    return update_settings_service(
        db,
        user.id,
        reply_notify=payload.reply_notify,
        notify_target_tg_id=payload.notify_target_tg_id,
        send_window_enabled=payload.send_window_enabled,
        send_window_start=payload.send_window_start,
        send_window_end=payload.send_window_end,
        user=user,
    )


@router.get("/settings/notify-targets", response_model=list[NotifyTargetOut])
def read_notify_targets(
    user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> list[NotifyTargetOut]:
    """Аккаунты, любой из которых можно назначить главным для уведомлений."""
    return list_notify_targets_service(db, user)


@router.post("/settings/notify-target/test", response_model=NotifyTargetTestOut)
def check_notify_target(
    user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> NotifyTargetTestOut:
    """Отправить проверочное сообщение туда, куда пойдут уведомления."""
    return test_notify_target_service(db, user)


# ---------- Telegram-аккаунт, с которого выполнен вход ----------


@router.get("/telegram-accounts", response_model=TelegramAccountOut)
def read_telegram_account(
    user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> TelegramAccountOut:
    """Карточка аккаунта в настройках.

    Раньше человек нигде не видел, под каким именно Telegram-аккаунтом он
    работает и не закрыт ли ему доступ: аватарка в шапке ничего не объясняла.

    Отдаём только свои данные: профиль из Telegram-логина, тариф и флаг
    блокировки из подписки. За статусом в самом Telegram (спам-блок,
    ограничения) сервер не ходит и врать об этом не будет.
    """
    return read_telegram_account_service(
        db,
        user,
        get_subscription_fn=get_subscription,
        effective_plan_slug_fn=effective_plan_slug,
        get_plan_fn=get_plan,
    )


# ---------- ключи YouTube ----------


@router.get("/keys", response_model=ApiKeyListOut)
def list_keys(
    user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> ApiKeyListOut:
    return list_api_keys_service(db, user.id)


@router.get("/quota", response_model=QuotaOut)
def read_quota(user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> QuotaOut:
    """Сводка для сайдбара."""
    return read_quota_service(db, user.id)


@router.post("/keys", response_model=ApiKeyListOut, status_code=201)
def create_key(
    payload: ApiKeyIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApiKeyListOut:
    return create_api_key_service(db, user.id, payload, record_event_fn=log_event)


@router.patch("/keys/{key_id}", response_model=ApiKeyListOut)
def update_key(
    key_id: int,
    payload: ApiKeyUpdate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApiKeyListOut:
    return update_api_key_service(db, user.id, key_id, payload)


@router.delete("/keys/{key_id}", response_model=ApiKeyListOut)
def delete_key(
    key_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApiKeyListOut:
    return delete_api_key_service(db, user.id, key_id, record_event_fn=log_event)


def check_key_live(key_value: str) -> tuple[str, str]:
    return check_key_live_service(key_value)


@router.post(
    "/keys/{key_id}/check",
    response_model=ApiKeyListOut,
    dependencies=[Depends(limiter("key_check"))],
)
def check_key(
    key_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApiKeyListOut:
    return check_api_key_service(
        db, user.id, key_id, checker=check_key_live, record_event_fn=log_event
    )


# ---------- Билет для подключения Telegram-аккаунта ----------


@router.get("/telegram-accounts/ticket", response_model=TelegramAccountTicketOut)
def telegram_account_ticket(
    account_id: int | None = Query(
        None, description="Слот из «Мои аккаунты», чей Telegram открыть"
    ),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> TelegramAccountTicketOut:
    """Подписанный пропуск к форме подключения своего Telegram-аккаунта.

    Раньше сессия Telegram была одна на всю установку, и раздел прятали от всех,
    кроме админа: иначе любой пользователь попадал бы в чужую переписку. Теперь
    у каждого своя сессия, а связать окно с человеком нужно без второго логина —
    для этого бэкенд, уже проверивший JWT, подписывает id пользователя тем же
    секретом, который знает tg-mini-client.
    """
    return telegram_account_ticket_service(db, user, account_id)
