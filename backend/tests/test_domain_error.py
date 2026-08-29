"""Contract tests for the framework-independent domain error boundary."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from fastapi.testclient import TestClient
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from app import main
from app.database import get_db
from app.services.errors import DomainError


@pytest.mark.parametrize("status_code", [400, 404, 409, 503])
def test_domain_error_from_overridden_dependency_keeps_http_error_contract(
    monkeypatch, status_code: int
) -> None:
    """A domain failure keeps the existing public envelope without a test route."""
    request_id = f"domain-{status_code}"
    detail = f"domain error {status_code}"
    headers = {"Retry-After": "30"} if status_code == 503 else None
    alert_calls: list[tuple[tuple[Any, ...], dict[str, Any]]] = []

    def capture_alert(*args: Any, **kwargs: Any) -> None:
        alert_calls.append((args, kwargs))

    monkeypatch.setattr(main.alerts, "report_error", capture_alert)
    previous_overrides = dict(main.app.dependency_overrides)

    def raise_domain_error() -> None:
        raise DomainError(status_code, detail, headers=headers)

    try:
        main.app.dependency_overrides[get_db] = raise_domain_error
        response = TestClient(main.app).get(
            "/api/public/stats",
            headers={"X-Request-Id": request_id},
        )
    finally:
        main.app.dependency_overrides.clear()
        main.app.dependency_overrides.update(previous_overrides)

    assert response.status_code == status_code
    assert response.json() == {"detail": detail, "request_id": request_id}
    assert response.headers["x-request-id"] == request_id
    if headers is not None:
        assert response.headers["retry-after"] == "30"
    assert len(alert_calls) == (1 if status_code == 503 else 0)


def test_domain_error_handler_delegates_once_to_existing_http_handler(monkeypatch) -> None:
    """The new handler adapts the error instead of reimplementing its policy."""
    request = Request(
        {
            "type": "http",
            "method": "GET",
            "scheme": "http",
            "path": "/domain-error-test",
            "raw_path": b"/domain-error-test",
            "query_string": b"",
            "headers": [],
            "client": ("testclient", 50000),
            "server": ("testserver", 80),
        }
    )
    calls: list[tuple[Request, Any]] = []

    async def existing_handler(received_request: Request, received_error: Any) -> Response:
        calls.append((received_request, received_error))
        return JSONResponse({"delegated": True})

    monkeypatch.setattr(main, "http_exception_handler", existing_handler)
    response = asyncio.run(
        main.domain_error_handler(
            request,
            DomainError(409, "duplicate", headers={"X-Domain": "kept"}),
        )
    )

    assert response.status_code == 200
    assert len(calls) == 1
    received_request, received_error = calls[0]
    assert received_request is request
    assert received_error.status_code == 409
    assert received_error.detail == "duplicate"
    assert received_error.headers == {"X-Domain": "kept"}


def test_domain_error_module_has_no_fastapi_or_starlette_dependency() -> None:
    """Domain services remain importable without the HTTP framework."""
    source = DomainError.__module__
    module_path = __import__(source, fromlist=["__file__"]).__file__
    assert module_path is not None
    module_source = open(module_path, encoding="utf-8").read().lower()
    assert "fastapi" not in module_source
    assert "starlette" not in module_source
