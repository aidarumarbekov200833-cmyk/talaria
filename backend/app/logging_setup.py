"""Структурное логирование и сквозной request-id.

Зачем: раньше лог был одной строкой «GET /api/leads -> 200 12ms» без привязки к
пользователю и без идентификатора. При разборе инцидента невозможно было
собрать все записи по одному запросу или по одному человеку.

Здесь: contextvars хранят request_id и user_id на время запроса, а форматтер
подмешивает их в КАЖДУЮ запись — включая логи из сервисов и трейсбеки.
Зависимостей не добавляем: всё на stdlib logging + json.
"""

from __future__ import annotations

import contextvars
import json
import logging
import sys
import uuid

from .redaction import redact_text, redact_value

# Сквозные поля запроса. Заполняются в middleware, читаются форматтером.
request_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("request_id", default="")

# Кто выполняет запрос. Хранится в СЛОВАРЕ, а не прямо в contextvars, и это не
# украшательство: синхронные зависимости и обработчики FastAPI выполняются в
# разных потоках пула, каждый со своей КОПИЕЙ контекста. Присваивание внутри
# зависимости в такой копии и остаётся — из-за этого в каждой строке лога стоял
# user_id=None, хотя код выглядел рабочим. Словарь middleware создаёт один раз
# на запрос, а зависимость меняет его содержимое: сам объект общий для всех
# копий контекста, поэтому изменение видно везде.
# Значение по умолчанию — None, а не пустой словарь: общий изменяемый объект
# по умолчанию протёк бы между запросами, которые идут мимо middleware.
_user_box: contextvars.ContextVar[dict | None] = contextvars.ContextVar("user_box", default=None)


def begin_request_user() -> contextvars.Token:
    """Завести хранилище на запрос. Вызывает middleware, возвращает токен сброса."""
    return _user_box.set({"id": None})


def end_request_user(token: contextvars.Token) -> None:
    _user_box.reset(token)


def set_current_user_id(user_id: int | None) -> None:
    """Запомнить, кто выполняет текущий запрос.

    Вне запроса (фоновые задачи, тесты модулей) хранилища нет — и это нормально:
    записывать такое действие не на кого.
    """
    box = _user_box.get()
    if box is not None:
        box["id"] = user_id


def current_user_id() -> int | None:
    box = _user_box.get()
    return box.get("id") if box else None

# Стандартные атрибуты LogRecord — всё остальное считаем «дополнительным»
# и выводим в JSON как есть (logger.info("...", extra={"lead_id": 5})).
_STD_ATTRS = frozenset(logging.LogRecord("", 0, "", 0, "", None, None).__dict__.keys()) | {
    "message",
    "asctime",
    "taskName",
}


def new_request_id() -> str:
    """Короткий идентификатор запроса — его удобно назвать в поддержке."""
    return uuid.uuid4().hex[:12]


class ContextFilter(logging.Filter):
    """Прокидывает request_id/user_id из contextvars в каждую запись."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id_var.get() or "-"
        record.user_id = current_user_id()
        return True


class JsonFormatter(logging.Formatter):
    """Одна строка = один JSON-объект. Готово для Loki/CloudWatch/jq."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
            "request_id": getattr(record, "request_id", "-"),
        }
        user_id = getattr(record, "user_id", None)
        if user_id is not None:
            payload["user_id"] = user_id
        for key, value in record.__dict__.items():
            if key in _STD_ATTRS or key in payload:
                continue
            try:
                json.dumps(value)
            except (TypeError, ValueError):
                value = repr(value)
            payload[key] = value
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        try:
            safe_payload = redact_value(payload)
            if not isinstance(safe_payload, dict):
                raise TypeError("redaction returned a non-mapping payload")
            return json.dumps(safe_payload, ensure_ascii=False)
        except Exception:
            return json.dumps(
                {"level": "ERROR", "msg": "redaction_failed", "request_id": "-"},
                ensure_ascii=False,
            )


class TextFormatter(logging.Formatter):
    """Человекочитаемый вариант для локального запуска (LOG_JSON=0)."""

    def __init__(self) -> None:
        super().__init__(fmt="%(asctime)s %(levelname)-7s %(name)s [%(request_id)s] %(message)s")

    def format(self, record: logging.LogRecord) -> str:
        try:
            return redact_text(super().format(record))
        except Exception:
            return "redaction_failed"


def setup_logging(*, json_output: bool = True, level: str = "INFO") -> None:
    """Настроить корневой логгер один раз при старте приложения."""
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter() if json_output else TextFormatter())
    handler.addFilter(ContextFilter())

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level.upper())

    # uvicorn ставит свои обработчики — забираем его логи себе, чтобы формат
    # был единым. uvicorn.access глушим: свой access-лог богаче (есть
    # request_id, user_id, длительность), дубль только мешает.
    for name in ("uvicorn", "uvicorn.error"):
        log = logging.getLogger(name)
        log.handlers.clear()
        log.propagate = True
    access = logging.getLogger("uvicorn.access")
    access.handlers.clear()
    access.propagate = False
