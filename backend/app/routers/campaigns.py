"""Campaign lifecycle and progress logs.

Mounted at /api/campaigns. These used to live under the /api/offers prefix,
where `/api/offers/campaigns` sat in the same namespace as `/api/offers/{id}`.
"""

import json
import logging
import secrets
import threading
import time
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from sqlalchemy import and_, case, func, literal, or_
from sqlalchemy.orm import Session

from .. import account_health, alerts, contacts, database, sending
from ..config import settings
from ..database import get_db
from ..dependencies import get_current_user
from ..events_hub import hub
from ..models import (
    AppSettings,
    Campaign,
    CampaignLog,
    CampaignSend,
    ChatDialogPref,
    Lead,
    LeadDatabase,
    Offer,
    TestReplyWatch,
    User,
    WarmupAccount,
)
from ..placeholders import render
from ..plans import (
    ai_offer_allowed,
    ai_offer_plan_names,
    check_campaign_budget,
    check_object_limit,
    require_sending,
)
from ..ratelimit import limiter
from ..schemas import (
    BestSendTime,
    CampaignIn,
    CampaignLaunchIn,
    CampaignLaunchOut,
    CampaignListOut,
    CampaignLogIn,
    CampaignLogListOut,
    CampaignLogOut,
    CampaignOut,
    CampaignPreviewOut,
    CampaignPreviewSampleOut,
    CampaignReadIn,
    CampaignReadOut,
    CampaignReplyIn,
    CampaignReplyOut,
    CampaignSendIn,
    CampaignSendListOut,
    CampaignSendOut,
    CampaignSendResultOut,
    CampaignStopAllOut,
    CampaignUpdate,
    LeadOut,
    OfferKindStat,
    OfferStatRow,
    OfferStatsOut,
    OkOut,
    RecipientsIn,
    RecipientsOut,
    ReplyHeatmapCell,
    ReplyHoursOut,
    TestSendIn,
    TestSendOut,
)
from ..services import log_event, resolve_ai_provider, send_window
from ..services.offers import album_of as offer_album
from ..services.settings import get_settings
from ..telegram_sessions import live_accounts

router = APIRouter(prefix="/api/campaigns", tags=["campaigns"])

# Статуса "scheduled" больше нет: рассылка запускается сразу, без ожидания.
VALID_STATUSES = {"running", "paused", "completed", "cancel_requested", "cancelled"}


def _owned_campaign(campaign_id: int, db: Session, user: User) -> Campaign:
    campaign = db.get(Campaign, campaign_id)
    if not campaign or campaign.owner_id != user.id:
        raise HTTPException(404, "Не найдено")
    return campaign


@router.post("/reply", response_model=CampaignReplyOut)
def lead_replied(
    payload: CampaignReplyIn,
    x_send_secret: str = Header(default="", alias="X-Send-Secret"),
    db: Session = Depends(get_db),
) -> CampaignReplyOut:
    """Принять только первый ответ на нашу реальную или тестовую отправку.

    Входящие личные сообщения Telegram сами по себе не являются лидами. До
    записи события, WebSocket и уведомлений обязательно подтверждаем точную
    пару «владелец + аккаунт-отправитель + адресат». Тестовая отправка имеет
    короткое durable-окно, реальная — успешную строку CampaignSend. Всё прочее
    завершается без побочных эффектов.
    """
    secret = (settings.tg_client_secret or "").strip()
    if not secret or not secrets.compare_digest(x_send_secret or "", secret):
        raise HTTPException(403, "Неверный секрет")

    username = (payload.username or "").strip().lstrip("@")[:64]
    tg_id = (payload.tg_id or "").strip()[:64]
    sender_name = " ".join((payload.sender_name or "").split())[:200]
    preview = " ".join((payload.text or "").split())[:500]
    replied_at = datetime.now(UTC)
    if not payload.account_id or not (tg_id or username):
        return CampaignReplyOut(matched=False, note="Нет точного адресата ответа")

    sender_account = (
        db.query(WarmupAccount)
        .filter(
            WarmupAccount.id == payload.account_id,
            WarmupAccount.owner_id == payload.owner_id,
        )
        .one_or_none()
    )
    if not sender_account:
        return CampaignReplyOut(matched=False, note="Аккаунт-отправитель не найден")

    # Тест имеет приоритет над старой кампанией тому же контакту: пользователь
    # явно запустил короткую проверку и ожидает именно её результат.
    watch_query = db.query(TestReplyWatch).filter(
        TestReplyWatch.owner_id == payload.owner_id,
        TestReplyWatch.account_id == payload.account_id,
        TestReplyWatch.expires_at > replied_at,
    )
    # Раньше здесь было два одинаковых запроса подряд: сначала по tg_id, потом
    # по username. Условия объединены в одно обращение, а приоритет tg_id
    # сохранён сортировкой: точное совпадение по id идёт первым.
    watch_conditions = []
    if tg_id:
        watch_conditions.append(TestReplyWatch.recipient_tg_id == tg_id)
    if username:
        username_match = func.lower(TestReplyWatch.recipient_username) == username.lower()
        if tg_id:
            # Если id был сохранён, несовпадение нельзя обходить совпавшим
            # username: Telegram-username можно переименовать и передать другому.
            username_match = and_(username_match, TestReplyWatch.recipient_tg_id == "")
        watch_conditions.append(username_match)

    test_watch = None
    if watch_conditions:
        priority = (
            case((TestReplyWatch.recipient_tg_id == tg_id, 0), else_=1) if tg_id else literal(0)
        )
        test_watch = (
            watch_query.filter(or_(*watch_conditions))
            .order_by(priority, TestReplyWatch.id.desc())
            .first()
        )

    campaign = None
    lead = None
    send_row = None
    matched_test = False
    if test_watch:
        if test_watch.consumed_at is not None:
            return CampaignReplyOut(matched=False, note="Тестовый ответ уже получен")
        matched_test = bool(
            db.query(TestReplyWatch)
            .filter(
                TestReplyWatch.id == test_watch.id,
                TestReplyWatch.consumed_at.is_(None),
                TestReplyWatch.expires_at > replied_at,
            )
            .update({TestReplyWatch.consumed_at: replied_at}, synchronize_session=False)
        )
        if not matched_test:
            db.rollback()
            return CampaignReplyOut(matched=False, note="Тестовый ответ уже получен")

    if not test_watch:
        send_query = (
            db.query(CampaignSend)
            .join(Lead, Lead.id == CampaignSend.lead_id)
            .filter(
                CampaignSend.owner_id == payload.owner_id,
                CampaignSend.account_id == payload.account_id,
                # Ответ лида — доказательство доставки: uncertain-отправка,
                # на которую ответили, ничем не хуже sent.
                CampaignSend.status.in_(("sent", "uncertain")),
            )
        )
        if tg_id:
            send_row = (
                send_query.filter(Lead.tg_id == tg_id)
                .order_by(CampaignSend.id.desc())
                .first()
            )
        if not send_row and username:
            send_username_filters = [func.lower(Lead.tg_username) == username.lower()]
            if tg_id:
                send_username_filters.append(or_(Lead.tg_id.is_(None), Lead.tg_id == ""))
            send_row = (
                send_query.filter(*send_username_filters)
                .order_by(CampaignSend.id.desc())
                .first()
            )
        if send_row:
            claimed = (
                db.query(CampaignSend)
                .filter(CampaignSend.id == send_row.id, CampaignSend.replied_at.is_(None))
                .update({CampaignSend.replied_at: replied_at}, synchronize_session=False)
            )
            if not claimed:
                send_row = None

    if not matched_test and not send_row:
        db.rollback()
        return CampaignReplyOut(matched=False, note="Нет активной отправки этому контакту")

    if send_row:
        lead = db.get(Lead, send_row.lead_id)
        campaign = db.get(Campaign, send_row.campaign_id)
        if not lead or not campaign:
            db.rollback()
            return CampaignReplyOut(matched=False, note="Связанная отправка больше не существует")
        lead.status = "replied"
        campaign.reply_count = (campaign.reply_count or 0) + 1
        log_who = f"@{username}" if username else sender_name or tg_id
        db.add(
            CampaignLog(
                campaign_id=campaign.id,
                level="info",
                message=f"Ответил {log_who}: {preview or 'без текста'}",
            )
        )
    # Снимаем всё нужное ДО commit. После него SQLAlchemy считает объекты
    # устаревшими, и каждое обращение к campaign.name или lead.title молча
    # уходит в базу за строкой заново. Из-за этого один ответ лида стоил
    # шестнадцати запросов: кампания перечитывалась трижды, аккаунт и лид — ещё
    # по разу.
    sender_label = sending.account_label(sender_account)
    lead_title = lead.title if lead else ""
    lead_id = lead.id if lead else None
    campaign_id = campaign.id if campaign else None
    campaign_name = campaign.name if campaign else ""

    db.commit()

    who = f"@{username}" if username else (sender_name or f"Telegram ID {tg_id}")
    title = sender_name or lead_title or who
    client_label = title if title == who else f"{title} ({who})"
    time_label = replied_at.strftime("%d.%m.%Y %H:%M UTC")
    message = f"Ответ от {client_label} на {sender_label}: {preview or 'без текста'}"

    log_event(
        db,
        payload.owner_id,
        "campaigns",
        message,
        "success",
        "campaign",
        campaign_id,
    )

    hub.publish(
        payload.owner_id,
        {
            "type": "reply",
            "username": username,
            "title": title,
            "lead_name": title,
            "text": preview,
            "campaign_id": campaign_id,
            "lead_id": lead_id,
            "account": sender_label,
            "replied_at": replied_at.isoformat(),
        },
    )
    alert_text = (
        "Новый ответ в Talaria\n"
        f"Время: {time_label}\n"
        f"Клиент: {client_label}\n"
        f"Аккаунт: {sender_label}\n"
        + (f"Рассылка: «{campaign_name}»\n" if campaign_id else "Проверка: тестовая отправка\n")
        + f"Ответ: {preview or 'без текста'}"
    )
    alerts.send_alert(
        alert_text,
        dedupe_key=f"reply:{payload.owner_id}:{payload.account_id}:{tg_id or username}:{preview[:40]}",
    )
    # То же уведомление — владельцу рассылки в личку бота: ответ лида — главное
    # событие сервиса, и ждать его с открытым сайтом человек не обязан.
    # Админам выше уходит своё сообщение, поэтому владельца-админа не дублируем.
    # Одним запросом берём и адресата, и его выключатель: раздельные обращения
    # к users и app_settings стоили бы двух round-trip на каждый ответ лида.
    # Третьим соединением сюда же подшит mute из переписки: диалог «без звука»
    # глушит и это уведомление, а отдельный запрос не влез бы в бюджет
    # обработчика (см. гейт на число запросов).
    owner_row = (
        db.query(
            User.telegram_id,
            AppSettings.reply_notify,
            AppSettings.notify_target_tg_id,
            ChatDialogPref.id,
        )
        .outerjoin(AppSettings, AppSettings.owner_id == User.id)
        .outerjoin(
            ChatDialogPref,
            and_(
                ChatDialogPref.owner_id == User.id,
                ChatDialogPref.account_id == payload.account_id,
                ChatDialogPref.peer_id == str(tg_id or ""),
                ChatDialogPref.muted.is_(True),
            ),
        )
        .filter(User.id == payload.owner_id)
        .one_or_none()
    )
    peer_muted = bool(owner_row and owner_row[3])
    login_tg_id = owner_row[0] if owner_row else None
    # Строки настроек может ещё не быть — считаем, что уведомления включены:
    # человек их не выключал.
    reply_notify = True if owner_row is None or owner_row[1] is None else bool(owner_row[1])
    # Главный аккаунт для уведомлений. Не выбран — остаётся аккаунт входа.
    owner_tg_id = (owner_row[2] if owner_row else None) or login_tg_id
    if (
        owner_tg_id
        and reply_notify
        and not peer_muted
        and str(owner_tg_id) not in {str(a) for a in settings.admin_ids}
    ):
        alerts.notify_user(
            owner_tg_id,
            alert_text + "\n\nОтветьте клиенту в Telegram.",
            dedupe_key=(
                f"reply-user:{payload.owner_id}:{payload.account_id}:"
                f"{tg_id or username}:{preview[:40]}"
            ),
        )

    return CampaignReplyOut(
        ok=True,
        matched=True,
        lead_id=lead_id,
        campaign_id=campaign_id,
        note="Ответ по рассылке засчитан" if campaign_id else "Тестовый ответ получен",
    )


@router.post("/read", response_model=CampaignReadOut)
def lead_read(
    payload: CampaignReadIn,
    x_send_secret: str = Header(default="", alias="X-Send-Secret"),
    db: Session = Depends(get_db),
) -> CampaignReadOut:
    """Отметить прочтение только по фактическому Telegram raw-update."""
    secret = (settings.tg_client_secret or "").strip()
    if not secret or not secrets.compare_digest(x_send_secret or "", secret):
        raise HTTPException(403, "Неверный секрет")
    peer_id = (payload.peer_tg_id or "").strip()
    if not peer_id or payload.max_message_id <= 0:
        return CampaignReadOut(marked=0)
    rows = (
        db.query(CampaignSend)
        .join(Lead, Lead.id == CampaignSend.lead_id)
        .filter(
            CampaignSend.owner_id == payload.owner_id,
            CampaignSend.account_id == payload.account_id,
            # uncertain с message_id не бывает, но прочтение — доказательство
            # доставки, скрывать его из-за статуса не за что.
            CampaignSend.status.in_(("sent", "uncertain")),
            CampaignSend.telegram_message_id.isnot(None),
            CampaignSend.telegram_message_id <= payload.max_message_id,
            CampaignSend.read_at.is_(None),
            Lead.tg_id == peer_id,
        )
        .all()
    )
    now = datetime.now(UTC)
    for row in rows:
        row.read_at = now
    if rows:
        db.commit()
    return CampaignReadOut(marked=len(rows))


@router.get("/offer-stats", response_model=OfferStatsOut)
def offer_stats(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> OfferStatsOut:
    """Какие офферы отправлялись и что из этого вышло.

    Считаем одним запросом с группировкой, а не циклом по офферам: история
    отправок растёт быстро, и N+1 здесь превратился бы в секунды ожидания.

    AI-офферы собираются в одну строку: у каждого лида текст свой, сравнивать
    имеет смысл не тексты, а сам подход против обычных офферов.
    """

    def rate(sent: int, value: int) -> float:
        return round(value / sent * 100, 1) if sent else 0.0

    rows = (
        db.query(
            CampaignSend.offer_id,
            CampaignSend.offer_kind,
            CampaignSend.status,
            func.count(CampaignSend.id),
            func.count(CampaignSend.delivered_at),
            func.count(CampaignSend.read_at),
            func.count(CampaignSend.replied_at),
            func.count(CampaignSend.interested_at),
        )
        .filter(CampaignSend.owner_id == user.id)
        .group_by(CampaignSend.offer_id, CampaignSend.offer_kind, CampaignSend.status)
        .all()
    )

    names = {
        oid: name for oid, name in db.query(Offer.id, Offer.name).filter(Offer.owner_id == user.id)
    }

    per_offer: dict[tuple[int | None, str], dict] = {}
    per_kind: dict[str, dict] = {}
    empty = {"sent": 0, "delivered": 0, "read": 0, "replied": 0, "interested": 0, "failed": 0}
    for offer_id, kind, status, count, delivered, read, replied, interested in rows:
        kind = kind or "regular"
        key = (offer_id, kind)
        acc = per_offer.setdefault(key, empty.copy())
        kind_acc = per_kind.setdefault(kind, empty.copy())
        if status in ("failed", "uncertain"):
            acc["failed"] += count
            kind_acc["failed"] += count
        elif status == "sent":
            acc["sent"] += count
            acc["delivered"] += delivered
            acc["read"] += read
            acc["replied"] += replied
            acc["interested"] += interested
            kind_acc["sent"] += count
            kind_acc["delivered"] += delivered
            kind_acc["read"] += read
            kind_acc["replied"] += replied
            kind_acc["interested"] += interested

    offers_out = [
        OfferStatRow(
            offer_id=offer_id,
            name=(
                "AI-оффер (индивидуальный текст)"
                if kind == "ai"
                else names.get(offer_id) or "Оффер удалён"
            ),
            kind=kind,
            sent=acc["sent"],
            delivered=acc["delivered"],
            read=acc["read"],
            replied=acc["replied"],
            interested=acc["interested"],
            failed=acc["failed"],
            delivery_rate=rate(acc["sent"], acc["delivered"]),
            read_rate=rate(acc["sent"], acc["read"]),
            reply_rate=rate(acc["sent"], acc["replied"]),
            interest_rate=rate(acc["sent"], acc["interested"]),
        )
        for (offer_id, kind), acc in per_offer.items()
    ]
    offers_out.sort(key=lambda r: (-r.sent, r.name))

    labels = {"ai": "AI-офферы", "regular": "Обычные офферы"}
    by_kind = [
        OfferKindStat(
            kind=kind,
            label=labels.get(kind, kind),
            sent=acc["sent"],
            delivered=acc["delivered"],
            read=acc["read"],
            replied=acc["replied"],
            interested=acc["interested"],
            failed=acc["failed"],
            delivery_rate=rate(acc["sent"], acc["delivered"]),
            read_rate=rate(acc["sent"], acc["read"]),
            reply_rate=rate(acc["sent"], acc["replied"]),
            interest_rate=rate(acc["sent"], acc["interested"]),
        )
        for kind, acc in sorted(per_kind.items())
    ]

    total_sent = sum(a["sent"] for a in per_kind.values())
    total_delivered = sum(a["delivered"] for a in per_kind.values())
    total_read = sum(a["read"] for a in per_kind.values())
    total_replied = sum(a["replied"] for a in per_kind.values())
    total_interested = sum(a["interested"] for a in per_kind.values())

    # Время отправки успешных диалогов: фактические ответы, без синтетических
    # «лучших часов». Группируем по sent_at, иначе измеряли бы время ответа.
    heat = {(weekday, hour): 0 for weekday in range(7) for hour in range(24)}
    replied_sends = (
        db.query(CampaignSend.sent_at)
        .filter(
            CampaignSend.owner_id == user.id,
            # Ответ подтверждает доставку — uncertain с ответом равен sent.
            CampaignSend.status.in_(("sent", "uncertain")),
            CampaignSend.replied_at.isnot(None),
        )
        .all()
    )
    for (sent_at,) in replied_sends:
        if sent_at is not None:
            heat[(sent_at.weekday(), sent_at.hour)] += 1
    heatmap = [
        ReplyHeatmapCell(weekday=weekday, hour=hour, replies=count)
        for (weekday, hour), count in heat.items()
    ]
    best = sorted(
        (cell for cell in heatmap if cell.replies),
        key=lambda cell: (-cell.replies, cell.weekday, cell.hour),
    )[:3]
    return OfferStatsOut(
        offers=offers_out,
        by_kind=by_kind,
        total_sent=total_sent,
        total_delivered=total_delivered,
        total_read=total_read,
        total_replied=total_replied,
        total_interested=total_interested,
        delivery_rate=rate(total_sent, total_delivered),
        read_rate=rate(total_sent, total_read),
        reply_rate=rate(total_sent, total_replied),
        interest_rate=rate(total_sent, total_interested),
        reply_heatmap=heatmap,
        best_send_times=[
            BestSendTime(weekday=cell.weekday, hour=cell.hour, replies=cell.replies)
            for cell in best
        ],
    )


@router.get("", response_model=CampaignListOut)
def list_campaigns(
    offset: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Список рассылок пользователя.

    Раньше возвращались все строки без ограничения. С ростом числа кампаний
    это тихо превращается в ответ на мегабайты и зависший интерфейс.
    Дефолт 100 выбран так, чтобы существующие аккаунты не заметили разницы.
    """
    base = db.query(Campaign).filter(Campaign.owner_id == user.id)
    total = base.count()
    rows = base.order_by(Campaign.id.desc()).offset(offset).limit(limit).all()
    return CampaignListOut(
        campaigns=[CampaignOut.model_validate(c) for c in rows],
        total=total,
        offset=offset,
        limit=limit,
    )


@router.post("", response_model=CampaignOut)
def create_campaign(
    data: CampaignIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    # Лимит тарифа на число рассылок.
    check_object_limit(db, user.id, "campaigns")

    # Статус «scheduled», а не «running»: отправку сервис не выполняет — он
    # фиксирует получателей и оффер, а пишет пользователь вручную через
    # Telegram-gateway. Ставить «идёт» и started_at означало бы показывать в
    # интерфейсе рассылку, которой не существует. Статус меняется на running,
    # когда отправку действительно начали (PATCH /api/campaigns/{id}).
    campaign = Campaign(
        owner_id=user.id,
        name=(data.name or f"Рассылка: {data.database_name}")[:200],
        database_name=data.database_name[:200],
        channel=data.channel,
        status="paused",
        total_recipients=data.total_recipients,
        offer_ids=json.dumps(data.offer_ids),
    )
    db.add(campaign)
    db.flush()  # assigns campaign.id without a second round trip

    db.add(
        CampaignLog(
            campaign_id=campaign.id,
            message="Кампания создана: получатели и оффер зафиксированы.",
            level="info",
        )
    )
    db.commit()
    db.refresh(campaign)
    log_event(
        db,
        user.id,
        "campaigns",
        f"Создана рассылка «{campaign.name}» · получателей {campaign.total_recipients}",
        "success",
        "campaign",
        campaign.id,
    )
    return CampaignOut.model_validate(campaign)


@router.post(
    "/test-send",
    response_model=TestSendOut,
    dependencies=[Depends(limiter("test_send"))],
)
def test_send(
    data: TestSendIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> TestSendOut:
    """Отправить одно тестовое сообщение на указанный username.

    Текст берётся либо из оффера (с подстановкой данных первого лида
    пользователя), либо из поля `text` как есть. Само сообщение шлёт
    Telegram-gateway с первого подключённого аккаунта пользователя; бэкенд
    рендерит текст и передаёт его в тот же транспорт, что использует кампания.
    """
    username = (data.username or "").strip().lstrip("@")
    if not username:
        raise HTTPException(400, "Укажите username получателя")

    image: list[str] = []
    if (data.text or "").strip():
        rendered = (data.text or "").strip()
        offer_label = "произвольный текст"
    elif data.offer_id:
        offer = (
            db.query(Offer)
            .filter(Offer.id == data.offer_id, Offer.owner_id == user.id)
            .one_or_none()
        )
        if not offer:
            raise HTTPException(404, "Оффер не найден")
        first_lead = (
            db.query(Lead)
            .join(LeadDatabase, Lead.database_id == LeadDatabase.id)
            .filter(LeadDatabase.owner_id == user.id)
            .order_by(Lead.id.desc())
            .first()
        )
        rendered = render(offer.text or "", first_lead)
        image = offer_album(offer)
        offer_label = f"оффер «{offer.name}»"
    else:
        raise HTTPException(400, "Укажите оффер или текст сообщения")

    if not rendered:
        raise HTTPException(400, "Текст сообщения пуст — отправлять нечего")

    accounts = sending.pick_accounts(db, user.id, [])
    if not accounts:
        raise HTTPException(409, "Нет подключённых Telegram-аккаунтов")
    outcome = sending.send_via_client(
        user.id,
        accounts[0].id,
        username,
        rendered,
        images=image if data.offer_id and not (data.text or "").strip() else [],
        watch_seconds=300,
    )
    ok, detail = outcome
    if not ok:
        raise HTTPException(502, detail or "Telegram-gateway не отправил сообщение")

    sent_at = datetime.now(UTC)
    db.add(
        TestReplyWatch(
            owner_id=user.id,
            account_id=accounts[0].id,
            recipient_tg_id=str(getattr(outcome, "recipient_tg_id", "") or "")[:64],
            recipient_username=str(
                getattr(outcome, "recipient_username", "") or username,
            )
            .strip()
            .lstrip("@")[:64],
            expires_at=sent_at + timedelta(seconds=300),
        )
    )

    log_event(
        db,
        user.id,
        "campaigns",
        f"Тестовая отправка на @{username} ({offer_label})",
        "success",
        "campaign",
        0,
    )
    return TestSendOut(
        ok=True,
        sent_to=username,
        rendered_text=rendered,
        account_label=sending.account_label(accounts[0]),
        sent_at=sent_at,
        note="Сообщение принято Telegram-gateway",
        reply_watch_seconds=300,
    )


# ---------------------------------------------------------------------------
# Получатели и дедупликация
# ---------------------------------------------------------------------------

SEND_STATUSES = {"sent", "failed", "skipped", "uncertain"}

# Сколько лидов разрешено отметить одним запросом. Ограничение есть потому,
# что тело запроса приходит снаружи и список на миллион id положил бы сервис.
MAX_SEND_BATCH = 1000


@router.post("/{campaign_id}/recipients", response_model=RecipientsOut)
def campaign_recipients(
    campaign_id: int,
    data: RecipientsIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> RecipientsOut:
    """Кому реально надо писать в этой рассылке.

    Два правила отсева разные и складываются:

    1. Отписавшиеся (status == "unsubscribed") исключаются всегда и без
       возможности отключить: человек прямо попросил ему не писать.
    2. Дедупликация (skip_contacted) убирает тех, кому уже отправляли — в любой
       из кампаний этого владельца, а не только в текущей. Именно этого ждёт
       человек, когда говорит «не шли тем, кому уже шли».

    Порядок — по оценке вниз: если дневной лимит аккаунта кончится на середине,
    лучше чтобы отправленное ушло самым перспективным каналам.
    """
    _owned_campaign(campaign_id, db, user)

    database = (
        db.query(LeadDatabase)
        .filter(LeadDatabase.id == data.database_id, LeadDatabase.owner_id == user.id)
        .one_or_none()
    )
    if not database:
        raise HTTPException(404, "База лидов не найдена")

    base = db.query(Lead).filter(
        Lead.database_id == database.id,
        Lead.status != "unsubscribed",
    )
    if data.min_score is not None:
        base = base.filter(Lead.score >= max(0, min(100, data.min_score)))

    query = base
    skipped_contacted = 0
    if data.skip_contacted:
        # «Уже писали» = успешные отправки плюс неопределённые (uncertain):
        # при обрыве связи со шлюзом сообщение могло дойти, и повтор дал бы
        # человеку дубль первого сообщения. Честные ошибки (failed) не
        # исключают лида: он ничего не получал.
        contacted = db.query(CampaignSend.lead_id).filter(
            CampaignSend.owner_id == user.id,
            CampaignSend.status.in_(("sent", "uncertain")),
        )
        # Оба счётчика берём одним запросом: раньше здесь было два отдельных
        # COUNT по той же выборке, и на больших базах это дублировало скан.
        eligible_total, remaining = (
            base.with_entities(
                func.count(Lead.id),
                func.count(case((~Lead.id.in_(contacted), Lead.id))),
            )
            .order_by(None)
            .one()
        )
        query = base.filter(~Lead.id.in_(contacted))
        skipped_contacted = eligible_total - remaining
        total = remaining
    else:
        total = base.count()

    offset = max(0, data.offset)
    limit = max(1, min(500, data.limit))
    rows = query.order_by(Lead.score.desc(), Lead.id.desc()).offset(offset).limit(limit).all()
    return RecipientsOut(
        leads=[LeadOut.model_validate(r) for r in rows],
        total=total,
        skipped_contacted=skipped_contacted,
        offset=offset,
        limit=limit,
    )


@router.post("/{campaign_id}/sends", response_model=CampaignSendResultOut)
def record_sends(
    campaign_id: int,
    data: CampaignSendIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> CampaignSendResultOut:
    """Отметить, что лидам отправили (или не смогли отправить).

    Вызов идемпотентен: повторная отметка тех же лидов не создаёт дублей
    и не накручивает счётчики. Это важно именно здесь: отправка идёт снаружи,
    сеть может оборваться после записи, но до ответа, и клиент повторит запрос.
    """
    campaign = _owned_campaign(campaign_id, db, user)
    # Отметка отправки — это тоже отправка: без проверки тарифа счётчики можно
    # было бы накрутить в обход запуска.
    require_sending(db, user.id)

    status = data.status if data.status in SEND_STATUSES else "sent"
    # dict.fromkeys убирает повторы внутри самого запроса, сохраняя порядок.
    ids = list(dict.fromkeys(data.lead_ids))[:MAX_SEND_BATCH]
    if not ids:
        return CampaignSendResultOut(recorded=0, duplicates=0, unknown_leads=0)

    # Чужие лиды отсеиваем сразу: без этой проверки по подобранному id можно
    # было бы писать в чужую историю отправок.
    owned_ids = {
        row[0]
        for row in db.query(Lead.id)
        .join(LeadDatabase, Lead.database_id == LeadDatabase.id)
        .filter(LeadDatabase.owner_id == user.id, Lead.id.in_(ids))
        .all()
    }
    unknown_leads = len(ids) - len(owned_ids)

    existing = {
        row[0]
        for row in db.query(CampaignSend.lead_id)
        .filter(
            CampaignSend.campaign_id == campaign.id,
            CampaignSend.lead_id.in_(owned_ids),
        )
        .all()
    }
    to_add = [i for i in ids if i in owned_ids and i not in existing]

    for lead_id in to_add:
        db.add(
            CampaignSend(
                owner_id=user.id,
                campaign_id=campaign.id,
                lead_id=lead_id,
                status=status,
                error=(data.error or "")[:2000],
            )
        )

    if to_add and status == "sent":
        campaign.sent_count = (campaign.sent_count or 0) + len(to_add)
        if campaign.total_recipients:
            campaign.progress = min(
                100.0,
                round(campaign.sent_count / campaign.total_recipients * 100, 1),
            )
        # Статус лида обновляем одним запросом, а не в цикле по тысяче строк.
        # Переводим только из "new": ручной "replied" затирать нельзя.
        db.query(Lead).filter(Lead.id.in_(to_add), Lead.status == "new").update(
            {Lead.status: "contacted"}, synchronize_session=False
        )

    if to_add and status == "failed":
        campaign.error_count = (campaign.error_count or 0) + len(to_add)

    db.commit()

    if to_add:
        hub.campaign_update(user.id, campaign.id, campaign.progress or 0.0, campaign.status)

    return CampaignSendResultOut(
        recorded=len(to_add),
        duplicates=len(existing),
        unknown_leads=unknown_leads,
    )


@router.get("/{campaign_id}/sends", response_model=CampaignSendListOut)
def list_sends(
    campaign_id: int,
    offset: int = Query(0, ge=0),
    limit: int = Query(200, ge=1, le=1000),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> CampaignSendListOut:
    """Кому уже отправили в этой рассылке."""
    campaign = _owned_campaign(campaign_id, db, user)
    base = db.query(CampaignSend).filter(CampaignSend.campaign_id == campaign.id)
    total = base.count()
    rows = base.order_by(CampaignSend.id.desc()).offset(offset).limit(limit).all()
    return CampaignSendListOut(
        sends=[CampaignSendOut.model_validate(r) for r in rows],
        total=total,
        offset=offset,
        limit=limit,
    )


@router.get("/reply-hours", response_model=ReplyHoursOut)
def reply_hours(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> ReplyHoursOut:
    """В какие часы получатели отвечают чаще.

    Считаем по часу МЕСТНОГО времени получателя, а не нашего: показатель нужен,
    чтобы выбрать окно отправки, а окно тоже живёт в его часовом поясе. Час
    выводится из страны канала и приблизителен — там же, где и в самом окне.
    """
    rows = (
        db.query(Lead.country, CampaignSend.sent_at, CampaignSend.replied_at)
        .join(Lead, Lead.id == CampaignSend.lead_id)
        .filter(
            CampaignSend.owner_id == user.id,
            CampaignSend.status.in_(("sent", "uncertain")),
            CampaignSend.sent_at.isnot(None),
        )
        .all()
    )
    return ReplyHoursOut(**send_window.reply_hours(list(rows)))


@router.post("/stop-all", response_model=CampaignStopAllOut)
def stop_all_campaigns(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> CampaignStopAllOut:
    """Аварийная остановка всех активных рассылок текущего пользователя."""
    rows = (
        db.query(Campaign)
        .filter(
            Campaign.owner_id == user.id,
            Campaign.status.in_(["running", "cancel_requested"]),
        )
        .order_by(Campaign.id)
        .all()
    )
    changed: list[int] = []
    for campaign in rows:
        if campaign.status != "cancel_requested":
            campaign.status = "cancel_requested"
            sending.clog(db, campaign.id, "Запрошена аварийная остановка всех рассылок", "warning")
            changed.append(campaign.id)
    db.commit()
    for campaign in rows:
        hub.campaign_update(user.id, campaign.id, campaign.progress or 0.0, campaign.status)
    if changed:
        log_event(
            db,
            user.id,
            "campaigns",
            f"Запрошена аварийная остановка рассылок: {len(changed)}",
            "warning",
        )
    return CampaignStopAllOut(stopped=len(changed), campaign_ids=changed)


@router.get("/{campaign_id}", response_model=CampaignOut)
def get_campaign(
    campaign_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    return CampaignOut.model_validate(_owned_campaign(campaign_id, db, user))


@router.post("/{campaign_id}/cancel", response_model=CampaignOut)
def cancel_campaign(
    campaign_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> CampaignOut:
    """Попросить фоновый поток остановиться между отправками или во время паузы."""
    campaign = _owned_campaign(campaign_id, db, user)
    if campaign.status not in {"running", "cancel_requested"}:
        raise HTTPException(409, "Эта рассылка уже не выполняется")
    campaign.status = "cancel_requested"
    sending.clog(db, campaign.id, "Пользователь запросил остановку", "warning")
    db.commit()
    db.refresh(campaign)
    hub.campaign_update(user.id, campaign.id, campaign.progress, campaign.status)
    return CampaignOut.model_validate(campaign)


@router.patch("/{campaign_id}", response_model=CampaignOut)
def update_campaign(
    campaign_id: int,
    data: CampaignUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    campaign = _owned_campaign(campaign_id, db, user)

    if data.status is not None:
        if data.status not in VALID_STATUSES:
            raise HTTPException(422, f"Неизвестный статус: {data.status}")
        campaign.status = data.status
        if data.status == "completed":
            campaign.completed_at = datetime.now(UTC)
        elif data.status == "running" and not campaign.started_at:
            campaign.started_at = datetime.now(UTC)

    if data.progress is not None:
        campaign.progress = min(100.0, max(0.0, data.progress))
    if data.sent_count is not None:
        campaign.sent_count = max(0, data.sent_count)
    if data.error_count is not None:
        campaign.error_count = max(0, data.error_count)
    db.commit()
    db.refresh(campaign)
    # Живой апдейт прогресса/статуса — двигает процент на странице без опроса.
    if data.progress is not None or data.status is not None:
        hub.campaign_update(user.id, campaign.id, campaign.progress, campaign.status)
    return CampaignOut.model_validate(campaign)


@router.delete("/{campaign_id}", response_model=OkOut)
def delete_campaign(
    campaign_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    campaign = _owned_campaign(campaign_id, db, user)
    if campaign.status in {"running", "cancel_requested"}:
        raise HTTPException(409, "Сначала остановите активную рассылку")
    # Журнал сносим явно. В модели стоит ondelete="CASCADE", но в базах,
    # созданных до этого, ограничение осталось без каскада, и удаление падало
    # с ошибкой внешнего ключа (миграцию 0009 могли не накатить).
    db.query(CampaignLog).filter(CampaignLog.campaign_id == campaign.id).delete(
        synchronize_session=False
    )
    # Историю отправок НЕ удаляем, а отвязываем: campaign_sends — единственный
    # источник skip_contacted («этому человеку уже писали»). Стереть её вместе
    # с кампанией — значит отправить тем же лидам повторное первое сообщение
    # в следующей рассылке. Отвязка до db.delete не даёт каскаду сработать.
    db.query(CampaignSend).filter(CampaignSend.campaign_id == campaign.id).update(
        {CampaignSend.campaign_id: None}, synchronize_session=False
    )
    db.delete(campaign)
    db.commit()
    return OkOut()


@router.get("/{campaign_id}/logs", response_model=CampaignLogListOut)
def get_campaign_logs(
    campaign_id: int,
    offset: int = Query(0, ge=0),
    limit: int = Query(200, ge=1, le=1000),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Журнал кампании, новые записи сверху.

    Здесь отсутствие лимита было опаснее всего: журнал пополняется на каждое
    отправленное сообщение, то есть растёт линейно числу получателей.

    Сортируем по (timestamp, id): у записей, созданных в одну и ту же микросекунду
    одинаковый timestamp, и без второго ключа порядок между страницами не
    определён — одна и та же запись могла бы показаться дважды или пропасть.
    """
    _owned_campaign(campaign_id, db, user)
    base = db.query(CampaignLog).filter(CampaignLog.campaign_id == campaign_id)
    total = base.count()
    logs = (
        base.order_by(CampaignLog.timestamp.desc(), CampaignLog.id.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )
    return CampaignLogListOut(
        logs=[CampaignLogOut.model_validate(x) for x in logs],
        total=total,
        offset=offset,
        limit=limit,
    )


@router.post("/{campaign_id}/logs", response_model=CampaignLogOut)
def add_campaign_log(
    campaign_id: int,
    data: CampaignLogIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    _owned_campaign(campaign_id, db, user)
    log = CampaignLog(campaign_id=campaign_id, message=data.message, level=data.level)
    db.add(log)
    db.commit()
    db.refresh(log)
    return CampaignLogOut.model_validate(log)


# ---------------------------------------------------------------------------
# Запуск сразу: одна кнопка «Запустить» вместо «Создать рассылку»
# ---------------------------------------------------------------------------


logger = logging.getLogger("outly.campaigns")

# Сколько секунд /launch имеет право потратить на синхронную доверификацию
# pending-кандидатов. Остальные останутся pending и попадут в следующий запуск
# или в верификацию базы — лид не теряется (см. _verify_launch_candidates).
LAUNCH_VERIFY_BUDGET_SECONDS = 20


def _launch_in_background(
    campaign_id: int,
    lead_ids: list[int],
    account_ids: list[int],
    offer_ids: list[int],
    ai_provider: tuple[str, str, str] | None,
    ai_materials: int,
) -> None:
    """Тело фонового запуска: своя сессия БД, никаких объектов из запроса.

    Объекты, привязанные к сессии HTTP-запроса, в поток передавать нельзя —
    сессия закроется вместе с ответом. Поэтому сюда приходят только id,
    и всё перечитывается заново.
    """
    db = database.SessionLocal()
    try:
        campaign = db.query(Campaign).filter(Campaign.id == campaign_id).one()
        leads = (
            db.query(Lead)
            .filter(Lead.id.in_(lead_ids))
            .order_by(Lead.score.desc(), Lead.id.desc())
            .all()
        )
        accounts = [
            a for a in db.query(WarmupAccount).filter(WarmupAccount.id.in_(account_ids)).all()
        ]
        accounts.sort(key=lambda a: account_ids.index(a.id))
        offers = db.query(Offer).filter(Offer.id.in_(offer_ids)).order_by(Offer.id).all()
        sending.run_launch(
            db,
            campaign,
            leads,
            accounts,
            offers,
            ai_provider=ai_provider,
            ai_materials=ai_materials,
        )
    except Exception as exc:
        # Падение фонового потока никто не увидит в HTTP-ответе, поэтому
        # обязательно фиксируем его в статусе кампании, журнале и алертах.
        logger.exception("Фоновая рассылка #%s упала", campaign_id)
        db.rollback()
        try:
            campaign = db.query(Campaign).filter(Campaign.id == campaign_id).one_or_none()
            if campaign is not None:
                campaign.status = "error"
                campaign.completed_at = datetime.now(UTC)
                sending.clog(db, campaign_id, f"Рассылка остановлена ошибкой: {exc}", "error")
                db.commit()
                hub.campaign_update(campaign.owner_id, campaign.id, campaign.progress, "error")
                _notify_campaign_error(db, campaign)
        except Exception:
            logger.exception("Не удалось записать ошибку рассылки #%s", campaign_id)
        alerts.report_error(
            "Рассылка",
            "campaign",
            "Фоновая отправка упала с необработанной ошибкой",
            entity=f"campaign #{campaign_id}",
            details={"Ошибка": str(exc)[:500]},
            dedupe_key=f"campaign-crash:{campaign_id}",
        )
    finally:
        db.close()


def recover_interrupted(db: Session) -> tuple[int, list[int]]:
    """Разобрать рассылки, «идущие» с прошлого запуска процесса.

    Отправка живёт в потоке процесса и не переживает рестарт (деплой, OOM,
    перезагрузка). Кампании, у которых сохранён список получателей, готовятся
    к автопродолжению — их id возвращаются вторым элементом, поток запускает
    lifespan. Старые кампании без списка честно помечаются прерванными: без
    списка получателей продолжать нечего. Ожидавшие остановки закрываются как
    остановленные — подтверждать остановку после рестарта уже некому.
    """
    stuck = (
        db.query(Campaign)
        .filter(Campaign.status.in_(["running", "cancel_requested"]))
        .all()
    )
    interrupted = 0
    resume: list[int] = []
    for campaign in stuck:
        if campaign.status == "cancel_requested":
            campaign.status = "cancelled"
            campaign.completed_at = datetime.now(UTC)
            sending.clog(
                db,
                campaign.id,
                "Остановка подтверждена перезапуском сервера.",
                "warning",
            )
            continue
        planned = _campaign_lead_ids(campaign)
        if planned and _remaining_lead_ids(db, campaign, planned):
            resume.append(campaign.id)
            continue
        if planned:
            # Список есть, но все получатели уже обработаны — рестарт случился
            # между последней отправкой и финальной записью статуса.
            campaign.status = "completed"
            campaign.progress = 100.0
            campaign.completed_at = campaign.completed_at or datetime.now(UTC)
            sending.clog(
                db,
                campaign.id,
                "Готово: все получатели были обработаны ещё до перезапуска сервера.",
                "success",
            )
            continue
        interrupted += 1
        campaign.status = "interrupted"
        campaign.completed_at = campaign.completed_at or datetime.now(UTC)
        sending.clog(
            db,
            campaign.id,
            "Рассылка прервана перезапуском сервера. Уже отправленное учтено; "
            "запустите новую — тем, кому уже писали, повторно не уйдёт.",
            "warn",
        )
    if stuck:
        db.commit()
    return interrupted, resume


def _notify_campaign_error(db: Session, campaign: Campaign) -> None:
    """Пуш владельцу о рассылке, остановленной ошибкой. Сбой пуша глотается."""
    owner = db.get(User, campaign.owner_id)
    if owner is None:
        return
    alerts.notify_user(
        owner.telegram_id,
        f"Talaria: рассылка «{campaign.name}» остановлена ошибкой — "
        f"отправлено {int(campaign.sent_count or 0)} из {int(campaign.total_recipients or 0)}. "
        "Подробности в журнале рассылки.",
        dedupe_key=f"campaign-error:{campaign.id}",
    )


def _campaign_lead_ids(campaign: Campaign) -> list[int]:
    """Сохранённый при запуске список получателей; [] у кампаний до v44."""
    try:
        raw = json.loads(campaign.lead_ids or "[]")
    except ValueError:
        return []
    return [int(v) for v in raw if isinstance(v, int | str) and str(v).isdigit()]


def _remaining_lead_ids(db: Session, campaign: Campaign, planned: list[int]) -> list[int]:
    """Кому из запланированных ещё не писали (нет строки в campaign_sends).

    Любой статус отправки считается обработкой: sent, failed и skipped — по
    ним решение уже принималось, повторять его после рестарта нельзя.
    """
    done = {
        row[0]
        for row in db.query(CampaignSend.lead_id).filter(
            CampaignSend.campaign_id == campaign.id
        )
    }
    return [lead_id for lead_id in planned if lead_id not in done]


def resume_campaign(campaign_id: int) -> None:
    """Продолжить прерванную рестартом рассылку в фоновом потоке."""
    thread = threading.Thread(
        target=_resume_in_background,
        args=(campaign_id,),
        name=f"launch-resume-{campaign_id}",
        daemon=True,
    )
    thread.start()


def _resume_in_background(campaign_id: int) -> None:
    """Тело возобновления: своя сессия, всё перечитывается из БД по id.

    Настройки запуска восстанавливаются из самой кампании (списки получателей,
    аккаунтов и офферов сохранены при старте), провайдер ИИ — из текущих
    настроек сервиса: хранить его ключ в кампании было бы дырой.
    """
    db = database.SessionLocal()
    try:
        campaign = db.query(Campaign).filter(Campaign.id == campaign_id).one()
        planned = _campaign_lead_ids(campaign)
        remaining = _remaining_lead_ids(db, campaign, planned)
        if not remaining:
            campaign.status = "completed"
            campaign.progress = 100.0
            campaign.completed_at = datetime.now(UTC)
            db.commit()
            return

        order = {lead_id: index for index, lead_id in enumerate(remaining)}
        leads = db.query(Lead).filter(Lead.id.in_(remaining)).all()
        leads.sort(key=lambda lead: order.get(lead.id, 0))
        account_ids = [int(v) for v in json.loads(campaign.account_ids or "[]")]
        accounts = [
            a for a in db.query(WarmupAccount).filter(WarmupAccount.id.in_(account_ids)).all()
        ]
        accounts.sort(key=lambda a: account_ids.index(a.id))
        offer_ids = [int(v) for v in json.loads(campaign.offer_ids or "[]")]
        offers = db.query(Offer).filter(Offer.id.in_(offer_ids)).order_by(Offer.id).all()
        if not accounts or not leads or (campaign.offer_mode != "ai" and not offers):
            raise RuntimeError("После рестарта не осталось аккаунтов или получателей")

        ai_provider = None
        if campaign.offer_mode == "ai":
            base_url, api_key, model, _prompt, _tools = resolve_ai_provider(db)
            if not (base_url and api_key and model):
                raise RuntimeError("ИИ-провайдер не подключён — продолжить AI-рассылку нельзя")
            ai_provider = (base_url, api_key, model)

        done = len(planned) - len(remaining)
        sending.clog(
            db,
            campaign.id,
            f"Продолжаем после перезапуска сервера: осталось {len(remaining)} из {len(planned)}.",
            "warning",
        )
        db.commit()
        sending.run_launch(
            db,
            campaign,
            leads,
            accounts,
            offers,
            ai_provider=ai_provider,
            ai_materials=int(campaign.ai_materials or 5),
            resume_from=done,
        )
    except Exception as exc:
        logger.exception("Возобновление рассылки #%s упало", campaign_id)
        db.rollback()
        try:
            campaign = db.query(Campaign).filter(Campaign.id == campaign_id).one_or_none()
            if campaign is not None:
                campaign.status = "error"
                campaign.completed_at = datetime.now(UTC)
                sending.clog(db, campaign_id, f"Возобновление остановлено ошибкой: {exc}", "error")
                db.commit()
                hub.campaign_update(campaign.owner_id, campaign.id, campaign.progress, "error")
                _notify_campaign_error(db, campaign)
        except Exception:
            logger.exception("Не удалось записать ошибку возобновления #%s", campaign_id)
        alerts.report_error(
            "Рассылка",
            "campaign",
            "Автовозобновление после рестарта упало с ошибкой",
            entity=f"campaign #{campaign_id}",
            details={"Ошибка": str(exc)[:500]},
            dedupe_key=f"campaign-resume-crash:{campaign_id}",
        )
    finally:
        db.close()


def _owned_database(db: Session, user: User, database_id: int) -> LeadDatabase:
    """База лидов пользователя или 404. Отдельно от отбора лидов: запуск должен
    проверить базу раньше тарифа и офферов — «базы нет» полезнее, чем разбор
    настроек рассылки, которая всё равно никуда не поедет."""
    database = (
        db.query(LeadDatabase)
        .filter(LeadDatabase.id == database_id, LeadDatabase.owner_id == user.id)
        .one_or_none()
    )
    if not database:
        raise HTTPException(404, "База лидов не найдена")
    return database


def _window_note(skipped_window: int) -> str:
    """Фраза об отложенных получателях. Пустая, если откладывать было некого."""
    if not skipped_window:
        return ""
    return (
        f" Отложено до дневного времени: {skipped_window} —"
        " у них сейчас ночь по местному времени."
    )


def _pick_launch_leads(
    db: Session, user: User, data: CampaignLaunchIn, database: LeadDatabase
) -> tuple[list[Lead], int, int, int]:
    """Отбор получателей — общий для запуска и предпросмотра.

    Одна функция, а не два похожих блока: если предпросмотр отбирает лидов
    хоть чуть-чуть иначе, чем запуск, он показывает человеку не тех, кому
    реально уйдут сообщения, — то есть врёт.

    Возвращает: (лиды, без Telegram, уже писали, всего кандидатов, ночь у лида).
    """
    base = db.query(Lead).filter(
        Lead.database_id == database.id,
        Lead.status != "unsubscribed",
    )
    if data.min_score is not None:
        base = base.filter(Lead.score >= max(0, min(100, data.min_score)))
    candidates_total = base.count()

    skipped_contacted = 0
    query = base
    if data.skip_contacted:
        # sent + uncertain: см. комментарий про дедупликацию в launch_preview.
        contacted = db.query(CampaignSend.lead_id).filter(
            CampaignSend.owner_id == user.id,
            CampaignSend.status.in_(("sent", "uncertain")),
        )
        query = base.filter(~Lead.id.in_(contacted))
        skipped_contacted = candidates_total - query.count()

    take = max(1, min(sending.MAX_PER_LAUNCH, data.limit))
    ready = query.filter(
        Lead.tg_username.isnot(None),
        Lead.tg_username != "",
        Lead.tg_status.in_(["verified", "unknown"]),
    )
    ready_total = ready.count()
    leads = ready.order_by(Lead.score.desc(), Lead.id.desc()).limit(take).all()
    # Считаем по всей отфильтрованной базе, а не только внутри первого limit:
    # иначе UI показывал «без Telegram: 0», хотя ниже первой страницы их тысячи.
    skipped_no_tg = max(0, query.count() - ready_total)

    # Окно отправки отсекает тех, у кого сейчас ночь. Отложенные не теряются:
    # отметки об отправке у них не появляется, и следующий запуск возьмёт их
    # снова — уже днём.
    skipped_window = 0
    settings_row = get_settings(db, user.id)
    if getattr(settings_row, "send_window_enabled", False):
        start, end = send_window.normalize_window(
            settings_row.send_window_start, settings_row.send_window_end
        )
        leads, waiting = send_window.split_by_window(leads, start, end)
        skipped_window = len(waiting)

    return leads, skipped_no_tg, skipped_contacted, candidates_total, skipped_window


def _verify_launch_candidates(
    db: Session,
    user: User,
    data: CampaignLaunchIn,
    database: LeadDatabase,
    accounts: list[WarmupAccount],
) -> tuple[int, int]:
    """Перед отправкой доверифицировать лидов со статусом pending.

    Старые строки `unknown` с уже заполненным tg_username остаются совместимыми.
    Новые результаты парсинга и импорта имеют `pending` и проходят этот шлюз.
    """
    query = db.query(Lead).filter(
        Lead.database_id == database.id,
        Lead.status != "unsubscribed",
        Lead.tg_status == "pending",
    )
    if data.min_score is not None:
        query = query.filter(Lead.score >= max(0, min(100, data.min_score)))
    account_health.release_expired(db, user.id)
    if data.skip_contacted:
        # sent + uncertain: см. комментарий про дедупликацию в launch_preview.
        contacted = db.query(CampaignSend.lead_id).filter(
            CampaignSend.owner_id == user.id,
            CampaignSend.status.in_(("sent", "uncertain")),
        )
        query = query.filter(~Lead.id.in_(contacted))

    # Берём запас сверх размера запуска: часть кандидатов окажется каналами,
    # ботами или недоступными профилями.
    cap = min(sending.MAX_PER_LAUNCH * 3, max(data.limit * 3, data.limit))
    rows = query.order_by(Lead.score.desc(), Lead.id.desc()).limit(cap).all()
    # Верификацию выполняют только явно разрешённые для неё подключённые слоты.
    # Аккаунты-отправители и аккаунты-проверяющие могут быть разными: это важно
    # для прогрева и для распределения риска между рабочими аккаунтами.
    account_ids = [account.id for account in accounts if account.verification_enabled]
    if not account_ids:
        verification_accounts = (
            db.query(WarmupAccount)
            .filter(
                WarmupAccount.owner_id == user.id,
                or_(
                    WarmupAccount.connected_at.isnot(None),
                    WarmupAccount.is_primary.is_(False)
                    & WarmupAccount.tg_id.isnot(None)
                    & (WarmupAccount.tg_id != ""),
                ),
                WarmupAccount.tg_id.isnot(None),
                WarmupAccount.tg_id != "",
                WarmupAccount.verification_enabled.is_(True),
                WarmupAccount.operational_status == "ready",
            )
            .order_by(WarmupAccount.id)
            .all()
        )
        account_ids = [
            account.id
            for account in live_accounts(db, verification_accounts, fail_closed=True)
        ]
    if not account_ids:
        return 0, 0
    verified = 0
    checked = 0
    # Бюджет времени: верификация выполняется синхронно внутри HTTP-запроса
    # /launch, а один resolve может занять до 20 с. Без предела 600 кандидатов
    # держали бы соединение часами — обрыв по таймауту прокси, повторный клик
    # и лишние флуд-лимиты. Непроверенные остаются pending: их добёрет
    # верификация базы или следующий запуск.
    deadline = time.monotonic() + LAUNCH_VERIFY_BUDGET_SECONDS
    for index, lead in enumerate(rows):
        if time.monotonic() >= deadline:
            logger.info(
                "Верификация перед запуском остановлена по бюджету времени: "
                "проверено %s из %s кандидатов",
                checked,
                len(rows),
            )
            break
        profile = contacts.verify_lead(
            lead,
            user.id,
            account_ids,
            start_index=index,
        )
        checked += 1
        if profile.sendable:
            verified += 1
    if checked:
        db.commit()
    return checked, verified


@router.post("/launch-preview", response_model=CampaignPreviewOut)
def launch_preview(
    data: CampaignLaunchIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> CampaignPreviewOut:
    """Показать, что уйдёт лидам, — до запуска и без отправки.

    Отбор получателей тот же, что у /launch (общий _pick_launch_leads), тексты
    строятся тем же кодом, что при реальной отправке. Ничего не пишем в базу и
    не требуем подключённых аккаунтов: смотреть примеры можно и до подключения.
    """
    database = _owned_database(db, user, data.database_id)
    leads, skipped_no_tg, skipped_contacted, _total, skipped_window = _pick_launch_leads(
        db, user, data, database
    )
    if not leads:
        return CampaignPreviewOut(
            mode=data.offer_mode,
            skipped_no_telegram=skipped_no_tg,
            skipped_contacted=skipped_contacted,
            note="По этим условиям получателей нет: либо ни у кого нет Telegram, "
            "либо всем уже писали. Ослабьте фильтры или соберите новую базу.",
        )

    samples: list[CampaignPreviewSampleOut] = []
    ai_mode = data.offer_mode == "ai"
    if ai_mode:
        template = (data.ai_template or "").strip() or (user.ai_offer_template or "").strip()
        if not template:
            raise HTTPException(
                400,
                "Скелет ИИ-оффера не настроен. Откройте «Офферы» → «ИИ-оффер» и "
                "опишите, как примерно должен выглядеть оффер.",
            )
        # ИИ в предпросмотре не зовём: генерация платная и медленная, а её
        # результат для каждого лида всё равно другой. Показываем скелет с
        # подставленными данными лида — «примерный вид» письма.
        for lead in leads[:3]:
            samples.append(
                CampaignPreviewSampleOut(
                    lead_id=lead.id,
                    title=lead.title or "",
                    username=sending.recipient_username(lead),
                    score=lead.score,
                    offer_name="ИИ-оффер (скелет)",
                    text=render(template, lead),
                )
            )
        note = (
            "Это скелет с данными реальных лидов. Финальный текст ИИ напишет "
            "каждому лиду свой — по этому скелету и данным канала."
        )
    else:
        query = db.query(Offer).filter(Offer.owner_id == user.id)
        if data.offer_ids:
            query = query.filter(Offer.id.in_(data.offer_ids))
        offers = query.order_by(Offer.id).all()
        if not offers:
            raise HTTPException(
                400,
                "Нет ни одного оффера. Создайте оффер на странице «Офферы» — "
                "в рассылке они чередуются автоматически.",
            )
        # Ровно та же ротация, что в run_launch: лид №1 — первый оффер и т.д.
        for index, lead in enumerate(leads[:3]):
            offer = offers[index % len(offers)]
            samples.append(
                CampaignPreviewSampleOut(
                    lead_id=lead.id,
                    title=lead.title or "",
                    username=sending.recipient_username(lead),
                    score=lead.score,
                    offer_name=offer.name or "",
                    text=sending.build_regular_text(offer, lead, bool(data.spintax)),
                )
            )
        note = f"Офферы чередуются по лидам ({len(offers)} шт.)." + (
            " Спинтакс включён: у каждого лида свой вариант текста." if data.spintax else ""
        )

    return CampaignPreviewOut(
        mode=data.offer_mode,
        recipients=len(leads),
        skipped_no_telegram=skipped_no_tg,
        skipped_contacted=skipped_contacted,
        skipped_window=skipped_window,
        samples=samples,
        note=note + _window_note(skipped_window),
    )


@router.post(
    "/launch",
    response_model=CampaignLaunchOut,
    dependencies=[Depends(limiter("test_send"))],
)
def launch_campaign(
    data: CampaignLaunchIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> CampaignLaunchOut:
    """Создать рассылку и сразу отправить её с подключённых аккаунтов.

    Раньше было три шага (создать → подобрать получателей → отметить отправки
    руками), и между ними рассылка висела в полусостоянии. Теперь одна кнопка.

    По умолчанию ответ приходит сразу, отправка идёт в фоне; `wait=true`
    оставляет синхронный режим для API-тестов. Прогресс виден в живом журнале.
    """
    require_sending(db, user.id)
    database = _owned_database(db, user, data.database_id)

    active = (
        db.query(Campaign.id)
        .filter(
            Campaign.owner_id == user.id,
            Campaign.status.in_(["running", "cancel_requested"]),
        )
        .first()
    )
    if active:
        raise HTTPException(
            409,
            f"Уже выполняется рассылка #{active[0]}. Дождитесь её завершения или остановите.",
        )

    # --- тип оффера и его доступность в тарифе ---
    ai_mode = data.offer_mode == "ai"
    ai_provider = None
    ai_template = ""
    ai_materials = 5
    offers: list[Offer] = []
    if ai_mode:
        if not ai_offer_allowed(db, user.id):
            raise HTTPException(
                403,
                "ИИ-оффер доступен в тарифе "
                + (ai_offer_plan_names() or "выше")
                + ". На текущем тарифе доступны обычные офферы со спинтаксом.",
            )
        # Скелет берём из настроек пользователя: в рассылке он больше не
        # вводится — там только переключатель типа оффера. Поле в запросе
        # оставлено для совместимости и как разовое переопределение.
        ai_template = (data.ai_template or "").strip() or (user.ai_offer_template or "").strip()
        if not ai_template:
            raise HTTPException(
                400,
                "Скелет ИИ-оффера не настроен. Откройте «Офферы» → «ИИ-оффер» и "
                "опишите, как примерно должен выглядеть оффер.",
            )
        ai_materials = int(user.ai_offer_materials or 5)
        base_url, api_key, model, _prompt, _tools = resolve_ai_provider(db)
        if not (base_url and api_key and model):
            raise HTTPException(
                409,
                "ИИ-провайдер не подключён владельцем сервиса",
            )
        ai_provider = (base_url, api_key, model)
    else:
        # Офферы поштучно больше не выбираются: берутся все офферы владельца и
        # чередуются по лидам. Выбор конкретных офферов оставлен как
        # необязательное сужение (старые клиенты и будущие сценарии).
        query = db.query(Offer).filter(Offer.owner_id == user.id)
        if data.offer_ids:
            query = query.filter(Offer.id.in_(data.offer_ids))
        offers = query.order_by(Offer.id).all()
        if not offers:
            raise HTTPException(
                400,
                "Нет ни одного оффера. Создайте оффер на странице «Офферы» — "
                "в рассылке они чередуются автоматически.",
            )

    # Тариф проверяем после валидации типа оффера, но до обращения к Telegram.
    # Возвращённый лимит уже учитывает месячный остаток и потолок одного запуска.
    allowed_limit = check_campaign_budget(db, user.id, int(data.limit or 1), ai_mode=ai_mode)
    data = data.model_copy(update={"limit": allowed_limit})

    # --- аккаунты отправителей ---
    accounts = sending.pick_accounts(db, user.id, data.account_ids)
    if not accounts:
        raise HTTPException(
            409,
            "Нет подключённых аккаунтов. Подключите аккаунт в настройках.",
        )

    checked, verified = _verify_launch_candidates(db, user, data, database, accounts)
    if checked:
        log_event(
            db,
            user.id,
            "campaigns",
            f"Перед рассылкой проверено через Telegram: {checked}, подтверждено: {verified}",
            "success" if verified else "warn",
            "database",
            database.id,
        )

    # --- кому пишем: тот же отбор, что в предпросмотре ---
    leads, skipped_no_tg, skipped_contacted, candidates_total, skipped_window = _pick_launch_leads(
        db, user, data, database
    )
    if not leads:
        raise HTTPException(
            409,
            "Нет подтверждённых личных Telegram-контактов. Откройте базу лидов "
            "и запустите верификацию — каналы и боты в рассылку не допускаются.",
        )

    # Сериализуем создание запуска на строке пользователя и повторяем проверку:
    # два одновременных HTTP-запроса не должны оба увидеть «активных нет».
    db.query(User.id).filter(User.id == user.id).with_for_update().one()
    active = (
        db.query(Campaign.id)
        .filter(
            Campaign.owner_id == user.id,
            Campaign.status.in_(["running", "cancel_requested"]),
        )
        .first()
    )
    if active:
        raise HTTPException(
            409,
            f"Уже выполняется рассылка #{active[0]}. Дождитесь её завершения или остановите.",
        )

    check_object_limit(db, user.id, "campaigns")

    campaign = Campaign(
        owner_id=user.id,
        name=(data.name or f"Рассылка: {database.name}")[:200],
        database_name=database.name[:200],
        channel="Telegram",
        status="running",
        total_recipients=len(leads),
        offer_ids=json.dumps([o.id for o in offers]),
        offer_mode="ai" if ai_mode else "regular",
        ai_template=ai_template if ai_mode else "",
        spintax=bool(data.spintax) and not ai_mode,
        account_ids=json.dumps([a.id for a in accounts]),
        # Полный список получателей — опора автовозобновления: после рестарта
        # остаток вычисляется вычитанием уже записанных campaign_sends.
        lead_ids=json.dumps([lead.id for lead in leads]),
        ai_materials=ai_materials,
        started_at=datetime.now(UTC),
    )
    db.add(campaign)
    db.commit()
    db.refresh(campaign)

    if data.wait:
        # Синхронный путь — для автоматизации через API и тестов: ответ придёт
        # после всей партии, поэтому годится только для маленьких запусков.
        result = sending.run_launch(
            db,
            campaign,
            leads,
            accounts,
            offers,
            ai_provider=ai_provider,
            ai_materials=ai_materials,
        )
        db.refresh(campaign)
        note = (
            f"Отправлено {result['sent']} из {len(leads)}."
            + (f" Без Telegram: {skipped_no_tg}." if skipped_no_tg else "")
            + (f" Уже писали раньше: {skipped_contacted}." if skipped_contacted else "")
            + _window_note(skipped_window)
        )
        return CampaignLaunchOut(
            campaign_id=campaign.id,
            campaign=CampaignOut.model_validate(campaign),
            sent=result["sent"],
            failed=result["failed"],
            skipped_no_telegram=skipped_no_tg,
            skipped_contacted=skipped_contacted,
            skipped_window=skipped_window,
            total_candidates=candidates_total,
            accounts_used=[sending.account_label(a) for a in accounts],
            problems=result["problems"],
            note=note,
        )

    # Обычный путь: отвечаем сразу, отправка идёт в фоне. Держать HTTP-запрос
    # открытым на всю партию нельзя — с паузами между сообщениями даже 50 лидов
    # упираются в таймаут прокси, и обрыв соединения раньше ронял запуск.
    thread = threading.Thread(
        target=_launch_in_background,
        args=(
            campaign.id,
            [lead.id for lead in leads],
            [a.id for a in accounts],
            [o.id for o in offers],
            ai_provider,
            ai_materials,
        ),
        name=f"launch-{campaign.id}",
        daemon=True,
    )
    thread.start()

    note = (
        f"Рассылка запущена: {len(leads)} получателей."
        + (f" Без Telegram: {skipped_no_tg}." if skipped_no_tg else "")
        + (f" Уже писали раньше: {skipped_contacted}." if skipped_contacted else "")
        + _window_note(skipped_window)
        + " Ход отправки — в журнале ниже."
    )
    return CampaignLaunchOut(
        campaign_id=campaign.id,
        campaign=CampaignOut.model_validate(campaign),
        background=True,
        skipped_no_telegram=skipped_no_tg,
        skipped_contacted=skipped_contacted,
        skipped_window=skipped_window,
        total_candidates=candidates_total,
        accounts_used=[sending.account_label(a) for a in accounts],
        note=note,
    )
