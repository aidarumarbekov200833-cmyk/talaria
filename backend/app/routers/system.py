"""Health, client bootstrap configuration and public landing counters."""

from fastapi import APIRouter, Depends
from sqlalchemy import case, func
from sqlalchemy.orm import Session

from ..config import settings
from ..database import get_db
from ..models import Campaign, CampaignSend, Lead, LeadDatabase, User
from ..schemas import ConfigOut, HealthOut, PublicStatsOut

router = APIRouter(prefix="/api", tags=["system"])


@router.get("/health", response_model=HealthOut)
def health():
    return HealthOut(status="ok")


@router.get("/config", response_model=ConfigOut)
def config():
    """Tells the frontend whether to render the Telegram widget or demo login."""
    return ConfigOut(
        demo_mode=settings.demo_mode,
        bot_username=None if settings.demo_mode else settings.public_bot_username,
        bot_id=None if settings.demo_mode else settings.public_bot_id,
    )


@router.get("/public/stats", response_model=PublicStatsOut)
def public_stats(db: Session = Depends(get_db)):
    """Счётчики для страницы входа.

    Без авторизации, поэтому отдаём только агрегаты по всей системе — никаких
    имён, каналов или текстов. Статистика настоящая: если база пустая, здесь
    будут честные нули. Рисовать выдуманные цифры на главной мы не стали —
    за «не выглядеть мёртвым» на пустых данных отвечает вёрстка лендинга,
    а не подкрученные числа.
    """
    # Три агрегата по лидам берём одним запросом, а не тремя: эндпоинт
    # публичный и висит на главной, каждый лишний проход по leads — это
    # нагрузка от любого анонимного посетителя.
    lead_row = db.query(
        func.count(Lead.id),
        func.coalesce(func.sum(Lead.subscribers), 0),
        func.count(func.distinct(func.nullif(Lead.country, ""))),
    ).one()
    leads = int(lead_row[0] or 0)
    audience = int(lead_row[1] or 0)
    countries = int(lead_row[2] or 0)

    # Вся воронка отправок — тоже одним проходом по campaign_sends.
    send_row = db.query(
        func.coalesce(func.sum(case((CampaignSend.status == "sent", 1), else_=0)), 0),
        func.count(CampaignSend.delivered_at),
        func.count(CampaignSend.replied_at),
    ).one()
    messages = int(send_row[0] or 0)
    delivered = int(send_row[1] or 0)
    replies = int(send_row[2] or 0)

    bases = db.query(func.count(LeadDatabase.id)).scalar() or 0
    campaigns = db.query(func.count(Campaign.id)).scalar() or 0
    users = db.query(func.count(User.id)).scalar() or 0
    return PublicStatsOut(
        leads=leads,
        databases=bases,
        campaigns=campaigns,
        messages_sent=messages,
        users=users,
        delivered=delivered,
        replies=replies,
        audience=audience,
        countries=countries,
    )
