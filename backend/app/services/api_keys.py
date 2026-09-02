"""API-key settings application service."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

import httpx
from sqlalchemy import update
from sqlalchemy.orm import Session

from .. import quota as q
from ..models import ApiKey
from ..repositories.api_keys import ApiKeyRepository
from ..schemas import ApiKeyIn, ApiKeyListOut, ApiKeyOut, ApiKeyUpdate, QuotaOut
from .errors import DomainError
from .events import record_event


def check_key_live(key_value: str) -> tuple[str, str]:
    """Check a key with the fixed one-unit YouTube videos.list request."""
    url = "https://www.googleapis.com/youtube/v3/videos"
    params = {"part": "id", "id": "dQw4w9WgXcQ", "key": key_value}
    try:
        response = httpx.get(url, params=params, timeout=15.0)
    except httpx.RequestError as exc:
        return "error", f"Нет связи с googleapis.com: {exc}"
    if response.status_code < 400:
        return "ok", "Ключ работает"
    try:
        error = response.json().get("error", {})
        errors = error.get("errors") or [{}]
        reason = errors[0].get("reason", "")
        message = error.get("message", "")
    except (ValueError, KeyError, IndexError):
        reason, message = "", ""
    if reason in {"quotaExceeded", "dailyLimitExceeded", "rateLimitExceeded"}:
        return "quota_exceeded", f"Квота исчерпана ({reason})"
    if reason in {
        "keyInvalid",
        "badRequest",
        "forbidden",
        "accessNotConfigured",
        "ipRefererBlocked",
        "keyExpired",
    }:
        return "invalid", f"{reason}: {message}" if message else reason
    return "error", f"HTTP {response.status_code} {reason}: {message}".strip()


def reencrypt_legacy(db: Session) -> int:
    """Дошифровать ключи, сохранённые до появления шифрования. Идемпотентно.

    Читаем сырые значения колонки мимо ORM-типа (иначе открытый текст и
    шифртекст неразличимы после расшифровки), затем присваиваем через ORM —
    EncryptedSecret зашифрует при flush. Вызывается при старте приложения.
    """
    from sqlalchemy import text

    from ..secretbox import PREFIX

    rows = db.execute(
        text("SELECT id FROM api_keys WHERE key NOT LIKE :prefix"),
        {"prefix": f"{PREFIX}%"},
    ).fetchall()
    if not rows:
        return 0
    ids = [int(row[0]) for row in rows]
    table = ApiKey.__table__
    for key in db.query(ApiKey).filter(ApiKey.id.in_(ids)).all():
        # Core-UPDATE, а не присваивание того же значения: для ORM оно
        # «не изменилось» и в базу не уйдёт. Тип колонки зашифрует при bind.
        db.execute(update(table).where(table.c.id == key.id).values(key=key.key))
    db.commit()
    return len(ids)


def _quota_out(repository: ApiKeyRepository, owner_id: int) -> QuotaOut:
    keys = repository.list_owned(owner_id)
    enabled = [key for key in keys if key.enabled]
    seen: set[str] = set()
    total = 0
    for key in enabled:
        marker = (key.project_label or "").strip().lower() or f"__key_{key.id}"
        if marker not in seen:
            seen.add(marker)
            total += int(key.daily_quota or q.DEFAULT_DAILY_QUOTA)
    used = sum(
        int(row.units or 0)
        for row in repository.usages_on([key.id for key in enabled], q.quota_day())
    )
    return QuotaOut(
        total_daily=total,
        used_today=used,
        left_today=max(0, total - used),
        keys_total=len(keys),
        keys_enabled=len(enabled),
        projects=len(seen),
        resets_at=q.next_reset(),
        measured_locally=True,
        source_note=q.SOURCE_NOTE,
    )


def _display_value(value: str | None, key_value: str) -> str | None:
    if value is None:
        return None
    return value.replace(key_value, q.mask_key(key_value))


def _key_out(repository: ApiKeyRepository, key: ApiKey) -> ApiKeyOut:
    limit = int(key.daily_quota or q.DEFAULT_DAILY_QUOTA)
    used = repository.used_on(key.id, q.quota_day())
    return ApiKeyOut(
        id=key.id,
        label=_display_value(key.label, key.key),
        key_masked=q.mask_key(key.key),
        project_label=_display_value(key.project_label, key.key),
        daily_quota=limit,
        enabled=key.enabled,
        status=_display_value(key.status, key.key),
        status_detail=_display_value(key.status_detail, key.key),
        last_checked_at=key.last_checked_at,
        used_today=used,
        left_today=max(0, limit - used),
    )


def _list_out(repository: ApiKeyRepository, owner_id: int) -> ApiKeyListOut:
    keys = repository.list_owned(owner_id)
    return ApiKeyListOut(
        keys=[_key_out(repository, key) for key in keys], quota=_quota_out(repository, owner_id)
    )


def list_api_keys(db: Session, owner_id: int) -> ApiKeyListOut:
    return _list_out(ApiKeyRepository(db), owner_id)


def read_quota(db: Session, owner_id: int) -> QuotaOut:
    return _quota_out(ApiKeyRepository(db), owner_id)


def create_api_key(
    db: Session,
    owner_id: int,
    payload: ApiKeyIn,
    *,
    record_event_fn: Callable[..., object] = record_event,
) -> ApiKeyListOut:
    repository = ApiKeyRepository(db)
    value = payload.key.strip()
    if not value:
        raise DomainError(400, "Пустой ключ")
    if repository.find_owned_value(owner_id, value):
        raise DomainError(409, "Такой ключ уже добавлен")
    key = ApiKey(
        owner_id=owner_id,
        key=value,
        label=payload.label.strip(),
        project_label=payload.project_label.strip(),
        daily_quota=max(1, payload.daily_quota or q.DEFAULT_DAILY_QUOTA),
    )
    try:
        repository.stage_key(key)
        db.commit()
        db.refresh(key)
    except Exception:
        db.rollback()
        raise
    record_event_fn(
        db,
        owner_id,
        "settings",
        f"Добавлен ключ YouTube «{_display_value(key.label, key.key) or q.mask_key(key.key)}»",
        "success",
        "api_key",
        key.id,
    )
    return _list_out(repository, owner_id)


def update_api_key(db: Session, owner_id: int, key_id: int, payload: ApiKeyUpdate) -> ApiKeyListOut:
    repository = ApiKeyRepository(db)
    key = repository.get_owned(owner_id, key_id)
    if key is None:
        raise DomainError(404, "Ключ не найден")
    try:
        for field, value in payload.model_dump(exclude_unset=True).items():
            if value is not None:
                setattr(key, field, value)
        db.commit()
    except Exception:
        db.rollback()
        raise
    return _list_out(repository, owner_id)


def delete_api_key(
    db: Session,
    owner_id: int,
    key_id: int,
    *,
    record_event_fn: Callable[..., object] = record_event,
) -> ApiKeyListOut:
    repository = ApiKeyRepository(db)
    key = repository.get_owned(owner_id, key_id)
    if key is None:
        raise DomainError(404, "Ключ не найден")
    label = _display_value(key.label, key.key) or q.mask_key(key.key)
    try:
        repository.delete_key(key)
        db.commit()
    except Exception:
        db.rollback()
        raise
    record_event_fn(db, owner_id, "settings", f"Ключ «{label}» удалён", "warn")
    return _list_out(repository, owner_id)


def check_api_key(
    db: Session,
    owner_id: int,
    key_id: int,
    *,
    checker: Callable[[str], tuple[str, str]],
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
    record_event_fn: Callable[..., object] = record_event,
) -> ApiKeyListOut:
    repository = ApiKeyRepository(db)
    key = repository.get_owned(owner_id, key_id)
    if key is None:
        raise DomainError(404, "Ключ не найден")
    status, detail = checker(key.key)
    detail = detail.replace(key.key, q.mask_key(key.key))
    try:
        key.status, key.status_detail, key.last_checked_at = status, detail, now()
        if status != "error" or "Нет связи" not in detail:
            repository.stage_spend(key, q.quota_day(), q.COSTS["videos.list"], 1)
        db.commit()
    except Exception:
        db.rollback()
        raise
    level = {"ok": "success", "quota_exceeded": "warn"}.get(status, "error")
    record_event_fn(
        db,
        owner_id,
        "settings",
        f"Проверка ключа «{_display_value(key.label, key.key) or q.mask_key(key.key)}»: {detail}",
        level,
        "api_key",
        key.id,
    )
    return _list_out(repository, owner_id)
