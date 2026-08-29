"""Persistence access for the server-side event journal."""

from __future__ import annotations

from sqlalchemy.orm import Session

from ..access import current_request_ip
from ..models import EventLog


class EventRepository:
    """Read and stage ``EventLog`` rows without owning a transaction."""

    def __init__(self, db: Session) -> None:
        self._db = db

    def list_for_owner(self, owner_id: int, scope: str, limit: int) -> list[EventLog]:
        query = self._db.query(EventLog).filter(EventLog.owner_id == owner_id)
        if scope:
            query = query.filter(EventLog.scope == scope)
        return query.order_by(EventLog.id.desc()).limit(limit).all()

    def stage(
        self,
        owner_id: int,
        scope: str,
        message: str,
        level: str = "info",
        ref_type: str = "",
        ref_id: int | None = None,
    ) -> EventLog:
        row = EventLog(
            owner_id=owner_id,
            scope=scope,
            level=level,
            message=message,
            ref_type=ref_type,
            ref_id=ref_id,
            # Пусто вне запроса — у фоновых задач адреса нет и быть не может.
            ip=current_request_ip(),
        )
        self._db.add(row)
        self._db.flush()
        return row
