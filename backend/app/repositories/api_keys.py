"""Persistence access for the API-key and quota settings slice."""

from __future__ import annotations

from datetime import date

from sqlalchemy.orm import Session

from ..models import ApiKey, QuotaUsage


class ApiKeyRepository:
    """Query and stage API-key rows without owning a transaction."""

    def __init__(self, db: Session) -> None:
        self._db = db

    def list_owned(self, owner_id: int) -> list[ApiKey]:
        return self._db.query(ApiKey).filter(ApiKey.owner_id == owner_id).order_by(ApiKey.id).all()

    def get_owned(self, owner_id: int, key_id: int) -> ApiKey | None:
        return (
            self._db.query(ApiKey)
            .filter(ApiKey.id == key_id, ApiKey.owner_id == owner_id)
            .one_or_none()
        )

    def find_owned_value(self, owner_id: int, value: str) -> ApiKey | None:
        # Сравнение в Python, а не в WHERE: колонка зашифрована с случайным
        # IV, одинаковые ключи дают разный шифртекст. Ключей у владельца
        # единицы, полный проход дешевле любой схемы с отпечатком.
        for row in self.list_owned(owner_id):
            if row.key == value:
                return row
        return None

    def stage_key(self, row: ApiKey) -> None:
        self._db.add(row)

    def delete_key(self, row: ApiKey) -> None:
        self._db.delete(row)

    def used_on(self, key_id: int, day: date) -> int:
        row = (
            self._db.query(QuotaUsage)
            .filter(QuotaUsage.api_key_id == key_id, QuotaUsage.day == day)
            .one_or_none()
        )
        return int(row.units or 0) if row else 0

    def usages_on(self, key_ids: list[int], day: date) -> list[QuotaUsage]:
        if not key_ids:
            return []
        return (
            self._db.query(QuotaUsage)
            .filter(QuotaUsage.api_key_id.in_(key_ids), QuotaUsage.day == day)
            .all()
        )

    def stage_spend(self, key: ApiKey, day: date, units: int, calls: int) -> None:
        row = (
            self._db.query(QuotaUsage)
            .filter(QuotaUsage.api_key_id == key.id, QuotaUsage.day == day)
            .one_or_none()
        )
        if row is None:
            row = QuotaUsage(api_key_id=key.id, day=day, units=0, calls=0)
            self._db.add(row)
            self._db.flush()
        row.units = int(row.units or 0) + units
        row.calls = int(row.calls or 0) + calls
        self._db.flush()
