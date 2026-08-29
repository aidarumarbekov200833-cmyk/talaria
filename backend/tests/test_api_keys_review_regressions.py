"""Independent-review regressions for Task 3B4."""

from __future__ import annotations

from types import SimpleNamespace


def test_all_six_key_handlers_only_delegate(monkeypatch) -> None:
    from app.routers import settings
    from app.schemas import ApiKeyIn, ApiKeyUpdate

    class Poison:
        def __getattr__(self, name: str) -> object:
            if name in {"query", "add", "delete", "flush", "commit", "rollback", "refresh"}:
                raise AssertionError(name)
            raise AttributeError(name)

    result = object()
    for name in (
        "list_api_keys_service",
        "read_quota_service",
        "create_api_key_service",
        "update_api_key_service",
        "delete_api_key_service",
        "check_api_key_service",
    ):
        monkeypatch.setattr(settings, name, lambda *_args, **_kwargs: result)
    user, db = SimpleNamespace(id=1), Poison()
    assert settings.list_keys(user, db) is result
    assert settings.read_quota(user, db) is result
    assert settings.create_key(ApiKeyIn(key="x"), user, db) is result
    assert settings.update_key(1, ApiKeyUpdate(), user, db) is result
    assert settings.delete_key(1, user, db) is result
    assert settings.check_key(1, user, db) is result


def test_live_checker_uses_exact_google_request(monkeypatch) -> None:
    from app.services import api_keys

    seen: dict[str, object] = {}

    class Response:
        status_code = 200

    def get(url: str, **kwargs: object) -> Response:
        seen.update(url=url, **kwargs)
        return Response()

    monkeypatch.setattr(api_keys.httpx, "get", get)
    assert api_keys.check_key_live("sentinel") == ("ok", "Ключ работает")
    assert seen == {
        "url": "https://www.googleapis.com/youtube/v3/videos",
        "params": {"part": "id", "id": "dQw4w9WgXcQ", "key": "sentinel"},
        "timeout": 15.0,
    }
