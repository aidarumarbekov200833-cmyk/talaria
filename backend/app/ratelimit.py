"""Простой in-memory rate limit по (пользователь/IP + группа эндпоинтов).

Намеренно без Redis: один процесс uvicorn, счётчики в памяти. Если понадобится
масштабирование на несколько воркеров — вынести в общий стор. Здесь задача
скромнее: прикрыть дорогие ручки (вход, ИИ, проверка ключа, поиск, тест-отправка)
от простого перебора и случайных циклов на фронте.

Окно — «скользящее ведро» на фиксированный интервал: список меток времени на
ключ, старые метки отбрасываются. Для нашего трафика этого достаточно.
"""

from __future__ import annotations

import threading
import time

from fastapi import HTTPException, Request

from .access import client_ip
from .config import settings

_lock = threading.Lock()
_hits: dict[str, list[float]] = {}
# Амортизированная уборка: без неё каждый новый IP на публичных ручках
# оставался в _hits навсегда — медленная утечка памяти и дешёвый способ
# раздуть процесс перебором адресов.
_SWEEP_EVERY = 1000
_calls_since_sweep = 0


def _sweep(cutoff: float) -> None:
    """Убрать ключи, у которых не осталось меток в окне. Вызывать под _lock."""
    dead = [key for key, hits in _hits.items() if not hits or hits[-1] <= cutoff]
    for key in dead:
        del _hits[key]


def _limit_for(group: str) -> int:
    return {
        "auth": settings.rate_limit_auth,
        "ai": settings.rate_limit_ai,
        "key_check": settings.rate_limit_key_check,
        "search": settings.rate_limit_search,
        "test_send": settings.rate_limit_test_send,
        "chat": settings.rate_limit_chat,
        "support": settings.rate_limit_support,
        "quote": settings.rate_limit_quote,
    }.get(group, 0)


def check(group: str, identity: str) -> tuple[bool, int]:
    """Разрешён ли запрос. Возвращает (allowed, retry_after_seconds).

    retry_after — сколько секунд ждать до освобождения слота (0, если разрешено).
    """
    if not settings.rate_limit_enabled:
        return True, 0

    limit = _limit_for(group)
    if limit <= 0:
        return True, 0

    window = max(1, settings.rate_limit_window_seconds)
    now = time.time()
    cutoff = now - window
    key = f"{group}:{identity}"

    global _calls_since_sweep
    with _lock:
        _calls_since_sweep += 1
        if _calls_since_sweep >= _SWEEP_EVERY:
            _calls_since_sweep = 0
            _sweep(cutoff)
        hits = [t for t in _hits.get(key, ()) if t > cutoff]
        if len(hits) >= limit:
            retry = int(hits[0] + window - now) + 1
            _hits[key] = hits
            return False, max(1, retry)
        hits.append(now)
        _hits[key] = hits
        return True, 0


def reset() -> None:
    """Сбросить все счётчики (используется в тестах)."""
    with _lock:
        _hits.clear()


def _client_ip(request: Request) -> str:
    """IP клиента с учётом обратного прокси.

    Было: брался ПЕРВЫЙ адрес из X-Forwarded-For. Это дыра именно здесь:
    заголовок полностью контролирует клиент, то есть любой желающий мог
    менять X-Forwarded-For на каждом запросе и получать свежий счётчик —
    ограничение частоты на входе не стоило ничего.

    Теперь используем общий access.client_ip: он отсчитывает адрес с конца
    цепочки по TRUSTED_PROXY_HOPS. Одна реализация на весь проект — иначе
    журнал входов и rate limit видели бы разные IP для одного и того же
    запроса.
    """
    return client_ip(request) or "unknown"


def limiter(group: str):
    """FastAPI-зависимость: ограничивает частоту по IP для группы эндпоинтов.

    Идентификатор — IP (по X-Forwarded-For, если за прокси). Для авторизации
    пользователь ещё не известен, поэтому IP — единственный доступный ключ; для
    остальных групп он тоже достаточно надёжен и не требует БД.
    """

    def _dep(request: Request) -> None:
        allowed, retry = check(group, _client_ip(request))
        if not allowed:
            raise HTTPException(
                429,
                f"Слишком много запросов. Повторите через {retry} с.",
                headers={"Retry-After": str(retry)},
            )

    return _dep
