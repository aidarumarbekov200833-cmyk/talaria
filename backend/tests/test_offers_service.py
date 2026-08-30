"""Contract-first coverage for the offers repository/service extraction."""

from __future__ import annotations

import ast
import base64
import hashlib
import inspect
import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from app import models
from app.schemas import AiOfferSettingsIn, OfferIn, OfferListOut
from app.security import create_jwt


def _data_uri(mime: str, raw: bytes) -> str:
    return f"data:image/{mime};base64,{base64.b64encode(raw).decode()}"


def _make_user(db, telegram_id: int, username: str) -> models.User:
    user = models.User(telegram_id=telegram_id, username=username, first_name=username)
    db.add(user)
    db.add(models.AccessWhitelist(username=username, note="offers service test"))
    db.commit()
    db.refresh(user)
    return user


def _headers(user: models.User) -> dict[str, str]:
    token, _jti = create_jwt(user.id)
    return {"Authorization": f"Bearer {token}"}


def _lead(db, owner_id: int, suffix: str, title: str) -> models.Lead:
    database = models.LeadDatabase(owner_id=owner_id, name=f"DB {suffix}")
    db.add(database)
    db.flush()
    lead = models.Lead(
        database_id=database.id,
        channel_id=f"channel-{suffix}",
        title=title,
        tg_first_name=f"Name-{suffix}",
        tg_username=f"user_{suffix}",
    )
    db.add(lead)
    db.commit()
    db.refresh(lead)
    return lead


def test_offers_layers_exist_are_transport_neutral_and_ai_bridge_is_unchanged() -> None:
    from app.repositories import offers as repository
    from app.routers import offers as router
    from app.services import offers as service

    for module in ("app.repositories.offers", "app.services.offers", "app.routers.offers"):
        result = subprocess.run(
            [sys.executable, "-c", f"import {module}"],
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, result.stderr

    repository_source = inspect.getsource(repository)
    service_source = inspect.getsource(service)
    assert "fastapi" not in repository_source.lower()
    assert "fastapi" not in service_source.lower()
    assert "HTTPException" not in repository_source
    assert "HTTPException" not in service_source
    assert "DomainError" not in repository_source
    for forbidden in (".commit(", ".rollback(", ".refresh("):
        assert forbidden not in repository_source

    handlers = (
        router.list_offers,
        router.create_offer,
        router.update_offer,
        router.delete_offer,
        router.preview_offer,
        router.get_ai_offer_settings,
        router.save_ai_offer_settings,
    )
    for handler in handlers:
        tree = ast.parse(inspect.getsource(handler))
        function = next(node for node in tree.body if isinstance(node, ast.FunctionDef))
        function.decorator_list = []
        calls = {
            node.func.attr
            for node in ast.walk(function)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
        }
        assert not (
            calls & {"get", "query", "add", "delete", "flush", "commit", "rollback", "refresh"}
        )

    digest = hashlib.sha256(inspect.getsource(router.ai_generate_offer).encode()).hexdigest()
    assert digest == "65cb861d7f311d9e06bf3da67dc894500dd00be3fc5e5b0bec1190d9b44f971d"


def test_offers_refactor_keeps_normalized_openapi_snapshot() -> None:
    repository_root = Path(__file__).resolve().parents[2]
    if str(repository_root) not in sys.path:
        sys.path.insert(0, str(repository_root))

    from app.main import app
    from scripts.contract_gate import normalize_openapi

    manifest = json.loads(
        (Path(__file__).parent / "contracts" / "openapi-contract.json").read_text(encoding="utf-8")
    )
    assert normalize_openapi(app.openapi()) == manifest


@pytest.mark.parametrize(
    ("mime", "raw"),
    [
        ("png", b"\x89PNG\r\n\x1a\ncontent"),
        ("jpeg", b"\xff\xd8\xffcontent"),
        ("webp", b"RIFFxxxxWEBPcontent"),
        ("gif", b"GIF87acontent"),
        ("gif", b"GIF89acontent"),
    ],
)
def test_validated_image_accepts_only_matching_raster_data(mime: str, raw: bytes) -> None:
    from app.services.offers import validated_image

    value = _data_uri(mime, raw)
    assert validated_image(value) == value
    assert validated_image("") == ""


@pytest.mark.parametrize(
    ("value", "status", "detail"),
    [
        (
            "data:image/svg+xml;base64,PHN2Zz48L3N2Zz4=",
            400,
            "Картинка должна быть PNG, JPEG, WebP или GIF",
        ),
        ("https://example.test/a.png", 400, "Картинка должна быть PNG, JPEG, WebP или GIF"),
        ("data:image/png;base64,%%%", 400, "Картинка повреждена: неверный base64"),
        (
            _data_uri("png", b"not-a-png"),
            400,
            "Формат картинки не совпадает с содержимым файла",
        ),
    ],
)
def test_validated_image_rejects_mime_base64_and_signature_spoofing(
    value: str, status: int, detail: str
) -> None:
    from app.services.errors import DomainError
    from app.services.offers import validated_image

    with pytest.raises(DomainError) as caught:
        validated_image(value)
    assert caught.value.status_code == status
    assert caught.value.detail == detail


def test_validated_image_enforces_configured_decoded_size(monkeypatch) -> None:
    from app.services import offers as service
    from app.services.errors import DomainError

    monkeypatch.setattr(service.app_config, "max_offer_image_bytes", 8)
    with pytest.raises(DomainError) as caught:
        service.validated_image(_data_uri("png", b"\x89PNG\r\n\x1a\nX"))
    assert caught.value.status_code == 413
    assert caught.value.detail == "Картинка слишком большая (максимум 0 КБ)"


@pytest.mark.parametrize(
    ("value", "fallback", "expected"),
    [
        (None, 5, 5),
        (3, 5, 3),
        (4, 5, 3),
        (7, 5, 5),
        (15, 5, 10),
        (99, 5, 20),
        ("bad", 10, 10),
    ],
)
def test_clamp_materials_preserves_nearest_lower_tie_and_fallback(
    value: object, fallback: int, expected: int
) -> None:
    from app.services.offers import clamp_materials

    assert clamp_materials(value, fallback) == expected


def test_offer_repository_queries_are_owner_scoped_and_ordered(db, user) -> None:
    from app.repositories.offers import OfferRepository

    foreign = _make_user(db, 3002, "offer_foreign")
    first = models.Offer(owner_id=user.id, name="first", text="1", image="")
    second = models.Offer(owner_id=user.id, name="second", text="2", image="")
    hidden = models.Offer(owner_id=foreign.id, name="hidden", text="3", image="")
    db.add_all([first, second, hidden])
    db.commit()
    repository = OfferRepository(db)

    assert [row.name for row in repository.list_offers(user.id)] == ["second", "first"]
    assert repository.get_offer(hidden.id) is hidden

    own_old = _lead(db, user.id, "old", "Old")
    own_new = _lead(db, user.id, "new", "New")
    foreign_lead = _lead(db, foreign.id, "foreign", "Foreign")
    assert repository.get_owned_lead(own_old.id, user.id) is own_old
    assert repository.get_owned_lead(foreign_lead.id, user.id) is None
    assert repository.latest_owned_lead(user.id).id == own_new.id


def test_real_http_offer_crud_keeps_exact_json_order_ownership_and_events(
    client, db, paid_user, auth_headers
) -> None:
    foreign = _make_user(db, 3003, "offer_other")
    hidden = models.Offer(owner_id=foreign.id, name="foreign", text="hidden", image="")
    db.add(hidden)
    db.commit()
    db.refresh(hidden)

    image = _data_uri("png", b"\x89PNG\r\n\x1a\ncontent")
    created = client.post(
        "/api/offers",
        headers=auth_headers,
        json={"name": "N" * 205, "text": "Text one", "image": image},
    )
    assert created.status_code == 200
    first = created.json()
    # created_by появилось вместе с сохранением офферов помощником: список
    # должен показывать, что текст написан не человеком.
    assert set(first) == {"id", "name", "text", "image", "images", "created_by", "created_at"}
    assert first["created_by"] == "user"
    assert first["name"] == "N" * 200
    assert first["text"] == "Text one"
    assert first["image"] == image
    # Одиночная картинка сохраняется и как альбом из одного кадра.
    assert first["images"] == [image]

    second = client.post(
        "/api/offers",
        headers=auth_headers,
        json={"name": "second", "text": "Text two", "image": ""},
    )
    assert second.status_code == 200
    listing = client.get("/api/offers", headers=auth_headers)
    assert listing.status_code == 200
    rows = listing.json()["offers"]
    assert [row["id"] for row in rows] == [second.json()["id"], first["id"]]
    assert hidden.id not in {row["id"] for row in rows}

    updated = client.patch(
        f"/api/offers/{first['id']}",
        headers=auth_headers,
        json={"name": "updated", "text": "changed", "image": ""},
    )
    assert updated.status_code == 200
    assert updated.json()["name"] == "updated"
    assert updated.json()["text"] == "changed"
    assert updated.json()["image"] == image

    for method in ("patch", "delete"):
        response = getattr(client, method)(
            f"/api/offers/{hidden.id}",
            headers={**auth_headers, "X-Request-Id": f"foreign-{method}"},
            **({"json": {"name": "x", "text": "x", "image": ""}} if method == "patch" else {}),
        )
        assert response.status_code == 404
        assert response.json() == {"detail": "Не найдено", "request_id": f"foreign-{method}"}

    deleted = client.delete(f"/api/offers/{first['id']}", headers=auth_headers)
    assert deleted.status_code == 200
    assert deleted.json() == {"ok": True}
    assert db.get(models.Offer, first["id"]) is None

    events = (
        db.query(models.EventLog)
        .filter(models.EventLog.owner_id == paid_user.id, models.EventLog.scope == "offers")
        .order_by(models.EventLog.id.asc())
        .all()
    )
    assert [(event.message, event.level, event.ref_type) for event in events] == [
        (f"Создан оффер «{'N' * 200}»", "success", "offer"),
        ("Создан оффер «second»", "success", "offer"),
    ]
    assert [event.ref_id for event in events] == [first["id"], second.json()["id"]]


class _TxDb:
    def __init__(self, calls: list[object], *, fail_commit: bool = False) -> None:
        self.calls = calls
        self.fail_commit = fail_commit

    def commit(self) -> None:
        self.calls.append("commit")
        if self.fail_commit:
            raise RuntimeError("commit failed")

    def rollback(self) -> None:
        self.calls.append("rollback")

    def refresh(self, row: object) -> None:
        self.calls.append("refresh")
        if getattr(row, "id", None) is None:
            row.id = 71


class _OfferRepo:
    def __init__(self, calls: list[object], row: object | None = None, *, fail: str = "") -> None:
        self.calls = calls
        self.row = row
        self.fail = fail

    def list_offers(self, _owner_id: int) -> list[object]:
        return []

    def get_offer(self, _offer_id: int) -> object | None:
        return self.row

    def stage_offer(self, row: object) -> None:
        self.calls.append("stage_offer")
        if self.fail == "stage_offer":
            raise RuntimeError("stage failed")

    def stage_update(self) -> None:
        self.calls.append("stage_update")
        if self.fail == "stage_update":
            raise RuntimeError("stage failed")

    def stage_delete(self, _row: object) -> None:
        self.calls.append("stage_delete")
        if self.fail == "stage_delete":
            raise RuntimeError("stage failed")

    def stage_ai_settings(self) -> None:
        self.calls.append("stage_ai_settings")
        if self.fail == "stage_ai_settings":
            raise RuntimeError("stage failed")

    def get_owned_lead(self, _lead_id: int, _owner_id: int) -> object | None:
        return self.row

    def latest_owned_lead(self, _owner_id: int) -> object | None:
        return self.row


def test_create_service_orders_limit_transaction_refresh_then_separate_event() -> None:
    from app.services import offers as service

    calls: list[object] = []
    db = _TxDb(calls)
    repository = _OfferRepo(calls)

    def limit(_db: object, owner_id: int, kind: str) -> None:
        calls.append(("limit", owner_id, kind))

    def validate(value: str) -> str:
        calls.append(("validate", value))
        return value

    def event(*args: object) -> None:
        calls.append(("event", *args))

    result = service.create_offer(
        db,
        9,
        OfferIn(name="A" * 205, text="hello", image="image"),
        repository_factory=lambda _db: repository,
        check_object_limit_fn=limit,
        validate_image_fn=validate,
        log_event_fn=event,
    )
    assert result.id == 71
    assert result.name == "A" * 200
    assert calls == [
        ("limit", 9, "offers"),
        ("validate", "image"),
        "stage_offer",
        "commit",
        "refresh",
        (
            "event",
            db,
            9,
            "offers",
            f"Создан оффер «{'A' * 200}»",
            "success",
            "offer",
            71,
        ),
    ]


@pytest.mark.parametrize("operation", ["create", "update", "delete", "settings"])
def test_failed_mutation_staging_rolls_back_and_emits_no_event(operation: str) -> None:
    from app.services import offers as service

    calls: list[object] = []
    row = SimpleNamespace(id=3, owner_id=9, name="old", text="old", image="old", created_at=None)
    repository = _OfferRepo(
        calls,
        row,
        fail=f"stage_{'ai_settings' if operation == 'settings' else ('offer' if operation == 'create' else operation)}",
    )
    db = _TxDb(calls)
    events: list[object] = []
    with pytest.raises(RuntimeError, match="stage failed"):
        if operation == "create":
            service.create_offer(
                db,
                9,
                OfferIn(name="new"),
                repository_factory=lambda _db: repository,
                check_object_limit_fn=lambda *_args: None,
                validate_image_fn=lambda value: value,
                log_event_fn=lambda *_args: events.append("event"),
            )
        elif operation == "update":
            service.update_offer(
                db,
                9,
                3,
                OfferIn(name="new"),
                repository_factory=lambda _db: repository,
                validate_image_fn=lambda value: value,
            )
        elif operation == "delete":
            service.delete_offer(db, 9, 3, repository_factory=lambda _db: repository)
        else:
            service.save_ai_offer_settings(
                db,
                SimpleNamespace(id=9, ai_offer_template="old", ai_offer_materials=5),
                AiOfferSettingsIn(template="new", materials=10),
                repository_factory=lambda _db: repository,
                clamp_materials_fn=lambda value: int(value),
                log_event_fn=lambda *_args: events.append("event"),
                ai_offer_allowed_fn=lambda *_args: True,
                ai_offer_plan_names_fn=lambda: "Talaria",
                ai_provider_ready_fn=lambda _db: True,
            )
    assert "rollback" in calls
    assert "commit" not in calls
    assert events == []


@pytest.mark.parametrize("operation", ["create", "update", "delete", "settings"])
def test_failed_mutation_commit_rolls_back_and_emits_no_event(operation: str) -> None:
    from app.services import offers as service

    calls: list[object] = []
    row = SimpleNamespace(id=3, owner_id=9, name="old", text="old", image="old", created_at=None)
    repository = _OfferRepo(calls, row)
    db = _TxDb(calls, fail_commit=True)
    events: list[object] = []
    with pytest.raises(RuntimeError, match="commit failed"):
        if operation == "create":
            service.create_offer(
                db,
                9,
                OfferIn(name="new"),
                repository_factory=lambda _db: repository,
                check_object_limit_fn=lambda *_args: None,
                validate_image_fn=lambda value: value,
                log_event_fn=lambda *_args: events.append("event"),
            )
        elif operation == "update":
            service.update_offer(
                db,
                9,
                3,
                OfferIn(name="new"),
                repository_factory=lambda _db: repository,
                validate_image_fn=lambda value: value,
            )
        elif operation == "delete":
            service.delete_offer(db, 9, 3, repository_factory=lambda _db: repository)
        else:
            service.save_ai_offer_settings(
                db,
                SimpleNamespace(id=9, ai_offer_template="old", ai_offer_materials=5),
                AiOfferSettingsIn(template="new", materials=10),
                repository_factory=lambda _db: repository,
                clamp_materials_fn=lambda value: int(value),
                log_event_fn=lambda *_args: events.append("event"),
                ai_offer_allowed_fn=lambda *_args: True,
                ai_offer_plan_names_fn=lambda: "Talaria",
                ai_provider_ready_fn=lambda _db: True,
            )
    assert calls[-2:] == ["commit", "rollback"]
    assert events == []


def test_preview_http_uses_latest_owned_explicit_isolation_and_no_writes(
    client, db, user, auth_headers
) -> None:
    foreign = _make_user(db, 3004, "preview_foreign")
    old = _lead(db, user.id, "pold", "Old title")
    _lead(db, user.id, "pnew", "Latest title")
    hidden = _lead(db, foreign.id, "phidden", "Hidden title")
    baseline = (
        db.query(models.Offer).count(),
        db.query(models.EventLog).count(),
        db.query(models.Lead).count(),
    )

    newest = client.post(
        "/api/offers/preview",
        headers=auth_headers,
        json={"text": "Привет, {first_name}! {channel}"},
    )
    assert newest.status_code == 200
    assert newest.json() == {
        "text": "Привет, Name-pnew! Latest title",
        "lead_title": "Latest title",
        "unresolved": [],
    }

    explicit = client.post(
        "/api/offers/preview",
        headers=auth_headers,
        json={"text": "{channel}", "lead_id": old.id},
    )
    assert explicit.status_code == 200
    assert explicit.json()["lead_title"] == "Old title"

    denied = client.post(
        "/api/offers/preview",
        headers={**auth_headers, "X-Request-Id": "preview-foreign"},
        json={"text": "{channel}", "lead_id": hidden.id},
    )
    assert denied.status_code == 404
    assert denied.json() == {"detail": "Лид не найден", "request_id": "preview-foreign"}
    assert baseline == (
        db.query(models.Offer).count(),
        db.query(models.EventLog).count(),
        db.query(models.Lead).count(),
    )


def test_preview_without_owned_leads_preserves_text_and_empty_title(
    client, user, auth_headers
) -> None:
    response = client.post(
        "/api/offers/preview",
        headers=auth_headers,
        json={"text": "Hello {channel} {typo}"},
    )
    assert response.status_code == 200
    assert response.json() == {
        "text": "Hello {channel} {typo}",
        "lead_title": "",
        "unresolved": ["{typo}"],
    }


def test_ai_settings_http_exact_trim_clamp_limits_and_unavailable_still_saves(
    client, db, paid_user, auth_headers, monkeypatch
) -> None:
    from app.routers import offers as router

    monkeypatch.setattr(router, "ai_offer_allowed", lambda *_args: False)
    monkeypatch.setattr(router, "ai_offer_plan_names", lambda: "Talaria")
    monkeypatch.setattr(router, "ai_provider_ready", lambda _db: False)

    empty = client.get("/api/offers/ai-settings", headers=auth_headers)
    assert empty.status_code == 200
    assert empty.json() == {
        "template": "",
        "materials": 5,
        "allowed": False,
        "plan_names": "Talaria",
        "provider_ready": False,
    }

    four_thousand = "x" * 4000
    saved = client.put(
        "/api/offers/ai-settings",
        headers=auth_headers,
        json={"template": f"  {four_thousand}  ", "materials": 4},
    )
    assert saved.status_code == 200
    assert saved.json() == {
        "template": four_thousand,
        "materials": 3,
        "allowed": False,
        "plan_names": "Talaria",
        "provider_ready": False,
    }
    db.refresh(paid_user)
    assert paid_user.ai_offer_template == four_thousand
    assert paid_user.ai_offer_materials == 3

    too_long = client.put(
        "/api/offers/ai-settings",
        headers={**auth_headers, "X-Request-Id": "settings-too-long"},
        json={"template": "x" * 4001, "materials": 5},
    )
    assert too_long.status_code == 400
    assert too_long.json() == {
        "detail": "Скелет оффера слишком длинный — до 4000 символов",
        "request_id": "settings-too-long",
    }
    events = (
        db.query(models.EventLog)
        .filter(models.EventLog.owner_id == paid_user.id, models.EventLog.scope == "offers")
        .all()
    )
    assert [(event.message, event.level) for event in events] == [
        ("Настройки ИИ-оффера сохранены", "success")
    ]


def test_ai_settings_commit_is_durable_before_separate_event_failure() -> None:
    from app.services import offers as service

    calls: list[object] = []
    user = SimpleNamespace(id=8, ai_offer_template="old", ai_offer_materials=5)
    repository = _OfferRepo(calls)
    db = _TxDb(calls)

    with pytest.raises(RuntimeError, match="event failed"):
        service.save_ai_offer_settings(
            db,
            user,
            AiOfferSettingsIn(template="  new  ", materials=15),
            repository_factory=lambda _db: repository,
            clamp_materials_fn=service.clamp_materials,
            log_event_fn=lambda *_args: (_ for _ in ()).throw(RuntimeError("event failed")),
            ai_offer_allowed_fn=lambda *_args: True,
            ai_offer_plan_names_fn=lambda: "Talaria",
            ai_provider_ready_fn=lambda _db: True,
        )
    assert user.ai_offer_template == "new"
    assert user.ai_offer_materials == 10
    assert calls == ["stage_ai_settings", "commit"]


class _PoisonDb:
    def __getattribute__(self, name: str) -> object:
        if name.startswith("_"):
            return object.__getattribute__(self, name)
        raise AssertionError(f"router touched persistence: {name}")


def test_list_router_is_thin_and_real_request_uses_live_service_alias(
    client, user, auth_headers, monkeypatch
) -> None:
    from app.routers import offers as router

    expected = OfferListOut(offers=[])
    calls: list[tuple[object, int]] = []

    def intercepted(db: object, owner_id: int) -> OfferListOut:
        calls.append((db, owner_id))
        return expected

    monkeypatch.setattr(router, "list_offers_service", intercepted)
    assert router.list_offers(_PoisonDb(), SimpleNamespace(id=91)) is expected
    response = client.get("/api/offers", headers=auth_headers)
    assert response.status_code == 200
    assert response.json() == {"offers": []}
    assert [owner_id for _db, owner_id in calls] == [91, user.id]


def test_create_route_passes_live_legacy_collaborators(monkeypatch) -> None:
    from app.routers import offers as router

    db = _PoisonDb()
    user = SimpleNamespace(id=17)
    payload = OfferIn(name="name", text="text", image="image")
    expected = SimpleNamespace(id=1)
    seen: dict[str, object] = {}

    def service(db_arg: object, owner_id: int, data: OfferIn, **kwargs: object) -> object:
        seen.update({"db": db_arg, "owner_id": owner_id, "data": data, "kwargs": kwargs})
        return expected

    monkeypatch.setattr(router, "create_offer_service", service)
    assert router.create_offer(payload, db, user) is expected
    assert seen == {
        "db": db,
        "owner_id": 17,
        "data": payload,
        "kwargs": {
            "check_object_limit_fn": router.check_object_limit,
            "validate_image_fn": router._validated_image,
            "log_event_fn": router.log_event,
        },
    }
