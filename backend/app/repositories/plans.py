"""Persistence queries for subscription and plan-usage policy."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import func
from sqlalchemy.orm import Session

from ..models import (
    Campaign,
    CampaignSend,
    Lead,
    LeadDatabase,
    Offer,
    Subscription,
    User,
    WarmupAccount,
)


class PlanRepository:
    """Query and stage plan-domain rows without owning a transaction."""

    def __init__(self, db: Session) -> None:
        self._db = db

    def get_user(self, user_id: int) -> User | None:
        return self._db.get(User, user_id)

    def get_subscription(self, owner_id: int) -> Subscription | None:
        return self._db.query(Subscription).filter(Subscription.owner_id == owner_id).one_or_none()

    def require_subscription(self, owner_id: int) -> Subscription:
        return self._db.query(Subscription).filter(Subscription.owner_id == owner_id).one()

    def stage_subscription(self, row: Subscription) -> None:
        self._db.add(row)

    def count_owned(self, model: object, owner_id: int) -> int:
        return self._db.query(func.count(model.id)).filter(model.owner_id == owner_id).scalar() or 0

    def count_databases(self, owner_id: int) -> int:
        return self.count_owned(LeadDatabase, owner_id)

    def count_offers(self, owner_id: int) -> int:
        return self.count_owned(Offer, owner_id)

    def count_campaigns(self, owner_id: int) -> int:
        return self.count_owned(Campaign, owner_id)

    def count_warmup(self, owner_id: int) -> int:
        return self.count_owned(WarmupAccount, owner_id)

    def count_leads_since(self, owner_id: int, start: datetime) -> int:
        return (
            self._db.query(func.count(Lead.id))
            .join(LeadDatabase, Lead.database_id == LeadDatabase.id)
            .filter(LeadDatabase.owner_id == owner_id, Lead.created_at >= start)
            .scalar()
            or 0
        )

    def count_sent_ai_since(self, owner_id: int, start: datetime) -> int:
        return (
            self._db.query(func.count(CampaignSend.id))
            .filter(
                CampaignSend.owner_id == owner_id,
                # uncertain потратил квоту (sending.py её не возвращает) —
                # пересчёт обязан видеть его, иначе разойдётся с monthly_used.
                CampaignSend.status.in_(("sent", "uncertain")),
                CampaignSend.offer_kind == "ai",
                CampaignSend.sent_at >= start,
            )
            .scalar()
            or 0
        )

    def owner_ids_for_grant(self, *, only_with_plan: bool) -> list[int]:
        """Кому массово выдавать дни.

        `only_with_plan` оставляет действующих клиентов: незаблокированных и с
        активированным тарифом. Иначе одна кнопка раздала бы платный доступ
        всем, кто когда-либо заходил.
        """
        query = self._db.query(User.id).outerjoin(
            Subscription, Subscription.owner_id == User.id
        )
        if only_with_plan:
            query = query.filter(
                Subscription.blocked.is_(False),
                Subscription.plan.notin_(["", "none", "free"]),
            )
        return [row[0] for row in query.order_by(User.id).all()]
