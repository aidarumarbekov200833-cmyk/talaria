"""Failure-boundary and secret-display regressions for Task 3B4."""

from __future__ import annotations

import pytest


@pytest.mark.parametrize("operation", ["create", "delete", "check"])
def test_staging_failure_rolls_back_once_without_commit_or_event(db, user, monkeypatch, operation):
    from app import models
    from app.schemas import ApiKeyIn
    from app.services import api_keys

    key = models.ApiKey(owner_id=user.id, key="failure-key")
    db.add(key)
    db.commit()
    commits: list[bool] = []
    rollbacks: list[bool] = []
    monkeypatch.setattr(db, "commit", lambda: commits.append(True))
    monkeypatch.setattr(db, "rollback", lambda: rollbacks.append(True))
    repository = api_keys.ApiKeyRepository
    method = {"create": "stage_key", "delete": "delete_key", "check": "stage_spend"}[operation]
    monkeypatch.setattr(
        repository, method, lambda *_args: (_ for _ in ()).throw(RuntimeError("stage"))
    )

    def event(*_args: object) -> None:
        pytest.fail("event after failed staging")

    with pytest.raises(RuntimeError):
        if operation == "create":
            api_keys.create_api_key(db, user.id, ApiKeyIn(key="new"), record_event_fn=event)
        elif operation == "delete":
            api_keys.delete_api_key(db, user.id, key.id, record_event_fn=event)
        else:
            api_keys.check_api_key(
                db, user.id, key.id, checker=lambda _: ("ok", "ok"), record_event_fn=event
            )
    assert commits == []
    assert rollbacks == [True]


def test_update_commit_failure_rolls_back_once_without_event(db, user, monkeypatch):
    from app import models
    from app.schemas import ApiKeyUpdate
    from app.services.api_keys import update_api_key

    key = models.ApiKey(owner_id=user.id, key="update-failure", label="old")
    db.add(key)
    db.commit()
    rollbacks: list[bool] = []
    monkeypatch.setattr(db, "commit", lambda: (_ for _ in ()).throw(RuntimeError("commit")))
    monkeypatch.setattr(db, "rollback", lambda: rollbacks.append(True))
    with pytest.raises(RuntimeError):
        update_api_key(db, user.id, key.id, ApiKeyUpdate(label="new"))
    assert rollbacks == [True]


def test_raw_key_in_label_and_project_never_leaks_api_or_events(
    client, db, user, auth_headers, monkeypatch
):
    from app import models
    from app.routers import settings

    sentinel = "raw-sentinel-1234"
    payload = {
        "key": sentinel,
        "label": f"before-{sentinel}-after",
        "project_label": f"project-{sentinel}",
    }
    created = client.post("/api/keys", headers=auth_headers, json=payload)
    assert created.status_code == 201, created.text
    key_id = created.json()["keys"][0]["id"]
    assert sentinel not in created.text
    assert (
        "before-" in created.json()["keys"][0]["label"]
        and "-after" in created.json()["keys"][0]["label"]
    )
    assert "project-" in created.json()["keys"][0]["project_label"]
    monkeypatch.setattr(settings, "check_key_live", lambda _: ("ok", "ok"))
    assert client.post(f"/api/keys/{key_id}/check", headers=auth_headers).status_code == 200
    assert client.delete(f"/api/keys/{key_id}", headers=auth_headers).status_code == 200
    assert sentinel not in " ".join(row.message for row in db.query(models.EventLog).all())


def test_raw_key_in_persisted_status_fields_never_leaks_list(client, db, user, auth_headers):
    from app import models

    sentinel = "raw-status-sentinel-1234"
    db.add(
        models.ApiKey(
            owner_id=user.id,
            key=sentinel,
            status=f"before-{sentinel}-after",
            status_detail=f"detail-{sentinel}-tail",
        )
    )
    db.commit()
    response = client.get("/api/keys", headers=auth_headers)
    assert response.status_code == 200
    row = response.json()["keys"][0]
    assert sentinel not in response.text
    assert row["status"].startswith("before-") and row["status"].endswith("-after")
    assert row["status_detail"].startswith("detail-") and row["status_detail"].endswith("-tail")
