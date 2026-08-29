"""Промокоды: хранение в базе вместо константы в коде.

Раньше код был один и лежал в `plan_catalog.PROMO_CODE` — новая акция требовала
деплоя. Теперь коды заводит админ.

Расчёт цены (`plan_catalog.quote_plan`) намеренно остался чистой функцией без
доступа к базе: он считает деньги, и его удобно проверять тестами без БД.
Поэтому годный код ищет этот модуль, а в расчёт передаётся уже готовая скидка.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy.orm import Session

from ..models import PromoCode
from .errors import DomainError

MAX_CODE_LEN = 40


def normalize(code: str | None) -> str:
    """Код всегда в верхнем регистре и без пробелов по краям."""
    return (code or "").strip().upper()[:MAX_CODE_LEN]


def _now() -> datetime:
    return datetime.now(UTC)


def _as_utc(moment: datetime | None) -> datetime | None:
    """Срок из SQLite приходит без зоны — сравнивать такое с aware-датой нельзя."""
    if moment is None:
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)


def find_usable(db: Session, code: str | None, *, now: datetime | None = None) -> PromoCode | None:
    """Годный к применению код или None.

    None — это и «нет такого», и «просрочен», и «исчерпан». Наружу разница не
    выдаётся: подсказка «код существует, но кончился» помогает перебирать чужие
    коды и ничего не даёт честному покупателю.
    """
    normalized = normalize(code)
    if not normalized:
        return None
    row = db.query(PromoCode).filter(PromoCode.code == normalized).one_or_none()
    if row is None:
        return None
    moment = now or _now()
    if not row.active or int(row.discount_pct or 0) <= 0:
        return None
    valid_until = _as_utc(row.valid_until)
    if valid_until is not None and valid_until < moment:
        return None
    limit = int(row.max_uses or 0)
    if limit and int(row.used_count or 0) >= limit:
        return None
    return row


def discount_for(db: Session, code: str | None) -> tuple[str, int]:
    """(код, процент скидки) для расчёта цены. Пусто, если код не годится."""
    row = find_usable(db, code)
    return (row.code, int(row.discount_pct)) if row else ("", 0)


def list_codes(db: Session) -> list[PromoCode]:
    return db.query(PromoCode).order_by(PromoCode.active.desc(), PromoCode.id.desc()).all()


def create_code(
    db: Session,
    *,
    code: str,
    discount_pct: int,
    valid_until: datetime | None = None,
    max_uses: int = 0,
    note: str = "",
    admin_id: int | None = None,
) -> PromoCode:
    normalized = normalize(code)
    if not normalized:
        raise DomainError(400, "Код не может быть пустым")
    if not normalized.replace("-", "").replace("_", "").isalnum():
        raise DomainError(400, "В коде допустимы буквы, цифры, дефис и подчёркивание")
    pct = int(discount_pct or 0)
    if not 1 <= pct <= 100:
        raise DomainError(400, "Скидка должна быть от 1 до 100 процентов")
    if int(max_uses or 0) < 0:
        raise DomainError(400, "Лимит применений не может быть отрицательным")
    if db.query(PromoCode.id).filter(PromoCode.code == normalized).first():
        raise DomainError(409, f"Промокод {normalized} уже существует")

    row = PromoCode(
        code=normalized,
        discount_pct=pct,
        active=True,
        valid_until=valid_until,
        max_uses=int(max_uses or 0),
        used_count=0,
        note=(note or "")[:2000],
        created_by=admin_id,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def update_code(
    db: Session,
    code_id: int,
    *,
    discount_pct: int | None = None,
    active: bool | None = None,
    valid_until: datetime | None = None,
    max_uses: int | None = None,
    note: str | None = None,
) -> PromoCode:
    row = db.get(PromoCode, code_id)
    if row is None:
        raise DomainError(404, "Промокод не найден")
    if discount_pct is not None:
        pct = int(discount_pct)
        if not 1 <= pct <= 100:
            raise DomainError(400, "Скидка должна быть от 1 до 100 процентов")
        row.discount_pct = pct
    if active is not None:
        row.active = bool(active)
    if valid_until is not None:
        row.valid_until = valid_until
    if max_uses is not None:
        if int(max_uses) < 0:
            raise DomainError(400, "Лимит применений не может быть отрицательным")
        row.max_uses = int(max_uses)
    if note is not None:
        row.note = note[:2000]
    db.commit()
    db.refresh(row)
    return row


def delete_code(db: Session, code_id: int) -> None:
    row = db.get(PromoCode, code_id)
    if row is None:
        raise DomainError(404, "Промокод не найден")
    db.delete(row)
    db.commit()


def mark_used(db: Session, code: str | None) -> None:
    """Отметить применение кода при активации тарифа админом.

    Считаем здесь, а не при расчёте цены на сайте: оплата ручная, и показ формы
    с введённым кодом ничего не значит — человек мог передумать. Настоящее
    применение — это оплаченный и активированный тариф.
    """
    normalized = normalize(code)
    if not normalized:
        return
    row = db.query(PromoCode).filter(PromoCode.code == normalized).one_or_none()
    if row is None:
        return
    row.used_count = int(row.used_count or 0) + 1
    db.commit()
