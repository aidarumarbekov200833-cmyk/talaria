"""Runtime contract for FastAPI's three public error envelopes."""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Never

from fastapi.testclient import TestClient
from starlette.requests import Request
from starlette.responses import Response

from app import logging_setup, main
from app.database import get_db
from app.logging_setup import ContextFilter


class _CapturingHandler(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


class _CapturingContextFilter(ContextFilter):
    def __init__(self) -> None:
        super().__init__()
        self.context_ids: list[str] = []

    def filter(self, record: logging.LogRecord) -> bool:
        self.context_ids.append(main.request_id_var.get() or "-")
        return super().filter(record)


def _snapshot(response, request_id: str) -> dict[str, object]:
    body = response.json()
    assert body["request_id"] == request_id
    assert response.headers["x-request-id"] == request_id
    body["request_id"] = "<request-id>"
    return {"status": response.status_code, "header_matches_request_id": True, "body": body}


def test_error_envelopes_match_runtime_snapshot(monkeypatch) -> None:
    monkeypatch.setattr(main.alerts, "report_error", lambda *args, **kwargs: None)
    previous = dict(main.app.dependency_overrides)

    def broken_db() -> Never:
        raise RuntimeError("contract database failure")

    assert main.request_id_var.get() == ""
    assert logging_setup.current_user_id() is None
    try:
        client = TestClient(main.app, raise_server_exceptions=False)
        normal = client.get("/api/health", headers={"X-Request-Id": "contract-normal"})
        http = client.get("/contract-missing", headers={"X-Request-Id": "contract-http"})
        validation = client.post(
            "/api/auth/telegram",
            json={"auth_date": 0, "hash": "contract"},
            headers={"X-Request-Id": "contract-validation"},
        )
        main.app.dependency_overrides[get_db] = broken_db
        unhandled = client.get("/api/public/stats", headers={"X-Request-Id": "contract-unhandled"})
    finally:
        main.app.dependency_overrides.clear()
        main.app.dependency_overrides.update(previous)

    assert normal.status_code == 200
    assert normal.headers["x-request-id"] == "contract-normal"
    assert main.request_id_var.get() == ""
    assert logging_setup.current_user_id() is None
    actual = {
        "http": _snapshot(http, "contract-http"),
        "validation": _snapshot(validation, "contract-validation"),
        "unhandled": _snapshot(unhandled, "contract-unhandled"),
    }
    expected = json.loads(
        (Path(__file__).parent / "error-envelope-contract.json").read_text(encoding="utf-8")
    )
    assert actual == expected


def test_request_context_resets_contextvars_after_response() -> None:
    async def exercise() -> None:
        request = Request(
            {
                "type": "http",
                "asgi": {"version": "3.0", "spec_version": "2.3"},
                "http_version": "1.1",
                "method": "GET",
                "scheme": "http",
                "path": "/api/health",
                "raw_path": b"/api/health",
                "query_string": b"",
                "headers": [(b"x-request-id", b"contract-context")],
                "client": ("testclient", 50000),
                "server": ("testserver", 80),
                "state": {},
            }
        )

        async def call_next(_request: Request) -> Response:
            assert main.request_id_var.get() == "contract-context"
            logging_setup.set_current_user_id(42)
            return Response()

        response = await main.request_context(request, call_next)

        assert response.headers["x-request-id"] == "contract-context"
        assert main.request_id_var.get() == ""
        assert logging_setup.current_user_id() is None

    asyncio.run(exercise())


def test_unhandled_exception_log_uses_response_request_id(monkeypatch) -> None:
    monkeypatch.setattr(main.alerts, "report_error", lambda *args, **kwargs: None)
    previous_overrides = dict(main.app.dependency_overrides)
    previous_level = main.logger.level
    handler = _CapturingHandler()
    context_filter = _CapturingContextFilter()
    handler.addFilter(context_filter)
    main.logger.addHandler(handler)
    main.logger.setLevel(logging.ERROR)

    def broken_db() -> Never:
        raise RuntimeError("contract log failure")

    try:
        main.app.dependency_overrides[get_db] = broken_db
        response = TestClient(main.app, raise_server_exceptions=False).get(
            "/api/public/stats",
            headers={"X-Request-Id": "contract-log"},
        )
    finally:
        main.app.dependency_overrides.clear()
        main.app.dependency_overrides.update(previous_overrides)
        main.logger.removeHandler(handler)
        main.logger.setLevel(previous_level)

    assert response.status_code == 500
    assert response.json()["request_id"] == "contract-log"
    assert response.headers["x-request-id"] == "contract-log"
    assert context_filter.context_ids == ["contract-log"]
    assert [record.request_id for record in handler.records] == ["contract-log"]
    assert main.request_id_var.get() == ""
