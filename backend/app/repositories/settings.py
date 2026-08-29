"""Persistence helpers for the settings read and Telegram ticket slice."""

from __future__ import annotations

from sqlalchemy.orm import Session

from ..models import AppSettings, WarmupAccount


class SettingsRepository:
    """Query and stage settings-domain rows without owning a transaction."""

    def __init__(self, db: Session) -> None:
        self._db = db

    def get_settings(self, owner_id: int) -> AppSettings | None:
        return (
            self._db.query(AppSettings)
            .filter(AppSettings.owner_id == owner_id)
            .one_or_none()
        )

    def stage_settings(self, row: AppSettings) -> None:
        self._db.add(row)

    def count_warmup_accounts(self, owner_id: int, *, active_only: bool = False) -> int:
        query = self._db.query(WarmupAccount).filter(WarmupAccount.owner_id == owner_id)
        if active_only:
            query = query.filter(WarmupAccount.status == "active")
        return query.count()

    def list_warmup_ids(self, owner_id: int) -> list[int]:
        return [
            row[0]
            for row in self._db.query(WarmupAccount.id)
            .filter(WarmupAccount.owner_id == owner_id)
            .order_by(WarmupAccount.id.asc())
            .all()
        ]
