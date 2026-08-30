"""Алерты в Telegram при критических ошибках (5xx).

Использует того же бота (BOT_TOKEN) и тех же админов (ADMIN_TELEGRAM_IDS),
что и вход — отдельный бот или чат заводить не нужно. Новых зависимостей
не добавляет: для HTTP-запроса используется уже подключённый httpx.

Дедупликация (throttling) обязательна: без неё шторм одинаковых 500-к на горячем
эндпоинте (например, упавшая база данных) за секунды засыпал бы чат админа
сотней одинаковых сообщений.
"""

from __future__ import annotations

import logging
import threading
import time

import httpx

from .alert_policy import CLIENT, classify
from .config import settings
from .redaction import redact_text, redact_value, register_secret

logger = logging.getLogger("outly.alerts")

_lock = threading.Lock()
_last_sent: dict[str, float] = {}


def _throttled(key: str) -> bool:
    """True, если такой же алерт уже уходил недавно — отправлять повторно не надо."""
    interval = max(1, settings.telegram_alert_min_interval_seconds)
    now = time.time()
    with _lock:
        last = _last_sent.get(key, 0.0)
        if now - last < interval:
            return True
        _last_sent[key] = now
    return False


def send_alert(text: str, *, dedupe_key: str | None = None) -> None:
    """Отправить сообщение всем админам в Telegram. Тихо глотает любые сбои.

    Алерт не должен уронить обработку ошибки, которую он же и оповещает, поэтому
    любое исключение здесь только логируется.
    """
    if not settings.telegram_alerts_enabled:
        return
    if not settings.bot_token or not settings.admin_ids:
        return

    register_secret(settings.bot_token)
    safe_text = redact_text(text)
    safe_key = redact_text(dedupe_key if dedupe_key is not None else safe_text)
    if _throttled(safe_key):
        return

    body = safe_text[:3900]
    try:
        with httpx.Client(timeout=5.0) as client:
            for chat_id in settings.admin_ids:
                client.post(
                    f"https://api.telegram.org/bot{settings.bot_token}/sendMessage",
                    json={"chat_id": chat_id, "text": body},
                )
    except Exception:  # noqa: BLE001 — алерт не должен ломать обработку ошибки
        logger.warning("Не удалось отправить алерт в Telegram", exc_info=True)


def notify_user(
    telegram_id: int | str | None, text: str, *, dedupe_key: str | None = None
) -> bool:
    """Личное уведомление пользователю через того же бота.

    Отличается от send_alert адресатом: не админам, а конкретному человеку —
    например, владельцу рассылки, когда его лид ответил. Telegram позволяет
    боту писать только тем, кто хоть раз ему писал (нажал Start); если человек
    этого не делал — Telegram вернёт 403, и мы молча логируем: уведомление
    вторично, ронять из-за него обработку ответа нельзя.
    """
    if not settings.telegram_alerts_enabled:
        return False
    if not settings.bot_token or not telegram_id:
        return False
    register_secret(settings.bot_token)
    safe_text = redact_text(text)
    safe_key = redact_text(dedupe_key or f"user:{telegram_id}:{safe_text[:60]}")
    if _throttled(safe_key):
        return False
    try:
        with httpx.Client(timeout=5.0) as client:
            response = client.post(
                f"https://api.telegram.org/bot{settings.bot_token}/sendMessage",
                json={"chat_id": int(telegram_id), "text": safe_text[:3900]},
            )
    except Exception:  # noqa: BLE001 — уведомление не должно ломать обработку ответа
        logger.warning(
            "Не удалось отправить уведомление пользователю %s", telegram_id, exc_info=True
        )
        return False
    # Telegram отвечает 200 с ok=false на понятные отказы (человек не нажал
    # Start, заблокировал бота). Молча считать это доставкой нельзя: именно так
    # уведомления и теряются незаметно.
    if response.status_code != 200:
        logger.warning(
            "Telegram отказал в уведомлении пользователю %s: HTTP %s",
            telegram_id,
            response.status_code,
        )
        return False
    try:
        delivered = bool(response.json().get("ok"))
    except ValueError:
        delivered = False
    if not delivered:
        logger.warning("Telegram не принял уведомление пользователю %s", telegram_id)
    return delivered


# --------------------------- подробные алерты об ошибках ---------------------------

# Почему отдельная функция, а не send_alert("500"): админу бесполезно сообщение
# вида «Error 500». Нужно сразу понять: какой модуль, какой этап, что именно
# сломалось, у кого и что делать. Поэтому собираем структурное письмо.

_STAGE_HINTS = {
    "send": "\u041f\u0440\u043e\u0432\u0435\u0440\u044c\u0442\u0435, \u0436\u0438\u0432 \u043b\u0438 tg-mini-client \u0438 \u043d\u0435 \u0441\u043b\u0435\u0442\u0435\u043b\u0430 \u043b\u0438 \u0441\u0435\u0441\u0441\u0438\u044f \u0430\u043a\u043a\u0430\u0443\u043d\u0442\u0430.",
    "ai": "\u041f\u0440\u043e\u0432\u0435\u0440\u044c\u0442\u0435 \u043a\u043b\u044e\u0447 \u0438 \u0431\u0430\u043b\u0430\u043d\u0441 \u0443 \u043f\u0440\u043e\u0432\u0430\u0439\u0434\u0435\u0440\u0430 \u0418\u0418 \u0432 \u043d\u0430\u0441\u0442\u0440\u043e\u0439\u043a\u0430\u0445.",
    "search": "\u041f\u0440\u043e\u0432\u0435\u0440\u044c\u0442\u0435 \u043a\u0432\u043e\u0442\u0443 \u043a\u043b\u044e\u0447\u0435\u0439 YouTube API \u0432 \u043d\u0430\u0441\u0442\u0440\u043e\u0439\u043a\u0430\u0445.",
    "contacts": "\u0414\u043e\u0441\u0442\u0440\u043e\u0439\u043a\u0430 \u043a\u043e\u043d\u0442\u0430\u043a\u0442\u043e\u0432 \u043d\u0435\u043e\u0431\u044f\u0437\u0430\u0442\u0435\u043b\u044c\u043d\u0430\u044f: \u043b\u0438\u0434\u044b \u0441\u043e\u0445\u0440\u0430\u043d\u0435\u043d\u044b, \u043d\u043e \u0431\u0435\u0437 Telegram.",
    "http": "\u041f\u043e\u0441\u043c\u043e\u0442\u0440\u0438\u0442\u0435 \u0442\u0440\u0435\u0439\u0441\u0431\u0435\u043a \u0432 \u043b\u043e\u0433\u0430\u0445 \u0431\u044d\u043a\u0435\u043d\u0434\u0430 \u043f\u043e \u0443\u043a\u0430\u0437\u0430\u043d\u043d\u043e\u043c\u0443 \u0437\u0430\u043f\u0440\u043e\u0441\u0443.",
    "warmup": "\u041f\u0440\u043e\u0432\u0435\u0440\u044c\u0442\u0435 \u043f\u043e\u0434\u043a\u043b\u044e\u0447\u0451\u043d\u043d\u044b\u0435 \u0430\u043a\u043a\u0430\u0443\u043d\u0442\u044b \u0432 \u043d\u0430\u0441\u0442\u0440\u043e\u0439\u043a\u0430\u0445.",
}


def report_error(
    module: str,
    stage: str,
    description: str,
    *,
    exc: BaseException | None = None,
    owner_id: int | None = None,
    entity: str | None = None,
    details: dict | None = None,
    dedupe_key: str | None = None,
) -> None:
    """Подробный алерт админу: где, на каком этапе и что именно сломалось.

    module      — где упало человеческими словами: «Рассылка», «Поиск лидов».
    stage       — этап внутри модуля: «отправка сообщения», «генерация AI-оффера».
    description — что произошло, тоже словами, а не кодом.
    entity      — конкретный объект: «рассылка #12 «Avgust»», «лид @nick».
    details     — любые дополнительные поля, будут выведены списком.

    Ключ дедупликации по умолчанию — модуль+этап+класс исключения, а не весь
    текст: иначе тысяча падений по разным лидам дала бы тысячу сообщений.
    """
    # Фильтр перед отправкой. Владелец просил: сообщение в Telegram — только
    # когда понятно, где ошибка или что делать, и она мешает работе сайта.
    # Всё остальное остаётся в логах контейнера (`docker compose logs backend`).
    verdict = classify(module, stage, description, exc, entity)
    if not verdict.notify:
        logger.error(
            "Ошибка без уведомления (%s). Модуль: %s | Этап: %s | %s%s",
            verdict.reason,
            module,
            stage,
            description,
            f" | Объект: {entity}" if entity else "",
            exc_info=exc if exc is not None else False,
        )
        return

    module = redact_text(module)
    stage = redact_text(stage)
    description = redact_text(description)
    entity = redact_text(entity) if entity else None
    safe_details = redact_value(details)
    lines = [
        "\u26a0\ufe0f \u041e\u0448\u0438\u0431\u043a\u0430 \u0432 Talaria",
        "",
        f"\u041c\u043e\u0434\u0443\u043b\u044c: {module}",
        f"\u042d\u0442\u0430\u043f: {stage}",
        f"\u0427\u0442\u043e \u0441\u043b\u0443\u0447\u0438\u043b\u043e\u0441\u044c: {description}",
    ]
    if entity:
        lines.append(f"\u041e\u0431\u044a\u0435\u043a\u0442: {entity}")
    if owner_id is not None:
        lines.append(
            f"\u041f\u043e\u043b\u044c\u0437\u043e\u0432\u0430\u0442\u0435\u043b\u044c: id {owner_id}"
        )
    if exc is not None:
        lines.append(
            f"\u0422\u0438\u043f \u043e\u0448\u0438\u0431\u043a\u0438: {type(exc).__name__}"
        )
        text = redact_text(exc).strip()
        if text:
            lines.append(f"\u0421\u043e\u043e\u0431\u0449\u0435\u043d\u0438\u0435: {text[:600]}")
    if isinstance(safe_details, dict) and safe_details:
        lines.append("")
        lines.append("\u041f\u043e\u0434\u0440\u043e\u0431\u043d\u043e\u0441\u0442\u0438:")
        for key, value in safe_details.items():
            if value in (None, ""):
                continue
            lines.append(f"\u2022 {key}: {str(value)[:300]}")
    if verdict.where:
        lines.insert(3, f"\u0413\u0434\u0435: {verdict.where}")
    hint = (
        verdict.fix
        or _STAGE_HINTS.get((stage or "").strip().lower())
        or _STAGE_HINTS.get((module or "").strip().lower())
    )
    if hint:
        lines.append("")
        lines.append(f"\u0427\u0442\u043e \u0434\u0435\u043b\u0430\u0442\u044c: {hint}")
    if exc is not None:
        import traceback

        tb = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
        tail = redact_text(tb).strip().splitlines()[-6:]
        if tail:
            lines.append("")
            lines.append("\u0421\u043b\u0435\u0434 (\u043a\u043e\u043d\u0435\u0446):")
            lines.extend(tail)

    key = redact_text(
        dedupe_key or f"{module}|{stage}|{type(exc).__name__ if exc else description[:60]}"
    )
    body = redact_text("\n".join(lines))

    # Ключи YouTube и аккаунты Telegram принадлежат конкретному пользователю.
    # Владелец сервиса не может ни добавить чужой ключ, ни переподключить чужой
    # аккаунт — такое сообщение для него просто шум. Пишем тому, у кого сломалось.
    if verdict.audience == CLIENT:
        if owner_id is None:
            logger.error(
                "Поломка на стороне пользователя, но владелец неизвестен — только лог. %s",
                body.splitlines()[0] if body else "",
            )
            return
        notify_user(_telegram_id_of(owner_id), body, dedupe_key=f"client:{owner_id}:{key}")
        return

    send_alert(body, dedupe_key=key)


def notify_target_of(session, owner_id: int) -> int | None:
    """Куда слать личные уведомления владельцу: выбранный аккаунт или аккаунт входа.

    Отдельной функцией, а не строкой внутри вызова: одну и ту же развилку
    используют клиентские алерты, напоминание об окончании тарифа и
    предупреждение о квоте — расхождение между ними означало бы, что часть
    сообщений уходит не туда, куда человек их ждёт.
    """
    from .models import AppSettings, User

    chosen = (
        session.query(AppSettings.notify_target_tg_id).filter_by(owner_id=owner_id).scalar()
    )
    if chosen:
        return int(chosen)
    return session.query(User.telegram_id).filter(User.id == owner_id).scalar()


def _telegram_id_of(owner_id: int) -> int | None:
    """Telegram-адрес пользователя. Отдельной сессией: алерт вызывается из мест,
    где чужая транзакция может быть уже сломана, и переиспользовать её нельзя.

    Учитывает выбранный в настройках аккаунт для уведомлений: у человека может
    быть подключено несколько аккаунтов, а читать он хочет в одном. Раньше
    здесь всегда брался аккаунт входа, и выбор в настройках на алерты не влиял.
    """
    try:
        from .database import SessionLocal

        with SessionLocal() as session:
            return notify_target_of(session, owner_id)
    except Exception as exc:  # noqa: BLE001 - алерт не должен ронять обработку ошибки
        logger.error("Не смог найти Telegram пользователя %s: %s", owner_id, type(exc).__name__)
        return None
