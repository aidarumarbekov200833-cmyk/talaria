"""Шифрование ключей YouTube в БД (secretbox + EncryptedSecret)."""

from __future__ import annotations

from sqlalchemy import text

from app import secretbox
from app.models import ApiKey
from app.repositories.api_keys import ApiKeyRepository
from app.services.api_keys import reencrypt_legacy


def test_roundtrip_and_random_iv():
    a = secretbox.encrypt("AIzaSyExample-1234567890")
    b = secretbox.encrypt("AIzaSyExample-1234567890")
    assert a.startswith(secretbox.PREFIX) and b.startswith(secretbox.PREFIX)
    assert a != b, "одинаковый шифртекст выдал бы факт совпадения ключей"
    assert secretbox.decrypt(a) == secretbox.decrypt(b) == "AIzaSyExample-1234567890"


def test_legacy_plaintext_and_double_encrypt():
    assert secretbox.decrypt("plain-legacy") == "plain-legacy"
    once = secretbox.encrypt("x")
    assert secretbox.encrypt(once) == once


def test_wrong_key_returns_empty(monkeypatch):
    token = secretbox.encrypt("secret")
    monkeypatch.setattr(secretbox.settings, "data_encryption_key", "another-key-" * 4)
    secretbox.reset_cache()
    try:
        assert secretbox.decrypt(token) == ""
    finally:
        monkeypatch.undo()
        secretbox.reset_cache()


def test_column_stores_ciphertext_and_repo_finds_by_value(db, user):
    db.add(ApiKey(owner_id=user.id, key="AIzaSy-col-test", label="k"))
    db.commit()
    raw = db.execute(text("SELECT key FROM api_keys")).scalar_one()
    assert raw.startswith(secretbox.PREFIX)
    assert "AIzaSy-col-test" not in raw
    repo = ApiKeyRepository(db)
    assert repo.find_owned_value(user.id, "AIzaSy-col-test") is not None
    assert repo.find_owned_value(user.id, "other") is None


def test_reencrypt_legacy_rows(db, user):
    db.execute(
        text(
            "INSERT INTO api_keys (owner_id, key, label, project_label, daily_quota, enabled, "
            "status, status_detail) VALUES (:o, 'legacy-plain', '', '', 10000, 1, 'unchecked', '')"
        ),
        {"o": user.id},
    )
    db.commit()
    assert reencrypt_legacy(db) == 1
    assert reencrypt_legacy(db) == 0
    raw = db.execute(text("SELECT key FROM api_keys")).scalar_one()
    assert raw.startswith(secretbox.PREFIX)
    assert db.query(ApiKey).one().key == "legacy-plain"
