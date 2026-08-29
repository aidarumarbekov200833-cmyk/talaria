"""Журнал событий для панелей логов."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from ..database import get_db
from ..dependencies import get_current_user
from ..models import User
from ..schemas import EventLogListOut
from ..services.events import list_events as list_event_rows

router = APIRouter(prefix="/api", tags=["events"])


@router.get("/events", response_model=EventLogListOut)
def list_events(
    scope: str = Query("", description="search | leads | offers | campaigns | warmup | ai"),
    limit: int = Query(50, ge=1, le=200),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> EventLogListOut:
    """Последние события. Пусто — значит ничего ещё не происходило."""
    return list_event_rows(db, user.id, scope, limit)
