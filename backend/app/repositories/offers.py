"""Persistence queries and staging for offers and offer previews."""

from __future__ import annotations

from sqlalchemy.orm import Session

from ..models import Lead, LeadDatabase, Offer


class OfferRepository:
    """Query and stage offers-domain rows without owning transactions."""

    def __init__(self, db: Session) -> None:
        self._db = db

    def list_offers(self, owner_id: int) -> list[Offer]:
        return (
            self._db.query(Offer).filter(Offer.owner_id == owner_id).order_by(Offer.id.desc()).all()
        )

    def get_offer(self, offer_id: int) -> Offer | None:
        return self._db.get(Offer, offer_id)

    def stage_offer(self, offer: Offer) -> None:
        self._db.add(offer)
        self._db.flush()

    def stage_update(self) -> None:
        self._db.flush()

    def stage_ai_settings(self) -> None:
        self._db.flush()

    def stage_delete(self, offer: Offer) -> None:
        self._db.delete(offer)
        self._db.flush()

    def get_owned_lead(self, lead_id: int, owner_id: int) -> Lead | None:
        return (
            self._db.query(Lead)
            .join(LeadDatabase, Lead.database_id == LeadDatabase.id)
            .filter(Lead.id == lead_id, LeadDatabase.owner_id == owner_id)
            .one_or_none()
        )

    def latest_owned_lead(self, owner_id: int) -> Lead | None:
        return (
            self._db.query(Lead)
            .join(LeadDatabase, Lead.database_id == LeadDatabase.id)
            .filter(LeadDatabase.owner_id == owner_id)
            .order_by(Lead.id.desc())
            .first()
        )


__all__ = ["OfferRepository"]
