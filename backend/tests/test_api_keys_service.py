"""Characterization tests for Task 3B4 API-key extraction."""

from __future__ import annotations

import inspect


def test_api_key_repository_and_service_have_layer_boundaries() -> None:
    from app.repositories import api_keys as repository
    from app.services import api_keys as service

    repository_source = inspect.getsource(repository).lower()
    for forbidden in (".commit(", ".rollback(", ".refresh(", "fastapi", "httpx", "record_event"):
        assert forbidden not in repository_source

    service_source = inspect.getsource(service).lower()
    for forbidden in ("fastapi", "starlette"):
        assert forbidden not in service_source


def test_api_key_routes_delegate_without_touching_the_session(monkeypatch) -> None:
    from types import SimpleNamespace

    from app.routers import settings

    class PoisonedSession:
        def __getattr__(self, name: str) -> object:
            if name in {"query", "add", "delete", "flush", "commit", "rollback", "refresh"}:
                raise AssertionError(f"router touched session.{name}")
            raise AttributeError(name)

    db = PoisonedSession()
    user = SimpleNamespace(id=77)
    result = object()
    monkeypatch.setattr(settings, "list_api_keys_service", lambda *_args, **_kwargs: result)
    monkeypatch.setattr(settings, "read_quota_service", lambda *_args, **_kwargs: result)

    assert settings.list_keys(user, db) is result
    assert settings.read_quota(user, db) is result
