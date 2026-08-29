"""Event-journal application service."""

from __future__ import annotations

from sqlalchemy.orm import Session

from ..events_hub import hub
from ..models import EventLog
from ..repositories.events import EventRepository
from ..schemas import EventLogListOut, EventLogOut


def list_events(db: Session, owner_id: int, scope: str, limit: int) -> EventLogListOut:
    """Return the owner's newest window in chronological display order."""
    rows = EventRepository(db).list_for_owner(owner_id, scope, limit)
    return EventLogListOut(events=[EventLogOut.model_validate(row) for row in reversed(rows)])


def record_event(
    db: Session,
    owner_id: int,
    scope: str,
    message: str,
    level: str = "info",
    ref_type: str = "",
    ref_id: int | None = None,
) -> EventLog:
    """Persist one event, then send its durable representation to live clients."""
    try:
        row = EventRepository(db).stage(owner_id, scope, message, level, ref_type, ref_id)
        db.commit()
    except Exception:
        db.rollback()
        raise
    db.refresh(row)
    hub.publish(
        owner_id,
        {
            "type": "event",
            "event": {
                "id": row.id,
                "scope": row.scope,
                "level": row.level,
                "message": row.message,
                "ref_type": row.ref_type,
                "ref_id": row.ref_id,
                "created_at": row.created_at.isoformat() if row.created_at else None,
            },
        },
    )
    return row
