"""WebSocket для живой ленты событий.

Один канал на пользователя: `/api/ws?token=<JWT>`. Поток односторонний —
сервер шлёт события, клиент только держит соединение (и отвечает на ping). Всё,
что уходит в сокет, уже записано в БД через `log_event`; сокет лишь доставляет
это мгновенно, без опроса.

Аутентификация через query-параметр, а не заголовок: браузерный WebSocket не
позволяет задать `Authorization`. Токен проверяем теми же правилами, что и
обычные ручки (подпись, чёрный список jti, блокировка пользователя).
"""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, status

from ..access import login_allowed
from ..database import SessionLocal
from ..events_hub import hub
from ..models import TokenBlacklist, User
from ..plans import is_blocked
from ..security import decode_jwt

logger = logging.getLogger("outly.ws")

router = APIRouter()

# Как часто слать ping при тишине. Прокси (nginx) закрывает простаивающие
# апгрейд-соединения; периодический кадр держит канал живым и заодно проверяет,
# что клиент ещё на связи.
_PING_INTERVAL_SECONDS = 25


def _authenticate(token: str) -> int | None:
    """Проверить токен так же, как get_current_user. Возвращает owner_id или None.

    Своя короткоживущая сессия: WebSocket не проходит через Depends(get_db).
    """
    decoded = decode_jwt(token)
    if not decoded:
        return None
    user_id, jti = decoded

    db = SessionLocal()
    try:
        if db.query(TokenBlacklist).filter(TokenBlacklist.jti == jti).first():
            return None  # токен отозван (logout)
        user = db.get(User, user_id)
        if not user:
            return None
        if is_blocked(db, user.id):
            return None  # доступ приостановлен админом
        # Как в get_current_user: убрали из допуска при закрытой регистрации —
        # живая лента закрывается сразу, а не когда истечёт JWT.
        if not login_allowed(db, user.telegram_id, user.username):
            return None
        return user.id
    finally:
        db.close()


@router.websocket("/api/ws")
async def events_ws(websocket: WebSocket) -> None:
    # Browsers cannot set Authorization on WebSocket handshakes.  Carry the JWT
    # as the second negotiated subprotocol instead of putting it in the URL,
    # where reverse-proxy access logs and browser history may expose it.
    protocols = [
        part.strip()
        for part in websocket.headers.get("sec-websocket-protocol", "").split(",")
        if part.strip()
    ]
    supported_auth_protocols = {"zephyr.jwt", "outly.jwt", "zapp.jwt"}  # legacy clients work
    uses_protocol_auth = len(protocols) >= 2 and protocols[0] in supported_auth_protocols
    token = protocols[1] if uses_protocol_auth else websocket.query_params.get("token", "")
    owner_id = _authenticate(token)
    if owner_id is None:
        # Закрываем до accept с политикой — клиент поймёт, что дело в авторизации.
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    await websocket.accept(subprotocol=protocols[0] if uses_protocol_auth else None)
    queue = hub.subscribe(owner_id)
    # Первый кадр — подтверждение, что канал живой (клиент гасит индикатор ошибки).
    await websocket.send_json({"type": "hello"})

    try:
        while True:
            try:
                event = await asyncio.wait_for(queue.get(), timeout=_PING_INTERVAL_SECONDS)
            except TimeoutError:
                # Тишина — шлём ping, чтобы прокси не закрыл соединение.
                await websocket.send_json({"type": "ping"})
                continue
            await websocket.send_json(event)
    except WebSocketDisconnect:
        pass
    except Exception as exc:  # сеть/сериализация — закрываем канал спокойно
        logger.debug("WS-канал закрыт: %s", exc)
    finally:
        hub.unsubscribe(owner_id, queue)
