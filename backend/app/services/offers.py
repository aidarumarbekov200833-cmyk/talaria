"""Transport-neutral offers CRUD, preview, image, and AI-settings policy."""

from __future__ import annotations

import base64
import binascii
from collections.abc import Callable

from sqlalchemy.orm import Session

from ..config import settings as app_config
from ..models import Lead, Offer, User
from ..placeholders import render, unresolved_tokens
from ..repositories.offers import OfferRepository
from ..schemas import (
    AiOfferSettingsIn,
    AiOfferSettingsOut,
    OfferIn,
    OfferListOut,
    OfferOut,
    OkOut,
    PreviewIn,
    PreviewOut,
)
from .errors import DomainError

RepositoryFactory = Callable[[Session], OfferRepository]
MATERIALS_CHOICES = (3, 5, 10, 20)

_IMAGE_PREFIXES = {
    "data:image/png;base64,": lambda raw: raw.startswith(b"\x89PNG\r\n\x1a\n"),
    "data:image/jpeg;base64,": lambda raw: raw.startswith(b"\xff\xd8\xff"),
    "data:image/webp;base64,": lambda raw: raw.startswith(b"RIFF") and raw[8:12] == b"WEBP",
    "data:image/gif;base64,": lambda raw: raw.startswith((b"GIF87a", b"GIF89a")),
}


def _repository(db: Session, factory: RepositoryFactory | None) -> OfferRepository:
    return (factory or OfferRepository)(db)


def validated_image(value: str) -> str:
    """Accept only a raster data URI whose decoded signature matches its MIME."""
    if not value:
        return ""
    prefix, separator, encoded = value.partition(",")
    normalized = prefix.lower() + separator
    signature_ok = _IMAGE_PREFIXES.get(normalized)
    if not separator or signature_ok is None:
        raise DomainError(400, "Картинка должна быть PNG, JPEG, WebP или GIF")
    try:
        raw = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise DomainError(400, "Картинка повреждена: неверный base64") from exc
    if len(raw) > app_config.max_offer_image_bytes:
        limit_kb = app_config.max_offer_image_bytes // 1000
        raise DomainError(413, f"Картинка слишком большая (максимум {limit_kb} КБ)")
    if not signature_ok(raw):
        raise DomainError(400, "Формат картинки не совпадает с содержимым файла")
    return value


def clamp_materials(value: object, fallback: int = 5) -> int:
    """Clamp the material count to the nearest supported value, lower on ties."""
    try:
        num = int(value if value is not None else fallback)
    except (TypeError, ValueError):
        return fallback
    return min(MATERIALS_CHOICES, key=lambda allowed: (abs(allowed - num), allowed))


def owned_offer(
    db: Session,
    owner_id: int,
    offer_id: int,
    *,
    repository_factory: RepositoryFactory | None = None,
) -> Offer:
    offer = _repository(db, repository_factory).get_offer(offer_id)
    if offer is None or offer.owner_id != owner_id:
        raise DomainError(404, "Не найдено")
    return offer


def list_offers(
    db: Session,
    owner_id: int,
    *,
    repository_factory: RepositoryFactory | None = None,
) -> OfferListOut:
    rows = _repository(db, repository_factory).list_offers(owner_id)
    return OfferListOut(offers=[OfferOut.model_validate(row) for row in rows])


def create_offer(
    db: Session,
    owner_id: int,
    data: OfferIn,
    *,
    check_object_limit_fn: Callable[[Session, int, str], None],
    validate_image_fn: Callable[[str], str] = validated_image,
    log_event_fn: Callable[..., object],
    repository_factory: RepositoryFactory | None = None,
    author: str = "user",
) -> OfferOut:
    """Создать оффер. author="ai" — когда текст сохранил помощник.

    Путь создания один на всех: у записи от помощника те же лимиты тарифа и та
    же проверка картинки, что у ручной. Отдельная ветка для ИИ означала бы
    вторую копию правил, которую однажды забудут обновить.
    """
    check_object_limit_fn(db, owner_id, "offers")
    offer = Offer(
        owner_id=owner_id,
        name=data.name[:200],
        text=data.text,
        image=validate_image_fn(data.image),
        # Ставим явно, а не полагаемся на умолчание колонки: значение нужно
        # ещё до вставки в базу, и подделать его снаружи нельзя — параметр
        # приходит из кода, а не из тела запроса.
        created_by="ai" if author == "ai" else "user",
    )
    repository = _repository(db, repository_factory)
    try:
        repository.stage_offer(offer)
        db.commit()
        db.refresh(offer)
    except Exception:
        db.rollback()
        raise
    log_event_fn(
        db,
        owner_id,
        "offers",
        f"Создан оффер «{offer.name}»",
        "success",
        "offer",
        offer.id,
    )
    return OfferOut.model_validate(offer)


def update_offer(
    db: Session,
    owner_id: int,
    offer_id: int,
    data: OfferIn,
    *,
    validate_image_fn: Callable[[str], str] = validated_image,
    repository_factory: RepositoryFactory | None = None,
) -> OfferOut:
    repository = _repository(db, repository_factory)
    offer = repository.get_offer(offer_id)
    if offer is None or offer.owner_id != owner_id:
        raise DomainError(404, "Не найдено")
    next_image = validate_image_fn(data.image) if data.image else offer.image
    try:
        offer.name = data.name[:200]
        offer.text = data.text
        offer.image = next_image
        repository.stage_update()
        db.commit()
        db.refresh(offer)
    except Exception:
        db.rollback()
        raise
    return OfferOut.model_validate(offer)


def delete_offer(
    db: Session,
    owner_id: int,
    offer_id: int,
    *,
    repository_factory: RepositoryFactory | None = None,
) -> OkOut:
    repository = _repository(db, repository_factory)
    offer = repository.get_offer(offer_id)
    if offer is None or offer.owner_id != owner_id:
        raise DomainError(404, "Не найдено")
    try:
        repository.stage_delete(offer)
        db.commit()
    except Exception:
        db.rollback()
        raise
    return OkOut()


def preview_offer(
    db: Session,
    owner_id: int,
    data: PreviewIn,
    *,
    repository_factory: RepositoryFactory | None = None,
    render_fn: Callable[[str, Lead | None], str] = render,
    unresolved_tokens_fn: Callable[[str, Lead | None], list[str]] = unresolved_tokens,
) -> PreviewOut:
    repository = _repository(db, repository_factory)
    lead = (
        repository.get_owned_lead(data.lead_id, owner_id)
        if data.lead_id
        else repository.latest_owned_lead(owner_id)
    )
    if data.lead_id and lead is None:
        raise DomainError(404, "Лид не найден")
    return PreviewOut(
        text=render_fn(data.text, lead),
        lead_title=lead.title if lead else "",
        unresolved=unresolved_tokens_fn(data.text, lead),
    )


def read_ai_offer_settings(
    db: Session,
    user: User,
    *,
    clamp_materials_fn: Callable[[object], int] = clamp_materials,
    ai_offer_allowed_fn: Callable[[Session, int], bool],
    ai_offer_plan_names_fn: Callable[[], str],
    ai_provider_ready_fn: Callable[[Session], bool],
) -> AiOfferSettingsOut:
    return AiOfferSettingsOut(
        template=user.ai_offer_template or "",
        materials=clamp_materials_fn(user.ai_offer_materials),
        allowed=ai_offer_allowed_fn(db, user.id),
        plan_names=ai_offer_plan_names_fn(),
        provider_ready=ai_provider_ready_fn(db),
    )


def save_ai_offer_settings(
    db: Session,
    user: User,
    data: AiOfferSettingsIn,
    *,
    clamp_materials_fn: Callable[[object], int] = clamp_materials,
    log_event_fn: Callable[..., object],
    ai_offer_allowed_fn: Callable[[Session, int], bool],
    ai_offer_plan_names_fn: Callable[[], str],
    ai_provider_ready_fn: Callable[[Session], bool],
    repository_factory: RepositoryFactory | None = None,
) -> AiOfferSettingsOut:
    template = (data.template or "").strip()
    if len(template) > 4000:
        raise DomainError(400, "Скелет оффера слишком длинный — до 4000 символов")
    materials = clamp_materials_fn(data.materials)
    repository = _repository(db, repository_factory)
    try:
        user.ai_offer_template = template
        user.ai_offer_materials = materials
        repository.stage_ai_settings()
        db.commit()
    except Exception:
        db.rollback()
        raise
    log_event_fn(db, user.id, "offers", "Настройки ИИ-оффера сохранены", "success")
    return read_ai_offer_settings(
        db,
        user,
        clamp_materials_fn=clamp_materials_fn,
        ai_offer_allowed_fn=ai_offer_allowed_fn,
        ai_offer_plan_names_fn=ai_offer_plan_names_fn,
        ai_provider_ready_fn=ai_provider_ready_fn,
    )


__all__ = [
    "MATERIALS_CHOICES",
    "clamp_materials",
    "create_offer",
    "delete_offer",
    "list_offers",
    "owned_offer",
    "preview_offer",
    "read_ai_offer_settings",
    "save_ai_offer_settings",
    "update_offer",
    "validated_image",
]
