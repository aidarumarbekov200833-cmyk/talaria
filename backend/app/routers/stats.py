"""Сводка для «Главной».

Всё считается по таблицам пользователя — ни одной выдуманной цифры. Раньше
раздел показывал зашитые «8214 отправлено», «64.2% открываемость», «₸1.2М
дохода» и недельный график из констант. Ни отправки, ни оплат в системе нет,
поэтому такие метрики показывать нельзя. Здесь только то, что действительно
есть: сколько баз, лидов, офферов, кампаний собрано и как лиды копились по дням.
"""

from __future__ import annotations

from collections import Counter
from datetime import date, timedelta

from fastapi import APIRouter, Depends
from sqlalchemy import func
from sqlalchemy.orm import Session

from ..database import get_db
from ..dependencies import get_current_user
from ..models import Campaign, CampaignSend, Lead, LeadDatabase, Offer, User
from ..schemas import StatsDayOut, StatsOut

router = APIRouter(prefix="/api", tags=["stats"])

# Статусы лидов в том же порядке, в каком их показывают чипы в интерфейсе.
LEAD_STATUSES = ["new", "contacted", "replied", "interested", "unsubscribed"]
# Разрешённые окна графика. Произвольное число дней не принимаем: интерфейсу
# нужны ровно два режима, а неограниченный параметр — это лишний способ
# нагрузить базу запросом на десять лет.
CHART_WINDOWS = (7, 14, 30)


@router.get("/stats", response_model=StatsOut)
def get_stats(
    days: int = 14,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> StatsOut:
    # Лиды считаем через join на базы: у самого лида нет owner_id, владелец —
    # у базы. Так чужие лиды не попадут в счётчик при общей базе данных.
    leads_q = (
        db.query(Lead)
        .join(LeadDatabase, Lead.database_id == LeadDatabase.id)
        .filter(LeadDatabase.owner_id == user.id)
    )

    databases = (
        db.query(func.count(LeadDatabase.id)).filter(LeadDatabase.owner_id == user.id).scalar() or 0
    )
    offers = db.query(func.count(Offer.id)).filter(Offer.owner_id == user.id).scalar() or 0
    campaigns = db.query(func.count(Campaign.id)).filter(Campaign.owner_id == user.id).scalar() or 0
    telegram_rows = dict(
        leads_q.with_entities(Lead.tg_status, func.count(Lead.id)).group_by(Lead.tg_status).all()
    )
    sent_messages = (
        db.query(func.count(CampaignSend.id))
        .filter(CampaignSend.owner_id == user.id, CampaignSend.status == "sent")
        .scalar()
        or 0
    )
    replies = (
        db.query(func.count(CampaignSend.id))
        .filter(CampaignSend.owner_id == user.id, CampaignSend.replied_at.isnot(None))
        .scalar()
        or 0
    )

    # Лиды по статусам: одним запросом, а не четырьмя.
    status_rows = (
        leads_q.with_entities(Lead.status, func.count(Lead.id)).group_by(Lead.status).all()
    )
    status_counts = {s: 0 for s in LEAD_STATUSES}
    total_leads = 0
    for status, count in status_rows:
        total_leads += count
        # Незнакомый статус в сумму попадёт, но отдельной строкой не покажется.
        if status in status_counts:
            status_counts[status] = count

    # Лиды по дням за выбранное окно. Даты, в которые ничего не собирали,
    # тоже показываем (нулями), иначе график «прыгает» и вводит в заблуждение.
    chart_days = days if days in CHART_WINDOWS else 14
    today = date.today()
    window_start = today - timedelta(days=chart_days - 1)
    # Забираем сразу два окна (текущее и предыдущее) одним запросом:
    # предыдущее нужно только суммой — для строки сравнения на «Главной».
    prev_start = window_start - timedelta(days=chart_days)
    day_rows = (
        leads_q.with_entities(func.date(Lead.created_at), func.count(Lead.id))
        .filter(func.date(Lead.created_at) >= prev_start.isoformat())
        .group_by(func.date(Lead.created_at))
        .all()
    )
    # Ключи приводим к строке: SQLite отдаёт date как текст, другие СУБД — как date.
    per_day_map: Counter[str] = Counter()
    for day_value, count in day_rows:
        per_day_map[str(day_value)] = count

    leads_per_day = [
        StatsDayOut(
            day=(window_start + timedelta(days=i)).isoformat(),
            leads=per_day_map.get((window_start + timedelta(days=i)).isoformat(), 0),
        )
        for i in range(chart_days)
    ]
    prev_chart_leads = sum(
        per_day_map.get((prev_start + timedelta(days=i)).isoformat(), 0) for i in range(chart_days)
    )

    return StatsOut(
        databases=databases,
        leads=total_leads,
        offers=offers,
        campaigns=campaigns,
        verified_telegram=int(telegram_rows.get("verified", 0)),
        pending_telegram=int(telegram_rows.get("pending", 0)),
        sent_messages=int(sent_messages),
        replies=int(replies),
        reply_rate=round(replies / sent_messages * 100, 1) if sent_messages else 0.0,
        leads_by_status=status_counts,
        leads_per_day=leads_per_day,
        chart_days=chart_days,
        chart_leads=sum(d.leads for d in leads_per_day),
        prev_chart_leads=prev_chart_leads,
    )
