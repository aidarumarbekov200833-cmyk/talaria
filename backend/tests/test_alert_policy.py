"""Проверки политики уведомлений.

Смысл политики: в Telegram уходит только то, по чему владелец может действовать.
Тесты закрепляют оба направления — и что нужное доходит, и что шум не доходит.
"""

from __future__ import annotations

import pytest

from app.alert_policy import CLIENT, OWNER, classify


class FakeOperationalError(Exception):
    pass


@pytest.mark.parametrize(
    ("module", "stage", "description", "exc", "rule", "audience"),
    [
        # Сломалось у НАС — чинит владелец сервиса.
        (
            "API",
            "http",
            "Запрос упал",
            FakeOperationalError("could not connect to server: Connection refused"),
            "db-down",
            OWNER,
        ),
        ("Диск", "backup", "no space left on device", None, "disk-full", OWNER),
        (
            "ИИ",
            "ai",
            "Провайдер ответил insufficient balance",
            None,
            "ai-provider-auth",
            OWNER,
        ),
        # Сломалось у КЛИЕНТА — его ключ, его аккаунт. Владелец сервиса тут
        # бессилен, и сообщение ему было бы чистым шумом.
        ("Поиск лидов", "search", "YouTube вернул quotaExceeded", None, "youtube-quota", CLIENT),
        (
            "Рассылка",
            "send",
            "Telegram отклонил отправку",
            Exception("AuthKeyUnregistered"),
            "telegram-session-dead",
            CLIENT,
        ),
        ("Рассылка", "send", "FloodWait 3600 секунд", None, "telegram-flood", CLIENT),
    ],
)
def test_поломка_адресуется_тому_кто_может_починить(
    module, stage, description, exc, rule, audience
):
    verdict = classify(module, stage, description, exc)
    assert verdict.notify is True
    assert verdict.reason == rule
    assert verdict.audience == audience
    # Без места и решения сообщение бесполезно — правило обязано их дать.
    assert verdict.where
    assert verdict.fix


def test_владельцу_не_уходят_поломки_на_стороне_клиентов():
    """Главное разграничение: чужой ключ и чужой аккаунт — не забота владельца."""
    client_side = [
        classify("Поиск лидов", "search", "quotaExceeded"),
        classify("Поиск лидов", "search", "API key not valid"),
        classify("Рассылка", "send", "AuthKeyUnregistered"),
        classify("Рассылка", "send", "FloodWait 600"),
    ]
    for verdict in client_side:
        assert verdict.audience == CLIENT, verdict.reason
        assert verdict.audience != OWNER


def test_нераспознанная_ошибка_остаётся_в_логах():
    """Главный случай: непонятное падение больше не шлёт сообщение."""
    verdict = classify(
        "API",
        "http",
        "Необработанное исключение — запрос упал до ответа",
        KeyError("channel_id"),
    )
    assert verdict.notify is False
    assert "не распознана" in verdict.reason
    assert verdict.audience == "nobody"


def test_понятная_но_безобидная_ошибка_не_будит():
    """Дозагрузка контактов необязательна: лиды сохранены, будить незачем."""
    verdict = classify("Поиск лидов", "contacts", "Не нашли Telegram у части лидов")
    assert verdict.notify is False
    assert verdict.where  # место известно
    assert "не влияет" in verdict.reason


@pytest.mark.parametrize(
    "exc",
    [
        ConnectionResetError("Connection reset by peer"),
        BrokenPipeError("Broken pipe"),
    ],
)
def test_обрыв_соединения_с_клиентом_не_будит(exc):
    verdict = classify("API", "http", "Запрос оборвался", exc)
    assert verdict.notify is False
    assert "клиент" in verdict.reason


def test_регистр_и_язык_не_мешают_распознаванию():
    assert classify("api", "HTTP", "QUOTAEXCEEDED", None).notify is True
    assert classify("API", "search", "quotaexceeded", None).notify is True


def test_решение_описано_словами_а_не_кодом_ошибки():
    """Подсказка должна говорить, что делать руками, а не цитировать исключение."""
    verdict = classify("Поиск лидов", "search", "quotaExceeded", None)
    assert "Настройк" in verdict.fix or "ключ" in verdict.fix.lower()
    assert len(verdict.fix) > 40
