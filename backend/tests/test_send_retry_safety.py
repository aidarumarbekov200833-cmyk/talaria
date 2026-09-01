"""Повтор отправки не имеет права дублировать сообщение лиду.

Ревью 2026-09-01: send_via_client повторял отправку при любом httpx.RequestError,
включая таймаут чтения (шлюзу даётся 45 с). К моменту таймаута сообщение могло
уже уйти в Telegram — живой человек получал два одинаковых первых сообщения.
Теперь транспортные ошибки делятся на «точно не дошло» (GATEWAY_UNREACHABLE,
повтор безопасен) и «исход неизвестен» (GATEWAY_INTERRUPTED, повтор запрещён).
"""

from __future__ import annotations

import httpx
import pytest

from app import sending
from app.telegram_gateway import TelegramGateway, TelegramGatewayError


def test_uncertain_outcome_is_never_retried(monkeypatch):
    calls = []

    def interrupted(*_args, **_kwargs) -> None:
        calls.append(1)
        raise TelegramGatewayError("Ответ Telegram-gateway не получен", code="GATEWAY_INTERRUPTED")

    monkeypatch.setattr(sending.gateway, "send", interrupted)
    outcome = sending.send_via_client(1, 1, "lead", "привет")
    assert outcome.ok is False
    assert outcome.error_code == "SEND_UNCERTAIN"
    assert len(calls) == 1, "повтор после таймаута — это дубль сообщения лиду"


def test_unreachable_gateway_is_retried_once(monkeypatch):
    calls = []

    def unreachable(*_args, **_kwargs) -> None:
        calls.append(1)
        raise TelegramGatewayError("Telegram-gateway недоступен", code="GATEWAY_UNREACHABLE")

    monkeypatch.setattr(sending.gateway, "send", unreachable)
    monkeypatch.setattr(sending.time, "sleep", lambda _s: None)
    outcome = sending.send_via_client(1, 1, "lead", "привет")
    assert outcome.ok is False
    assert len(calls) == 2, "запрос точно не дошёл — один повтор безопасен и нужен"


def test_gateway_maps_read_timeout_to_interrupted(monkeypatch):
    gateway = TelegramGateway(base_url="http://web-tg:3000", secret="x" * 40)

    def timeout(*_args, **_kwargs) -> None:
        raise httpx.ReadTimeout("timed out")

    monkeypatch.setattr(httpx, "post", timeout)
    with pytest.raises(TelegramGatewayError) as err:
        gateway.send(1, 1, "lead", "привет")
    assert err.value.code == "GATEWAY_INTERRUPTED"


def test_gateway_maps_connect_error_to_unreachable(monkeypatch):
    gateway = TelegramGateway(base_url="http://web-tg:3000", secret="x" * 40)

    def refused(*_args, **_kwargs) -> None:
        raise httpx.ConnectError("connection refused")

    monkeypatch.setattr(httpx, "post", refused)
    with pytest.raises(TelegramGatewayError) as err:
        gateway.send(1, 1, "lead", "привет")
    assert err.value.code == "GATEWAY_UNREACHABLE"
