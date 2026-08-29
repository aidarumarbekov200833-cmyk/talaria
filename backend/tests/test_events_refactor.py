"""Characterization and boundary tests for the event journal slice."""

from __future__ import annotations

import inspect
import os
import subprocess
import sys
from pathlib import Path

import pytest


def test_events_endpoint_keeps_owner_scope_limit_and_old_to_new_order(
    client, db, user, auth_headers
) -> None:
    """The public list remains scoped, newest-windowed, then chronological."""
    from app.models import EventLog, User

    empty = client.get("/api/events", headers=auth_headers)
    assert empty.status_code == 200
    assert empty.json() == {"events": []}

    other = User(telegram_id=9_001, username="other")
    db.add(other)
    db.flush()
    db.add_all(
        [
            EventLog(owner_id=user.id, scope="leads", message="first"),
            EventLog(owner_id=user.id, scope="offers", message="ignored"),
            EventLog(owner_id=user.id, scope="leads", message="second"),
            EventLog(owner_id=user.id, scope="leads", message="third"),
            EventLog(owner_id=other.id, scope="leads", message="other owner"),
        ]
    )
    db.commit()

    all_events = client.get("/api/events", headers=auth_headers)
    filtered = client.get("/api/events?scope=leads&limit=2", headers=auth_headers)

    assert all_events.status_code == 200
    assert [event["message"] for event in all_events.json()["events"]] == [
        "first",
        "ignored",
        "second",
        "third",
    ]
    assert filtered.status_code == 200
    assert [event["message"] for event in filtered.json()["events"]] == ["second", "third"]


def test_event_repository_lists_newest_first_and_stages_and_flushes_writes(db, user) -> None:
    from app.models import EventLog
    from app.repositories.events import EventRepository

    db.add_all(
        [
            EventLog(owner_id=user.id, scope="leads", message="first"),
            EventLog(owner_id=user.id, scope="offers", message="ignored"),
            EventLog(owner_id=user.id, scope="leads", message="second"),
        ]
    )
    db.commit()
    repository = EventRepository(db)

    assert [row.message for row in repository.list_for_owner(user.id, "leads", 2)] == [
        "second",
        "first",
    ]
    staged = repository.stage(user.id, "ai", "draft", "success", "offer", 42)
    assert staged.id is not None
    assert staged not in db.new


def test_record_event_commits_refreshes_then_publishes(monkeypatch) -> None:
    from app.models import EventLog
    from app.services import events as events_service

    calls: list[str] = []
    row = EventLog(owner_id=7, scope="ai", message="done", level="success", ref_type="offer", ref_id=3)
    row.id = 11

    class RepositorySpy:
        def __init__(self, db) -> None:
            assert db is session

        def stage(self, *args) -> EventLog:
            assert args == (7, "ai", "done", "success", "offer", 3)
            calls.append("stage")
            return row

    class SessionSpy:
        def commit(self) -> None:
            calls.append("commit")

        def refresh(self, refreshed) -> None:
            assert refreshed is row
            calls.append("refresh")

    session = SessionSpy()
    monkeypatch.setattr(events_service, "EventRepository", RepositorySpy)
    monkeypatch.setattr(
        events_service.hub,
        "publish",
        lambda owner_id, payload: calls.append(f"publish:{owner_id}:{payload['event']['id']}"),
    )

    assert events_service.record_event(session, 7, "ai", "done", "success", "offer", 3) is row
    assert calls == ["stage", "commit", "refresh", "publish:7:11"]


def test_record_event_rolls_back_once_and_never_publishes_after_commit_failure(monkeypatch) -> None:
    from app.models import EventLog
    from app.services import events as events_service

    calls: list[str] = []
    failure = RuntimeError("database unavailable")

    class RepositorySpy:
        def __init__(self, db) -> None:
            assert db is session

        def stage(self, *args) -> EventLog:
            calls.append("stage")
            return EventLog(owner_id=7, scope="ai", message="failed")

    class SessionSpy:
        def commit(self) -> None:
            calls.append("commit")
            raise failure

        def rollback(self) -> None:
            calls.append("rollback")

        def refresh(self, refreshed) -> None:
            calls.append("refresh")

    session = SessionSpy()
    monkeypatch.setattr(events_service, "EventRepository", RepositorySpy)
    monkeypatch.setattr(events_service.hub, "publish", lambda *_args: calls.append("publish"))

    with pytest.raises(RuntimeError) as raised:
        events_service.record_event(session, 7, "ai", "failed")

    assert raised.value is failure
    assert calls == ["stage", "commit", "rollback"]


def test_record_event_rolls_back_once_and_never_publishes_after_stage_failure(monkeypatch) -> None:
    from app.services import events as events_service

    calls: list[str] = []
    failure = RuntimeError("flush failed")

    class RepositorySpy:
        def __init__(self, db) -> None:
            assert db is session

        def stage(self, *args) -> None:
            calls.append("stage")
            raise failure

    class SessionSpy:
        def commit(self) -> None:
            calls.append("commit")

        def rollback(self) -> None:
            calls.append("rollback")

    session = SessionSpy()
    monkeypatch.setattr(events_service, "EventRepository", RepositorySpy)
    monkeypatch.setattr(events_service.hub, "publish", lambda *_args: calls.append("publish"))

    with pytest.raises(RuntimeError) as raised:
        events_service.record_event(session, 7, "ai", "failed")

    assert raised.value is failure
    assert calls == ["stage", "rollback"]


def test_event_slice_preserves_facade_and_forbids_cross_layer_persistence() -> None:
    from app import services
    from app.services.events import record_event

    backend_root = Path(__file__).resolve().parents[1]
    router_source = (backend_root / "app" / "routers" / "events.py").read_text(encoding="utf-8")
    repository_source = (backend_root / "app" / "repositories" / "events.py").read_text(
        encoding="utf-8"
    )

    assert services.log_event is record_event
    assert str(inspect.signature(services.log_event)) == (
        "(db: 'Session', owner_id: 'int', scope: 'str', message: 'str', level: 'str' = 'info', "
        "ref_type: 'str' = '', ref_id: 'int | None' = None) -> 'EventLog'"
    )
    assert not any(f"db.{name}(" in router_source for name in (
        "query", "get", "add", "delete", "flush", "refresh", "commit", "rollback"
    ))
    assert not any(token in repository_source for token in (
        ".commit(", ".rollback(", "HTTPException", "fastapi", "hub.publish"
    ))


def test_event_slice_cold_imports_keep_the_legacy_facade() -> None:
    backend_root = Path(__file__).resolve().parents[1]
    script = """
import importlib

for module_name in (
    'app.repositories.events', 'app.services.events', 'app.services',
    'app.routers.events', 'app.main',
):
    importlib.import_module(module_name)

from app import services
from app.services.events import record_event
assert services.log_event is record_event
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=backend_root,
        env={**os.environ, "PYTHONPATH": str(backend_root)},
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
