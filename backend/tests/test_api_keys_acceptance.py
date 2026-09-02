"""Acceptance matrix for the Task 3B4 API-key settings extraction."""

# ruff: noqa: ANN001, ANN202, ANN204

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app import quota as q
from app.security import create_jwt


def _other_headers(db, models):
    other = models.User(telegram_id=3004, username="keys_other", first_name="Other")
    db.add_all([other, ])
    db.commit()
    token, _ = create_jwt(other.id)
    return other, {"Authorization": f"Bearer {token}", "X-Request-Id": "keys-owner-404"}


def test_list_and_quota_are_owner_scoped_with_exact_math(client, db, user, auth_headers):
    from app import models

    mine_one = models.ApiKey(
        owner_id=user.id, key="mine-one-1234", project_label=" Alpha ", daily_quota=100
    )
    mine_two = models.ApiKey(
        owner_id=user.id, key="mine-two-5678", project_label="alpha", daily_quota=999
    )
    mine_three = models.ApiKey(
        owner_id=user.id, key="mine-three-9012", project_label="", daily_quota=50, enabled=False
    )
    other, _ = _other_headers(db, models)
    foreign = models.ApiKey(owner_id=other.id, key="foreign-9999", daily_quota=500)
    db.add_all([mine_one, mine_two, mine_three, foreign])
    db.flush()
    db.add_all(
        [
            models.QuotaUsage(api_key_id=mine_one.id, day=q.quota_day(), units=11, calls=2),
            models.QuotaUsage(api_key_id=mine_two.id, day=q.quota_day(), units=7, calls=1),
            models.QuotaUsage(api_key_id=foreign.id, day=q.quota_day(), units=400, calls=1),
        ]
    )
    db.commit()

    response = client.get("/api/keys", headers=auth_headers)
    assert response.status_code == 200
    body = response.json()
    assert [row["id"] for row in body["keys"]] == [mine_one.id, mine_two.id, mine_three.id]
    assert [(row["used_today"], row["left_today"]) for row in body["keys"]] == [
        (11, 89),
        (7, 992),
        (0, 50),
    ]
    assert body["quota"] == {
        "total_daily": 100,
        "used_today": 18,
        "left_today": 82,
        "keys_total": 3,
        "keys_enabled": 2,
        "projects": 1,
        "resets_at": body["quota"]["resets_at"],
        "measured_locally": True,
        "source_note": q.SOURCE_NOTE,
    }
    assert client.get("/api/quota", headers=auth_headers).json() == body["quota"]


def test_blank_duplicate_and_foreign_mutations_keep_exact_error_envelope(
    client, db, user, auth_headers
):
    from app import models

    request_id = {**auth_headers, "X-Request-Id": "keys-bad"}
    blank = client.post("/api/keys", headers=request_id, json={"key": " "})
    assert blank.status_code == 400
    assert blank.json() == {"detail": "Пустой ключ", "request_id": "keys-bad"}
    key = models.ApiKey(owner_id=user.id, key="mine-duplicate")
    db.add(key)
    db.commit()
    duplicate = client.post("/api/keys", headers=request_id, json={"key": "mine-duplicate"})
    assert duplicate.status_code == 409
    assert duplicate.json() == {"detail": "Такой ключ уже добавлен", "request_id": "keys-bad"}

    other, other_headers = _other_headers(db, models)
    foreign = models.ApiKey(owner_id=other.id, key="foreign")
    db.add(foreign)
    db.commit()
    for method, path, payload in (
        (client.patch, f"/api/keys/{foreign.id}", {"label": "x"}),
        (client.delete, f"/api/keys/{foreign.id}", None),
        (client.post, f"/api/keys/{foreign.id}/check", None),
    ):
        response = method(
            path,
            headers={**auth_headers, "X-Request-Id": "keys-owner-404"},
            **({"json": payload} if payload else {}),
        )
        assert response.status_code == 404
        assert response.json() == {"detail": "Ключ не найден", "request_id": "keys-owner-404"}


def test_partial_update_omitted_and_none_do_not_clear(client, db, user, auth_headers):
    from app import models

    key = models.ApiKey(
        owner_id=user.id,
        key="update-key",
        label="kept",
        project_label="kept-project",
        daily_quota=23,
    )
    db.add(key)
    db.commit()
    for payload in (
        {},
        {"label": None, "project_label": None, "daily_quota": None, "enabled": None},
    ):
        response = client.patch(f"/api/keys/{key.id}", headers=auth_headers, json=payload)
        assert response.status_code == 200
    db.refresh(key)
    assert (key.label, key.project_label, key.daily_quota, key.enabled) == (
        "kept",
        "kept-project",
        23,
        True,
    )


def test_create_update_delete_ordering_and_rollback(db, user, monkeypatch):
    from app import models
    from app.schemas import ApiKeyIn, ApiKeyUpdate
    from app.services import api_keys

    commits: list[str] = []
    original_commit = db.commit
    monkeypatch.setattr(db, "commit", lambda: (commits.append("commit"), original_commit())[1])
    event_seen: list[int] = []
    result = api_keys.create_api_key(
        db,
        user.id,
        ApiKeyIn(key="ordering-create"),
        record_event_fn=lambda *_args: event_seen.append(len(commits)),
    )
    key_id = result.keys[0].id
    assert event_seen == [1]
    api_keys.update_api_key(db, user.id, key_id, ApiKeyUpdate(label="updated"))
    api_keys.delete_api_key(
        db, user.id, key_id, record_event_fn=lambda *_args: event_seen.append(len(commits))
    )
    assert event_seen == [1, 3]

    before = db.query(models.ApiKey).count()
    monkeypatch.setattr(db, "commit", lambda: (_ for _ in ()).throw(RuntimeError("commit failed")))
    rollbacks: list[bool] = []
    original_rollback = db.rollback
    monkeypatch.setattr(db, "rollback", lambda: (rollbacks.append(True), original_rollback())[1])
    with pytest.raises(RuntimeError):
        api_keys.create_api_key(
            db,
            user.id,
            ApiKeyIn(key="rollback-key"),
            record_event_fn=lambda *_args: pytest.fail("event"),
        )
    assert rollbacks == [True]
    assert db.query(models.ApiKey).count() == before


def test_event_failure_does_not_undo_committed_create_or_delete(db, user):
    from app import models
    from app.schemas import ApiKeyIn
    from app.services import api_keys

    with pytest.raises(RuntimeError):
        api_keys.create_api_key(
            db,
            user.id,
            ApiKeyIn(key="event-survives"),
            record_event_fn=lambda *_args: (_ for _ in ()).throw(RuntimeError("event")),
        )
    # Колонка зашифрована со случайным IV — равенство в WHERE не работает,
    # ищем расшифрованное значение в Python (как ApiKeyRepository).
    key = next(k for k in db.query(models.ApiKey).all() if k.key == "event-survives")
    with pytest.raises(RuntimeError):
        api_keys.delete_api_key(
            db,
            user.id,
            key.id,
            record_event_fn=lambda *_args: (_ for _ in ()).throw(RuntimeError("event")),
        )
    assert db.query(models.ApiKey).filter_by(id=key.id).one_or_none() is None


@pytest.mark.parametrize(
    ("status", "detail", "spent", "level"),
    [
        ("ok", "Ключ работает", 1, "success"),
        ("quota_exceeded", "Квота исчерпана (quotaExceeded)", 1, "warn"),
        ("invalid", "keyInvalid", 1, "error"),
        ("error", "HTTP 500", 1, "error"),
        ("error", "Нет связи с googleapis.com", 0, "error"),
    ],
)
def test_live_service_spend_policy_and_event_level(db, user, status, detail, spent, level):
    from app import models
    from app.services.api_keys import check_api_key

    key = models.ApiKey(owner_id=user.id, key="live-service-secret")
    db.add(key)
    db.commit()
    events: list[tuple] = []
    result = check_api_key(
        db,
        user.id,
        key.id,
        checker=lambda _value: (status, detail),
        now=lambda: datetime(2026, 8, 21, tzinfo=UTC),
        record_event_fn=lambda *args: events.append(args),
    )
    assert result.keys[0].used_today == spent
    assert events[0][4] == level


def test_check_key_live_reason_matrix(monkeypatch):
    from app.services import api_keys as settings

    class Response:
        def __init__(self, code, payload=None, bad_json=False):
            self.status_code, self.payload, self.bad_json = code, payload, bad_json

        def json(self):
            if self.bad_json:
                raise ValueError("bad")
            return self.payload

    monkeypatch.setattr(settings.httpx, "get", lambda *_args, **_kwargs: Response(200))
    assert settings.check_key_live("k") == ("ok", "Ключ работает")
    for reason in ("quotaExceeded", "dailyLimitExceeded", "rateLimitExceeded"):
        monkeypatch.setattr(
            settings.httpx,
            "get",
            lambda *_args, reason=reason, **_kwargs: Response(
                403, {"error": {"errors": [{"reason": reason}]}}
            ),
        )
        assert settings.check_key_live("k") == ("quota_exceeded", f"Квота исчерпана ({reason})")
    for reason in (
        "keyInvalid",
        "badRequest",
        "forbidden",
        "accessNotConfigured",
        "ipRefererBlocked",
        "keyExpired",
    ):
        monkeypatch.setattr(
            settings.httpx,
            "get",
            lambda *_args, reason=reason, **_kwargs: Response(
                403, {"error": {"errors": [{"reason": reason}], "message": "detail"}}
            ),
        )
        assert settings.check_key_live("k") == ("invalid", f"{reason}: detail")
    monkeypatch.setattr(
        settings.httpx, "get", lambda *_args, **_kwargs: Response(500, bad_json=True)
    )
    assert settings.check_key_live("k") == ("error", "HTTP 500 :")
    monkeypatch.setattr(
        settings.httpx,
        "get",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(settings.httpx.RequestError("offline")),
    )
    status, detail = settings.check_key_live("k")
    assert status == "error" and "Нет связи" in detail


def test_check_route_keeps_limiter_and_openapi_contract(client):
    from app.routers import settings

    route = next(
        route
        for route in settings.router.routes
        if getattr(route, "path", "") == "/api/keys/{key_id}/check"
    )
    assert route.dependencies
    from app.main import app

    operation = app.openapi()["paths"]["/api/keys/{key_id}/check"]["post"]
    assert operation["operationId"] == "check_key_api_keys__key_id__check_post"
    assert (
        operation["responses"]["200"]["content"]["application/json"]["schema"]["$ref"]
        == "#/components/schemas/ApiKeyListOut"
    )
