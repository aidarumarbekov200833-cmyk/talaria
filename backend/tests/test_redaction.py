"""Central secret-redaction contracts at logging and error sinks."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import sys
import time
from types import SimpleNamespace
from urllib.parse import quote, quote_plus

from fastapi.exceptions import RequestValidationError
from starlette.requests import Request


def _request(path: str = "/redaction", query: bytes = b"") -> Request:
    return Request(
        {
            "type": "http",
            "method": "GET",
            "scheme": "http",
            "path": path,
            "raw_path": path.encode(),
            "query_string": query,
            "headers": [],
            "client": ("testclient", 50000),
            "server": ("testserver", 80),
        }
    )


def test_redaction_module_exists_and_is_dependency_free() -> None:
    from pathlib import Path

    path = Path(__file__).resolve().parents[1] / "app" / "redaction.py"
    assert path.exists(), "RED: centralized redaction module is missing"
    source = path.read_text(encoding="utf-8").lower()
    assert "from .config" not in source
    assert "from .logging" not in source
    assert "from .alerts" not in source
    assert "from .main" not in source


def test_registered_raw_quoted_plus_and_repeated_values_are_removed() -> None:
    from app.redaction import REDACTED, redact_text, register_secret

    secret = "registered secret+/=?&"
    register_secret(secret)
    encoded = quote(secret, safe="")
    plus = quote_plus(secret)
    output = redact_text(f"{secret}|{encoded}|{plus}|again:{secret}")
    assert secret not in output
    assert encoded not in output
    assert plus not in output
    assert output.count(REDACTED) >= 4


def test_recursive_sensitive_keys_bytes_exceptions_cycles_and_false_positives() -> None:
    from app.redaction import REDACTED, redact_value, register_secret

    secret = "recursive-secret-sentinel"
    register_secret(secret)

    class ReprValue:
        def __repr__(self) -> str:
            return f"ReprValue({secret})"

    class BrokenRepr:
        def __repr__(self) -> str:
            raise RuntimeError(secret)

    cycle: dict[str, object] = {"safe": "value"}
    cycle["self"] = cycle
    value = {
        "api-key": secret,
        "Authorization": f"Bearer {secret}",
        "monkey": "banana",
        "session_status": "active",
        "nested": [secret.encode(), ValueError(secret), ReprValue(), BrokenRepr(), cycle],
    }
    safe = redact_value(value)
    serialized = repr(safe)
    assert secret not in serialized
    assert safe["api-key"] == REDACTED
    assert safe["Authorization"] == REDACTED
    assert safe["monkey"] == "banana"
    assert safe["session_status"] == "active"
    assert safe["nested"][-1]["self"] == REDACTED


def test_known_token_patterns_are_redacted_without_generic_base64_masking() -> None:
    from app.redaction import REDACTED, redact_text

    jwt = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.signature_value"
    jwe = "eyJheader.part_two.part_three.part_four.part_five"
    bot = "123456789:AAAbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
    google = "AIzaSyA1234567890abcdefghijklmnopqrst"
    telegram_session = "1" + "A" * 110
    generic_base64 = "B" * 120
    text = (
        f"Authorization: Bearer bearer-token-value\n"
        f"jwt={jwt}\n"
        f"jwe={jwe}\n"
        f"bot={bot}\n"
        f"url=https://api.telegram.org/bot{bot}/sendMessage\n"
        f"google={google}\n"
        f"session={telegram_session}\n"
        f"StringSession({telegram_session})\n"
        f"access_token=query-secret&monkey=banana&session_status=active\n"
        f"plain={generic_base64}"
    )
    safe = redact_text(text)
    for secret in ("bearer-token-value", jwt, jwe, bot, google, telegram_session, "query-secret"):
        assert secret not in safe
    assert REDACTED in safe
    assert "monkey=banana" in safe
    assert "session_status=active" in safe
    assert generic_base64 in safe


def test_json_and_text_formatters_sanitize_message_extras_and_traceback(monkeypatch) -> None:
    from app import logging_setup
    from app.redaction import REDACTED, register_secret

    secret = "formatter-secret-sentinel"
    register_secret(secret)
    try:
        raise ValueError(f"trace {secret}")
    except ValueError:
        exc_info = sys.exc_info()
    record = logging.LogRecord(
        "test.redaction",
        logging.ERROR,
        __file__,
        1,
        "message %s",
        (secret,),
        exc_info,
    )
    record.request_id = "redaction-rid"
    record.user_id = 7
    record.payload = {"api_key": secret, "safe": "kept"}

    rendered = logging_setup.JsonFormatter().format(record)
    payload = json.loads(rendered)
    assert secret not in rendered
    assert payload["request_id"] == "redaction-rid"
    assert payload["payload"] == {"api_key": REDACTED, "safe": "kept"}
    assert REDACTED in payload["msg"]
    assert REDACTED in payload["exc"]

    text = logging_setup.TextFormatter().format(record)
    assert secret not in text
    assert "redaction-rid" in text

    monkeypatch.setattr(
        logging_setup, "redact_value", lambda _value: (_ for _ in ()).throw(RuntimeError(secret))
    )
    failed_json = logging_setup.JsonFormatter().format(record)
    assert secret not in failed_json
    assert json.loads(failed_json)["msg"] == "redaction_failed"

    monkeypatch.setattr(
        logging_setup, "redact_text", lambda _value: (_ for _ in ()).throw(RuntimeError(secret))
    )
    failed_text = logging_setup.TextFormatter().format(record)
    assert failed_text == "redaction_failed"


def test_alerts_sanitize_body_dedupe_details_and_transport_failure_log(monkeypatch, caplog) -> None:
    from app import alerts, logging_setup
    from app.redaction import register_secret

    secret = "alert-secret-sentinel"
    bot_token = "987654321:AAAccccccccccccccccccccccccccccccc"
    register_secret(secret)
    monkeypatch.setattr(alerts.settings, "telegram_alerts_enabled", True)
    monkeypatch.setattr(alerts.settings, "bot_token", bot_token)
    monkeypatch.setattr(alerts.settings, "admin_telegram_ids", "12345")
    captured_keys: list[str] = []
    monkeypatch.setattr(alerts, "_throttled", lambda key: (captured_keys.append(key) or False))
    posts: list[tuple[str, dict]] = []

    class Client:
        def __init__(self, **_kwargs: object) -> None:
            pass

        def __enter__(self) -> Client:
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def post(self, url: str, json: dict) -> None:
            posts.append((url, json))

    monkeypatch.setattr(alerts.httpx, "Client", Client)
    original_send_alert = alerts.send_alert
    alerts.send_alert(f"body {secret}", dedupe_key=f"key {secret}")
    assert secret not in captured_keys[0]
    assert secret not in posts[0][1]["text"]

    reported: list[tuple[str, str | None]] = []
    monkeypatch.setattr(
        alerts,
        "send_alert",
        lambda text, *, dedupe_key=None: reported.append((text, dedupe_key)),
    )
    alerts.report_error(
        f"module {secret}",
        "http",
        f"description {secret}",
        exc=RuntimeError(secret),
        entity=f"entity {secret}",
        details={"api_key": secret, "safe": f"text {secret}"},
        dedupe_key=f"dedupe {secret}",
    )
    assert secret not in repr(reported)

    class FailingClient(Client):
        def post(self, url: str, json: dict) -> None:
            raise RuntimeError(f"transport failed at {url}")

    monkeypatch.setattr(alerts, "send_alert", original_send_alert)
    monkeypatch.setattr(alerts.httpx, "Client", FailingClient)
    caplog.set_level(logging.WARNING, logger="outly.alerts")
    alerts.send_alert("failure", dedupe_key="transport-failure")
    record = caplog.records[-1]
    rendered_failure = logging_setup.JsonFormatter().format(record)
    assert bot_token not in rendered_failure


def test_http_and_validation_error_sinks_redact_secrets_but_keep_envelopes(
    monkeypatch, caplog
) -> None:
    from fastapi.testclient import TestClient

    from app import main
    from app.database import get_db
    from app.redaction import REDACTED, register_secret
    from app.services.errors import DomainError

    secret = "http-secret-sentinel"
    register_secret(secret)
    alert_calls: list[tuple[tuple[object, ...], dict[str, object]]] = []
    monkeypatch.setattr(
        main.alerts,
        "report_error",
        lambda *args, **kwargs: alert_calls.append((args, kwargs)),
    )
    previous = dict(main.app.dependency_overrides)

    def fail_dependency() -> None:
        raise DomainError(503, {"api_key": secret, "message": f"bad {secret}"})

    try:
        main.app.dependency_overrides[get_db] = fail_dependency
        caplog.set_level(logging.ERROR, logger="outly")
        response = TestClient(main.app).get(
            f"/api/public/stats?access_token={secret}",
            headers={"X-Request-Id": "redaction-http"},
        )
    finally:
        main.app.dependency_overrides.clear()
        main.app.dependency_overrides.update(previous)

    assert response.status_code == 503
    assert response.json() == {
        "detail": {"api_key": REDACTED, "message": f"bad {REDACTED}"},
        "request_id": "redaction-http",
    }
    assert secret not in caplog.text
    assert secret not in repr(alert_calls)

    validation = RequestValidationError(
        [
            {
                "type": "value_error",
                "loc": ("body", f"field-{secret}"),
                "msg": f"invalid {secret}",
                "input": secret,
                "ctx": {"error": ValueError(secret)},
            }
        ]
    )
    response = asyncio.run(main.validation_exception_handler(_request(), validation))
    body = json.loads(response.body)
    assert secret not in json.dumps(body)
    assert body["errors"] == [{"field": f"field-{REDACTED}", "message": f"invalid {REDACTED}"}]

    ordinary = TestClient(main.app).get(
        "/api/definitely-missing",
        headers={"X-Request-Id": "ordinary-404"},
    )
    assert ordinary.status_code == 404
    assert ordinary.json() == {"detail": "Not Found", "request_id": "ordinary-404"}


def test_sentry_hooks_and_lifespan_secret_registration(monkeypatch) -> None:
    from app import main
    from app.redaction import REDACTED, register_secret

    secret = "sentry-secret-sentinel"
    register_secret(secret)
    captured: dict[str, object] = {}
    fake = SimpleNamespace(init=lambda **kwargs: captured.update(kwargs))
    monkeypatch.setitem(sys.modules, "sentry_sdk", fake)
    monkeypatch.setattr(main.settings, "sentry_dsn", f"https://public:{secret}@sentry.test/1")
    main._init_sentry()
    assert captured["send_default_pii"] is False
    assert captured["include_local_variables"] is False
    assert captured["max_request_body_size"] == "never"
    event = captured["before_send"]({"extra": {"api_key": secret}}, {})
    breadcrumb = captured["before_breadcrumb"]({"message": secret}, {})
    assert event == {"extra": {"api_key": REDACTED}}
    assert breadcrumb == {"message": REDACTED}

    registered: list[object] = []
    monkeypatch.setattr(main, "register_secret", lambda *values: registered.extend(values))
    monkeypatch.setattr(
        main.settings,
        "database_url",
        "postgresql+psycopg2://zephyr:db-password-secret@db:5432/zephyr",
    )
    monkeypatch.setattr(main.settings, "bot_token", "bot-secret")
    monkeypatch.setattr(main.settings, "jwt_secret", "jwt-secret")
    monkeypatch.setattr(main.settings, "tg_client_secret", "gateway-secret")
    monkeypatch.setattr(main.settings, "ai_api_key", "ai-secret")
    monkeypatch.setattr(main.settings, "metrics_token", "metrics-secret")
    main._register_configured_secrets()
    for value in (
        "bot-secret",
        "jwt-secret",
        "gateway-secret",
        "ai-secret",
        "metrics-secret",
        "db-password-secret",
    ):
        assert value in registered


def test_auth_token_and_account_ticket_success_responses_remain_usable_and_unlogged(
    client, db, user, auth_headers, monkeypatch, caplog
) -> None:
    from app.config import settings

    payload = {
        "id": user.telegram_id,
        "first_name": user.first_name,
        "username": user.username,
        "auth_date": int(time.time()),
    }
    pairs = sorted(f"{key}={value}" for key, value in payload.items())
    bot_secret = hashlib.sha256(settings.bot_token.encode()).digest()
    payload["hash"] = hmac.new(
        bot_secret,
        "\n".join(pairs).encode(),
        hashlib.sha256,
    ).hexdigest()
    caplog.set_level(logging.INFO)
    auth = client.post("/api/auth/telegram", json=payload)
    assert auth.status_code == 200, auth.text
    token = auth.json()["access_token"]
    assert token
    assert client.get("/api/me", headers={"Authorization": f"Bearer {token}"}).status_code == 200
    assert token not in caplog.text

    ticket_secret = "ticket-success-secret"
    monkeypatch.setattr(settings, "tg_client_secret", ticket_secret)
    ticket_response = client.get("/api/telegram-accounts/ticket", headers=auth_headers)
    assert ticket_response.status_code == 200, ticket_response.text
    ticket = ticket_response.json()["ticket"]
    payload_text, signature = ticket.rsplit(".", 1)
    expected = hmac.new(ticket_secret.encode(), payload_text.encode(), hashlib.sha256).hexdigest()
    assert hmac.compare_digest(signature, expected)
    assert ticket not in caplog.text
