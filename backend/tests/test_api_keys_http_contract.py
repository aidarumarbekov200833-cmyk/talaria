"""HTTP characterisation coverage for Task 3B4."""

from __future__ import annotations

from app.security import create_jwt


def test_key_create_list_duplicate_blank_and_owner_isolation(
    client, db, user, auth_headers
) -> None:
    from app import models

    raw_key = "raw-secret-sentinel-1234"
    created = client.post(
        "/api/keys",
        headers=auth_headers,
        json={"key": f" {raw_key} ", "label": " Mine ", "project_label": " Project "},
    )
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["keys"][0]["key_masked"] == "••••••1234"
    assert body["keys"][0]["left_today"] == 10_000
    assert body["quota"]["measured_locally"] is True
    assert raw_key not in created.text

    duplicate = client.post("/api/keys", headers=auth_headers, json={"key": raw_key})
    assert duplicate.status_code == 409
    assert duplicate.json()["detail"] == "Такой ключ уже добавлен"
    blank = client.post("/api/keys", headers=auth_headers, json={"key": " "})
    assert blank.status_code == 400
    assert blank.json()["detail"] == "Пустой ключ"

    other = models.User(telegram_id=3003, username="other", first_name="Other")
    db.add(other)
    db.add(models.AccessWhitelist(username="other", note="test"))
    db.commit()
    foreign_token, _ = create_jwt(other.id)
    foreign_headers = {"Authorization": f"Bearer {foreign_token}", "X-Request-Id": "keys-404"}
    response = client.patch(
        f"/api/keys/{body['keys'][0]['id']}", headers=foreign_headers, json={"label": "x"}
    )
    assert response.status_code == 404
    assert response.json() == {"detail": "Ключ не найден", "request_id": "keys-404"}
    assert raw_key not in " ".join(row.message for row in db.query(models.EventLog).all())


def test_live_check_spends_once_and_masks_checker_secret(
    client, db, user, auth_headers, monkeypatch
) -> None:
    from app import models
    from app.routers import settings

    raw_key = "raw-live-secret-1234"
    key = models.ApiKey(owner_id=user.id, key=raw_key, label="live")
    db.add(key)
    db.commit()
    monkeypatch.setattr(settings, "check_key_live", lambda value: ("error", f"HTTP 500 {value}"))

    response = client.post(f"/api/keys/{key.id}/check", headers=auth_headers)
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["keys"][0]["used_today"] == 1
    assert raw_key not in response.text
    assert raw_key not in " ".join(row.message for row in db.query(models.EventLog).all())
