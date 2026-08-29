"""Базы лидов: список, поиск, просмотр, удаление."""

from __future__ import annotations

import csv
import io
import logging
import threading
from datetime import UTC, datetime
from typing import overload

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    Response,
    UploadFile,
)
from sqlalchemy import func, or_
from sqlalchemy.orm import Query as SQLQuery
from sqlalchemy.orm import Session

from .. import contacts, database
from ..account_health import release_expired
from ..database import get_db
from ..dependencies import get_current_user
from ..models import CampaignSend, Lead, LeadDatabase, LeadVerificationJob, User, WarmupAccount
from ..schemas import (
    LeadBulkDeleteIn,
    LeadBulkResultOut,
    LeadBulkStatusIn,
    LeadDatabaseListOut,
    LeadDatabaseOut,
    LeadDatabaseRename,
    LeadImportResultOut,
    LeadListOut,
    LeadNoteIn,
    LeadOut,
    LeadTelegramIn,
    LeadTelegramOut,
    LeadTelegramResolveIn,
    LeadTelegramResolveOut,
    LeadTelegramSummaryOut,
    LeadVerificationJobOut,
    LeadVerificationStartIn,
    OkOut,
)
from ..services import compute_lead_score, log_event
from ..telegram_sessions import live_accounts

router = APIRouter(prefix="/api/leads", tags=["leads"])
logger = logging.getLogger("outly.lead_verification")

LEAD_STATUSES = {"new", "contacted", "replied", "interested", "unsubscribed"}


def _mark_interested_sends(db: Session, owner_id: int, lead_ids: list[int]) -> None:
    """Зафиксировать бизнес-результат на последней отправке каждому лиду."""
    if not lead_ids:
        return
    rows = (
        db.query(CampaignSend)
        .filter(
            CampaignSend.owner_id == owner_id,
            CampaignSend.lead_id.in_(lead_ids),
            CampaignSend.status == "sent",
        )
        .order_by(CampaignSend.lead_id, CampaignSend.id.desc())
        .all()
    )
    now = datetime.now(UTC)
    seen: set[int] = set()
    for row in rows:
        if row.lead_id in seen:
            continue
        seen.add(row.lead_id)
        if row.interested_at is None:
            row.interested_at = now


def _owned_db(database_id: int, db: Session, user: User) -> LeadDatabase:
    row = db.get(LeadDatabase, database_id)
    if not row or row.owner_id != user.id:
        raise HTTPException(404, "База не найдена")
    return row


def _counts(db: Session, owner_id: int) -> dict[int, int]:
    """Число лидов по базам одним запросом — иначе N+1 на списке."""
    rows = (
        db.query(Lead.database_id, func.count(Lead.id))
        .join(LeadDatabase, Lead.database_id == LeadDatabase.id)
        .filter(LeadDatabase.owner_id == owner_id)
        .group_by(Lead.database_id)
        .all()
    )
    return {db_id: int(count) for db_id, count in rows}


@overload
def _verification_job_out(job: None) -> None: ...


@overload
def _verification_job_out(job: LeadVerificationJob) -> LeadVerificationJobOut: ...


def _verification_job_out(job: LeadVerificationJob | None) -> LeadVerificationJobOut | None:
    if job is None:
        return None
    progress = (
        round(job.processed * 100 / job.total)
        if job.total
        else (100 if job.status == "completed" else 0)
    )
    return LeadVerificationJobOut(
        id=job.id,
        database_id=job.database_id,
        status=job.status or "queued",
        retry_errors=bool(job.retry_errors),
        total=int(job.total or 0),
        processed=int(job.processed or 0),
        verified=int(job.verified or 0),
        unresolved=int(job.unresolved or 0),
        errors=int(job.errors or 0),
        progress=max(0, min(100, progress)),
        current_lead_id=job.current_lead_id,
        current_title=job.current_title or "",
        last_result=job.last_result or "",
        last_error=job.last_error or "",
        started_at=job.started_at,
        completed_at=job.completed_at,
        created_at=job.created_at,
        updated_at=job.updated_at,
    )


def _telegram_summary(db: Session, database: LeadDatabase, owner_id: int) -> LeadTelegramSummaryOut:
    grouped = dict(
        db.query(Lead.tg_status, func.count(Lead.id))
        .filter(Lead.database_id == database.id)
        .group_by(Lead.tg_status)
        .all()
    )
    total = sum(int(value) for value in grouped.values())
    pending = int(grouped.get("pending", 0)) + int(grouped.get("unknown", 0))
    verified = int(grouped.get("verified", 0))
    channels = int(grouped.get("channel", 0))
    bots = int(grouped.get("bot", 0))
    missing = int(grouped.get("missing", 0))
    errors = int(grouped.get("error", 0)) + int(grouped.get("rejected", 0))
    finished = max(0, total - pending)
    progress = round(finished * 100 / total) if total else 0
    if pending:
        pipeline_status = "verifying" if finished else "youtube_ready"
    elif verified:
        pipeline_status = "ready"
    else:
        pipeline_status = "partial"
    if database.pipeline_status != pipeline_status:
        database.pipeline_status = pipeline_status
    release_expired(db, owner_id)
    accounts_connected = (
        db.query(func.count(WarmupAccount.id))
        .filter(
            WarmupAccount.owner_id == owner_id,
            or_(
                WarmupAccount.connected_at.isnot(None),
                WarmupAccount.is_primary.is_(False)
                & WarmupAccount.tg_id.isnot(None)
                & (WarmupAccount.tg_id != ""),
            ),
            WarmupAccount.verification_enabled.is_(True),
            WarmupAccount.operational_status == "ready",
        )
        .scalar()
        or 0
    )
    latest_job = (
        db.query(LeadVerificationJob)
        .filter(
            LeadVerificationJob.database_id == database.id,
            LeadVerificationJob.owner_id == owner_id,
        )
        .order_by(LeadVerificationJob.id.desc())
        .first()
    )
    return LeadTelegramSummaryOut(
        database_id=database.id,
        total=total,
        candidates=total - missing,
        pending=pending,
        verified=verified,
        channels=channels,
        bots=bots,
        missing=missing,
        errors=errors,
        progress=progress,
        pipeline_status=pipeline_status,
        accounts_connected=int(accounts_connected),
        job=_verification_job_out(latest_job),
    )


@router.get(
    "/databases/{database_id}/telegram-summary",
    response_model=LeadTelegramSummaryOut,
)
def telegram_summary(
    database_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> LeadTelegramSummaryOut:
    database = _owned_db(database_id, db, user)
    result = _telegram_summary(db, database, user.id)
    db.commit()
    return result


@router.get("/databases", response_model=LeadDatabaseListOut)
def list_databases(
    q: str = Query("", description="поиск по названию и запросу базы"),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> LeadDatabaseListOut:
    query = db.query(LeadDatabase).filter(LeadDatabase.owner_id == user.id)
    if q.strip():
        pattern = f"%{q.strip()}%"
        query = query.filter(
            or_(LeadDatabase.name.ilike(pattern), LeadDatabase.query.ilike(pattern))
        )
    rows = query.order_by(LeadDatabase.id.desc()).all()
    counts = _counts(db, user.id)

    out = [
        LeadDatabaseOut(
            id=r.id,
            name=r.name,
            source=r.source,
            mode=r.mode,
            query=r.query,
            filters_json=r.filters_json or "{}",
            pipeline_status=r.pipeline_status or "youtube_ready",
            units_spent=r.units_spent,
            created_at=r.created_at,
            leads_count=counts.get(r.id, 0),
        )
        for r in rows
    ]
    # total_leads — по всем базам пользователя, независимо от фильтра поиска.
    return LeadDatabaseListOut(databases=out, total_leads=sum(counts.values()))


@router.patch("/databases/{database_id}", response_model=LeadDatabaseOut)
def rename_database(
    database_id: int,
    payload: LeadDatabaseRename,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> LeadDatabaseOut:
    row = _owned_db(database_id, db, user)
    name = payload.name.strip()
    if not name:
        raise HTTPException(400, "Пустое название")
    row.name = name[:200]
    db.commit()
    db.refresh(row)
    return LeadDatabaseOut(
        id=row.id,
        name=row.name,
        source=row.source,
        mode=row.mode,
        query=row.query,
        filters_json=row.filters_json or "{}",
        pipeline_status=row.pipeline_status or "youtube_ready",
        units_spent=row.units_spent,
        created_at=row.created_at,
        leads_count=_counts(db, user.id).get(row.id, 0),
    )


@router.delete("/databases/{database_id}", response_model=OkOut)
def delete_database(
    database_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> OkOut:
    row = _owned_db(database_id, db, user)
    name = row.name
    db.delete(row)  # лиды уходят каскадом
    db.commit()
    log_event(db, user.id, "leads", f"База «{name}» удалена вместе с лидами", "warn")
    return OkOut()


@router.get("", response_model=LeadListOut)
def list_leads(
    database_id: int | None = Query(None, description="без него — поиск по всем базам"),
    q: str = Query("", description="поиск по названию, описанию, ссылке, ключевому слову"),
    status: str = Query(""),
    country: str = Query(""),
    min_subs: int | None = Query(None),
    max_subs: int | None = Query(None),
    sort: str = Query("subs_desc", description="subs_desc | subs_asc | title | newest"),
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=500),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> LeadListOut:
    """Лиды с фильтрами. Без database_id ищет по всем базам пользователя."""
    query = (
        db.query(Lead)
        .join(LeadDatabase, Lead.database_id == LeadDatabase.id)
        .filter(LeadDatabase.owner_id == user.id)
    )
    if database_id is not None:
        _owned_db(database_id, db, user)
        query = query.filter(Lead.database_id == database_id)
    if q.strip():
        pattern = f"%{q.strip()}%"
        query = query.filter(
            or_(
                Lead.title.ilike(pattern),
                Lead.description.ilike(pattern),
                Lead.custom_url.ilike(pattern),
                Lead.matched_keyword.ilike(pattern),
                Lead.tg_username.ilike(pattern),
                Lead.telegram_channel.ilike(pattern),
                Lead.contact_email.ilike(pattern),
            )
        )
    if status.strip():
        query = query.filter(Lead.status == status.strip())
    if country.strip():
        query = query.filter(Lead.country == country.strip().upper())
    # Каналы со скрытым счётчиком (subscribers IS NULL) под фильтр не подходят:
    # их число подписчиков неизвестно, а не равно нулю.
    if min_subs is not None:
        query = query.filter(Lead.subscribers.isnot(None), Lead.subscribers >= min_subs)
    if max_subs is not None:
        query = query.filter(Lead.subscribers.isnot(None), Lead.subscribers <= max_subs)

    total = query.count()

    orders = {
        "subs_desc": Lead.subscribers.desc().nullslast(),
        "subs_asc": Lead.subscribers.asc().nullslast(),
        "title": Lead.title.asc(),
        "newest": Lead.id.desc(),
        # При равном балле добавляем id вторым ключом: без него порядок строк
        # с одинаковым score не определён, и при листании один и тот же лид
        # может попасть на две страницы или не попасть ни на одну.
        "score_desc": Lead.score.desc(),
        "score_asc": Lead.score.asc(),
    }
    if sort in {"score_desc", "score_asc"}:
        query = query.order_by(orders[sort], Lead.id.desc())
        rows = query.offset(offset).limit(limit).all()
        return LeadListOut(
            leads=[LeadOut.model_validate(r) for r in rows],
            total=total,
            offset=offset,
            limit=limit,
        )
    query = query.order_by(orders.get(sort, orders["subs_desc"]))

    rows = query.offset(offset).limit(limit).all()
    return LeadListOut(
        leads=[LeadOut.model_validate(r) for r in rows],
        total=total,
        offset=offset,
        limit=limit,
    )


# ---------------------------------------------------------------------------
# Экспорт и импорт CSV
# ---------------------------------------------------------------------------

# Порядок колонок общий для экспорта и импорта: выгруженный файл должен
# загружаться обратно без правки руками.
CSV_COLUMNS = [
    "channel_id",
    "title",
    "custom_url",
    "country",
    "subscribers",
    "video_count",
    "view_count",
    "published_at",
    "matched_keyword",
    "status",
    "score",
    "telegram_channel",
    "tg_username",
    "contact_email",
    "tg_status",
    "description",
]

# Разделитель — точка с запятой. Excel в русской локали открывает файл с
# запятыми одной колонкой-кашей, и это первое, на что жалуются. При импорте
# принимаем оба варианта.
CSV_DELIMITER = ";"

# Потолок строк за один файл. Защищает и от случайного гигабайтного файла,
# и от того, что весь разбор идёт в памяти.
MAX_IMPORT_ROWS = 50_000
MAX_EXPORT_ROWS = 50_000
MAX_REPORTED_ERRORS = 10
MAX_IMPORT_BYTES = 5_000_000


def _csv_safe(value: object) -> object:
    """Не дать Excel выполнить значение из CSV как формулу."""
    if isinstance(value, str) and value.lstrip().startswith(("=", "+", "-", "@")):
        return "'" + value
    return value


def _csv_int(raw: str) -> int | None:
    """Число из CSV или None.

    Пустая ячейка и мусор дают None, а не ноль. Для subscribers это
    принципиально: NULL означает «счётчик скрыт», а ноль — «подписчиков нет».
    Пробелы и неразрывные пробелы внутри числа убираем: Excel охотно
    сохраняет «1 234 567» именно так.
    """
    text = (raw or "").strip().replace("\u00a0", "").replace(" ", "")
    if not text:
        return None
    try:
        return int(float(text))
    except ValueError:
        return None


@router.get("/export")
def export_leads(
    database_id: int | None = Query(None, description="без него — все базы"),
    status: str = Query(""),
    min_score: int | None = Query(None, ge=0, le=100),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Response:
    """Выгрузить лидов в CSV.

    Файл собирается целиком в памяти, а не отдаётся потоком, сознательно.
    StreamingResponse выполняет генератор УЖЕ ПОСЛЕ того, как зависимость
    get_db закрыла сессию — чтение из БД внутри такого генератора падает на
    закрытой сессии. Цена — потолок в MAX_EXPORT_ROWS строк.
    """
    query = (
        db.query(Lead)
        .join(LeadDatabase, Lead.database_id == LeadDatabase.id)
        .filter(LeadDatabase.owner_id == user.id)
    )
    label = "все базы"
    if database_id is not None:
        row = _owned_db(database_id, db, user)
        query = query.filter(Lead.database_id == database_id)
        label = f"«{row.name}»"
    if status.strip():
        query = query.filter(Lead.status == status.strip())
    if min_score is not None:
        query = query.filter(Lead.score >= min_score)

    rows = query.order_by(Lead.id).limit(MAX_EXPORT_ROWS).all()

    buffer = io.StringIO()
    writer = csv.writer(buffer, delimiter=CSV_DELIMITER, lineterminator="\r\n")
    writer.writerow(CSV_COLUMNS)
    for lead in rows:
        writer.writerow(
            [
                _csv_safe(value)
                for value in [
                    lead.channel_id or "",
                    lead.title or "",
                    lead.custom_url or "",
                    lead.country or "",
                    "" if lead.subscribers is None else lead.subscribers,
                    lead.video_count or 0,
                    lead.view_count or 0,
                    lead.published_at.isoformat() if lead.published_at else "",
                    lead.matched_keyword or "",
                    lead.status or "",
                    lead.score or 0,
                    lead.telegram_channel or "",
                    lead.tg_username or "",
                    lead.contact_email or "",
                    lead.tg_status or "unknown",
                    # Переводы строк в описании канала встречаются постоянно. csv их
                    # экранирует кавычками, но часть редакторов всё равно ломает таблицу,
                    # поэтому схлопываем их в пробел.
                    " ".join((lead.description or "").split()),
                ]
            ]
        )

    # BOM обязателен: без него Excel читает UTF-8 как cp1251 и все русские
    # названия каналов превращаются в кракозябры.
    body = ("\ufeff" + buffer.getvalue()).encode("utf-8")

    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M")
    filename = f"leads-{stamp}.csv"

    log_event(
        db,
        user.id,
        "leads",
        f"Экспорт лидов: {len(rows)} строк ({label})",
        "info",
    )
    return Response(
        content=body,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/import", response_model=LeadImportResultOut)
def import_leads(
    file: UploadFile = File(..., description="CSV с колонкой channel_id"),
    database_id: int | None = Form(None, description="куда добавить"),
    database_name: str = Form("", description="имя новой базы, если database_id пуст"),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> LeadImportResultOut:
    """Загрузить лидов из CSV в существующую или новую базу.

    Обязательна одна колонка — channel_id. Остальные необязательные: смысл импорта
    часто в том, чтобы залить голый список каналов из чужого инструмента.

    Оценка считается той же функцией, что и при поиске — иначе импортированные
    лиды были бы с нулёвым баллом и вечно внизу списка. Колонка score в
    файле игнорируется намеренно: балл — производная от данных, и чужой балл,
    посчитанный по другим правилам, сделал бы сортировку бессмысленной.
    """
    raw = file.file.read(MAX_IMPORT_BYTES + 1)
    if not raw:
        raise HTTPException(400, "Файл пуст")
    if len(raw) > MAX_IMPORT_BYTES:
        raise HTTPException(413, "CSV больше 5 МБ")

    # utf-8-sig сам съедает BOM от Excel. Если файл в cp1251 (тоже Excel,
    # только «Сохранить как CSV») — пробуем его вторым, иначе человек получит
    # непонятную ошибку на вполне обычном файле.
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        try:
            text = raw.decode("cp1251")
        except UnicodeDecodeError as exc:
            raise HTTPException(
                400, "Не удалось прочитать файл. Сохраните его в кодировке UTF-8."
            ) from exc

    # Разделитель определяем по первой строке, а не через csv.Sniffer:
    # Sniffer регулярно ошибается на описаниях каналов, где полно запятых.
    header_line = text.split("\n", 1)[0]
    delimiter = ";" if header_line.count(";") >= header_line.count(",") else ","

    reader = csv.DictReader(io.StringIO(text), delimiter=delimiter)
    if not reader.fieldnames or "channel_id" not in [
        (name or "").strip().lower() for name in reader.fieldnames
    ]:
        raise HTTPException(400, "В файле нет колонки channel_id")

    if database_id is not None:
        database = _owned_db(database_id, db, user)
    else:
        name = (database_name or "").strip() or (f"Импорт {datetime.now(UTC):%d.%m.%Y %H:%M}")
        database = LeadDatabase(
            owner_id=user.id,
            name=name[:200],
            source="import",
            mode="custom",
            query=f"импорт из {file.filename or 'CSV'}"[:4000],
            units_spent=0,
        )
        db.add(database)
        db.flush()

    # Уже имеющиеся channel_id тянем одним запросом, а не проверяем каждую
    # строку отдельно — иначе на 50 000 строк будет 50 000 запросов.
    seen: set[str] = {
        row[0] for row in db.query(Lead.channel_id).filter(Lead.database_id == database.id).all()
    }

    created = 0
    duplicates = 0
    skipped = 0
    errors: list[str] = []

    for index, row in enumerate(reader, start=2):  # 1-я строка — заголовки
        if created + duplicates + skipped >= MAX_IMPORT_ROWS:
            errors.append(f"Обработаны первые {MAX_IMPORT_ROWS} строк, остальные пропущены")
            break

        # Ключи приводим к нижнему регистру: выгрузки из других сервисов
        # часто приходят с Channel_ID или CHANNEL_ID.
        data = {
            (key or "").strip().lower(): (value or "")
            for key, value in row.items()
            if key is not None
        }

        channel_id = data.get("channel_id", "").strip()
        if not channel_id:
            skipped += 1
            if len(errors) < MAX_REPORTED_ERRORS:
                errors.append(f"Строка {index}: пустой channel_id")
            continue

        if channel_id in seen:
            duplicates += 1
            continue
        seen.add(channel_id)

        published = None
        raw_date = data.get("published_at", "").strip()
        if raw_date:
            try:
                published = datetime.fromisoformat(raw_date.replace("Z", "+00:00"))
            except ValueError:
                # Неразобранная дата не повод терять весь лид — просто остаётся
                # пустой, но человеку об этом говорим.
                if len(errors) < MAX_REPORTED_ERRORS:
                    errors.append(f"Строка {index}: дата «{raw_date}» не распознана")

        subscribers = _csv_int(data.get("subscribers", ""))
        video_count = _csv_int(data.get("video_count", "")) or 0
        view_count = _csv_int(data.get("view_count", "")) or 0
        matched_keyword = data.get("matched_keyword", "").strip()

        row_status = data.get("status", "").strip().lower()
        if row_status not in LEAD_STATUSES:
            row_status = "new"

        tg_username = data.get("tg_username", "").strip().lstrip("@")[:64]
        telegram_channel = data.get("telegram_channel", "").strip().lstrip("@")[:64]
        if not telegram_channel:
            telegram_channel = contacts.extract_telegram(
                data.get("description", ""), data.get("title", "")
            )[:64]
        contact_email = data.get("contact_email", "").strip()[:200]
        if not contact_email:
            contact_email = contacts.extract_email(data.get("description", ""))[:200]
        tg_status = "pending" if (tg_username or telegram_channel) else "missing"

        db.add(
            Lead(
                database_id=database.id,
                channel_id=channel_id[:64],
                title=data.get("title", "").strip()[:300],
                description=data.get("description", "").strip(),
                custom_url=data.get("custom_url", "").strip()[:200],
                country=data.get("country", "").strip().upper()[:8],
                thumbnail="",
                subscribers=subscribers,
                video_count=video_count,
                view_count=view_count,
                published_at=published,
                matched_keyword=matched_keyword[:200],
                tg_username=tg_username,
                telegram_channel=telegram_channel,
                telegram_url=(f"https://t.me/{telegram_channel}" if telegram_channel else "")[:256],
                contact_email=contact_email,
                contacts_checked_at=datetime.now(UTC),
                tg_status=tg_status,
                tg_error=(
                    "Ожидает проверки через Telegram-аккаунт"
                    if tg_status == "pending"
                    else "Telegram-контакты не найдены в импортированных данных"
                ),
                status=row_status,
                score=compute_lead_score(subscribers, video_count, view_count, matched_keyword),
            )
        )
        created += 1

    db.commit()
    db.refresh(database)

    log_event(
        db,
        user.id,
        "leads",
        f"Импорт в базу «{database.name}»: добавлено {created}, "
        f"дублей {duplicates}, пропущено {skipped}",
        "success" if created else "warn",
    )
    return LeadImportResultOut(
        database_id=database.id,
        database_name=database.name,
        created=created,
        duplicates=duplicates,
        skipped=skipped,
        errors=errors,
    )


# Потолок на одну массовую операцию. Совпадает с MAX_SEND_BATCH в рассылках:
# список id уезжает в теле запроса, и без потолка сюда можно положить сотни тысяч.
MAX_BULK_LEADS = 1000


def _owned_lead_ids(db: Session, user: User, lead_ids: list[int]) -> tuple[list[int], int]:
    """Оставить только свои лиды и сосчитать отброшенные.

    Права проверяются одним запросом через владельца базы, а не поштучно.
    Чужой id не вызывает 404: иначе по ответу можно было бы перебором узнать,
    какие id существуют у других пользователей.
    """
    # dict.fromkeys вместо set — убирает повторы, сохраняя порядок.
    unique = list(dict.fromkeys(lead_ids))
    if not unique:
        raise HTTPException(400, "Список лидов пуст")

    capped = unique[:MAX_BULK_LEADS]
    rows = (
        db.query(Lead.id)
        .join(LeadDatabase, Lead.database_id == LeadDatabase.id)
        .filter(Lead.id.in_(capped), LeadDatabase.owner_id == user.id)
        .all()
    )
    owned = [r[0] for r in rows]
    return owned, len(unique) - len(owned)


@router.post("/bulk/status", response_model=LeadBulkResultOut)
def bulk_update_status(
    payload: LeadBulkStatusIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> LeadBulkResultOut:
    """Поменять статус сразу многим лидам.

    Без этого эндпоинта интерфейс был бы вынужден слать по PATCH на каждый
    лид: пятьсот запросов из браузера, любой из которых может упасть и оставить
    выборку в половинчатом состоянии. Здесь это одна транзакция.
    """
    status = payload.status.strip()
    if status not in LEAD_STATUSES:
        raise HTTPException(400, f"Неизвестный статус: {status}")

    owned, skipped = _owned_lead_ids(db, user, payload.lead_ids)
    if owned:
        # synchronize_session=False: объекты в сессии нам больше не нужны,
        # а синхронизация на тысяче строк стоит дороже самого UPDATE.
        db.query(Lead).filter(Lead.id.in_(owned)).update(
            {Lead.status: status}, synchronize_session=False
        )
        if status == "interested":
            _mark_interested_sends(db, user.id, owned)
        db.commit()
        log_event(
            db,
            user.id,
            "leads",
            f"Массовая смена статуса на «{status}»: {len(owned)} лидов",
        )
    return LeadBulkResultOut(affected=len(owned), skipped=skipped)


@router.post("/bulk/delete", response_model=LeadBulkResultOut)
def bulk_delete_leads(
    payload: LeadBulkDeleteIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> LeadBulkResultOut:
    """Удалить выбранных лидов.

    Удаляются только сами лиды; база остаётся, даже если опустела — удаление
    базы есть отдельное осознанное действие, а не побочный эффект чистки.
    """
    owned, skipped = _owned_lead_ids(db, user, payload.lead_ids)
    if owned:
        db.query(Lead).filter(Lead.id.in_(owned)).delete(synchronize_session=False)
        db.commit()
        log_event(db, user.id, "leads", f"Массовое удаление лидов: {len(owned)}")
    return LeadBulkResultOut(affected=len(owned), skipped=skipped)


@router.patch("/{lead_id}", response_model=LeadOut)
def update_lead_status(
    lead_id: int,
    status: str = Query(..., description="new | contacted | replied | interested | unsubscribed"),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> LeadOut:
    lead = (
        db.query(Lead)
        .join(LeadDatabase, Lead.database_id == LeadDatabase.id)
        .filter(Lead.id == lead_id, LeadDatabase.owner_id == user.id)
        .one_or_none()
    )
    if not lead:
        raise HTTPException(404, "Лид не найден")
    if status not in LEAD_STATUSES:
        raise HTTPException(400, f"Неизвестный статус: {status}")
    lead.status = status
    if status == "interested":
        _mark_interested_sends(db, user.id, [lead.id])
    db.commit()
    db.refresh(lead)
    return LeadOut.model_validate(lead)


@router.put("/{lead_id}/note", response_model=LeadOut)
def update_lead_note(
    lead_id: int,
    data: LeadNoteIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> LeadOut:
    """Сохранить заметку по лиду.

    Отдельным эндпоинтом, а не полем в PATCH статуса: статус меняется массово и
    из списка, заметка — поштучно из карточки. Слив их в один вызов означал бы,
    что массовая смена статуса способна затереть чужой текст.
    """
    lead = (
        db.query(Lead)
        .join(LeadDatabase, Lead.database_id == LeadDatabase.id)
        .filter(Lead.id == lead_id, LeadDatabase.owner_id == user.id)
        .one_or_none()
    )
    if not lead:
        raise HTTPException(404, "Лид не найден")
    lead.note = (data.note or "").strip()
    db.commit()
    db.refresh(lead)
    return LeadOut.model_validate(lead)


# ---------- Telegram-профиль лида ----------
#
# Плейсхолдеры {first_name}/{last_name} берутся не из YouTube (там только
# название канала), а из Telegram — через MTProto-клиент. Пока имя не
# подтверждено Telegram, мы его не придумываем и оставляем пустым.

TG_RESOLVE_MAX = 100


def _verification_accounts(db: Session, owner_id: int) -> list[int]:
    release_expired(db, owner_id)
    rows = (
        db.query(WarmupAccount)
        .filter(
            WarmupAccount.owner_id == owner_id,
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
    return [row.id for row in live_accounts(db, rows, fail_closed=True)]


def _telegram_out(lead: Lead, ok: bool) -> LeadTelegramOut:
    return LeadTelegramOut(
        lead_id=lead.id,
        title=lead.title or "",
        ok=ok,
        username=lead.tg_username or "",
        tg_id=lead.tg_id or "",
        kind=lead.tg_kind or "",
        status=lead.tg_status or "unknown",
        account_id=lead.tg_verified_by_account_id,
        first_name=lead.tg_first_name or "",
        last_name=lead.tg_last_name or "",
        error=lead.tg_error or "",
    )


_ACTIVE_VERIFICATION_JOBS: set[int] = set()
_ACTIVE_VERIFICATION_LOCK = threading.Lock()
_VERIFICATION_START_LOCK = threading.Lock()


def _verification_statuses(retry_errors: bool) -> list[str]:
    statuses = ["pending", "unknown"]
    if retry_errors:
        statuses.extend(["error", "rejected"])
    return statuses


def _verification_candidates(db: Session, job: LeadVerificationJob) -> SQLQuery:
    """Ещё не обработанные этим job лиды в приоритетном порядке."""
    return db.query(Lead).filter(
        Lead.database_id == job.database_id,
        Lead.tg_status.in_(_verification_statuses(bool(job.retry_errors))),
        or_(
            Lead.tg_verification_job_id.is_(None),
            Lead.tg_verification_job_id != job.id,
        ),
    )


def _finish_verification_job(
    db: Session,
    job: LeadVerificationJob,
    status: str,
    *,
    error: str = "",
) -> None:
    job.status = status
    job.current_lead_id = None
    job.current_title = ""
    job.completed_at = datetime.now(UTC)
    if error:
        job.last_error = error[:1000]
    database_row = db.get(LeadDatabase, job.database_id)
    if database_row is not None:
        _telegram_summary(db, database_row, job.owner_id)
    db.commit()


def _process_verification_job(db: Session, job_id: int) -> None:
    """Последовательно проверить всю очередь одного job.

    Коммит идёт после каждого лида: закрытие вкладки ничего не прерывает, а
    рестарт сервера продолжает с первой строки без отметки этого прогона.
    """
    job = db.get(LeadVerificationJob, job_id)
    if job is None:
        return
    if job.status == "cancel_requested":
        _finish_verification_job(db, job, "cancelled")
        return

    accounts = _verification_accounts(db, job.owner_id)
    if not accounts:
        _finish_verification_job(
            db,
            job,
            "error",
            error="Нет подключённых Telegram-аккаунтов для проверки",
        )
        return

    job.status = "running"
    job.started_at = job.started_at or datetime.now(UTC)
    job.completed_at = None
    db.commit()

    while True:
        db.expire_all()
        job = db.get(LeadVerificationJob, job_id)
        if job is None:
            return
        if job.status == "cancel_requested":
            _finish_verification_job(db, job, "cancelled")
            log_event(
                db,
                job.owner_id,
                "leads",
                f"Telegram-верификация остановлена: обработано {job.processed} из {job.total}",
                "warn",
                "database",
                job.database_id,
            )
            return

        accounts = _verification_accounts(db, job.owner_id)
        if not accounts:
            _finish_verification_job(
                db,
                job,
                "error",
                error="Все Telegram-аккаунты отключены во время проверки",
            )
            return

        lead = _verification_candidates(db, job).order_by(Lead.score.desc(), Lead.id).first()
        if lead is None:
            _finish_verification_job(db, job, "completed")
            log_event(
                db,
                job.owner_id,
                "leads",
                (
                    "Telegram-верификация завершена: "
                    f"личных пользователей {job.verified}, без получателя {job.unresolved}, "
                    f"ошибок {job.errors}"
                ),
                "success" if job.verified else "warn",
                "database",
                job.database_id,
            )
            return

        job.current_lead_id = lead.id
        job.current_title = (lead.title or "Без названия")[:300]
        db.commit()

        try:
            profile = contacts.verify_lead(
                lead,
                job.owner_id,
                accounts,
                start_index=int(job.processed or 0),
            )
            lead.tg_verification_job_id = job.id
            job.processed = int(job.processed or 0) + 1
            if profile.sendable:
                job.verified = int(job.verified or 0) + 1
                job.last_result = f"@{lead.tg_username} · личный пользователь"[:500]
                job.last_error = ""
            else:
                job.unresolved = int(job.unresolved or 0) + 1
                if lead.tg_status in {"error", "rejected"}:
                    job.errors = int(job.errors or 0) + 1
                job.last_result = (lead.tg_error or "Получатель не найден")[:500]
            db.commit()
        except Exception as exc:
            logger.exception("Ошибка проверки лида #%s в job #%s", lead.id, job.id)
            db.rollback()
            job = db.get(LeadVerificationJob, job_id)
            failed_lead = db.get(Lead, lead.id)
            if job is None or failed_lead is None:
                raise
            failed_lead.tg_status = "error"
            failed_lead.tg_error = str(exc)[:500]
            failed_lead.tg_checked_at = datetime.now(UTC)
            failed_lead.tg_verification_job_id = job.id
            job.processed = int(job.processed or 0) + 1
            job.unresolved = int(job.unresolved or 0) + 1
            job.errors = int(job.errors or 0) + 1
            job.last_error = str(exc)[:1000]
            job.last_result = f"Ошибка у «{failed_lead.title or 'Без названия'}»"[:500]
            db.commit()


def _verification_background(job_id: int) -> None:
    db = database.SessionLocal()
    try:
        _process_verification_job(db, job_id)
    except Exception as exc:
        logger.exception("Непрерывная Telegram-верификация #%s упала", job_id)
        db.rollback()
        try:
            job = db.get(LeadVerificationJob, job_id)
            if job is not None:
                _finish_verification_job(db, job, "error", error=str(exc))
        except Exception:
            logger.exception("Не удалось записать ошибку verification job #%s", job_id)
    finally:
        db.close()
        with _ACTIVE_VERIFICATION_LOCK:
            _ACTIVE_VERIFICATION_JOBS.discard(job_id)


def spawn_verification_job(job_id: int) -> bool:
    """Запустить job не больше одного раза в текущем процессе."""
    with _ACTIVE_VERIFICATION_LOCK:
        if job_id in _ACTIVE_VERIFICATION_JOBS:
            return False
        _ACTIVE_VERIFICATION_JOBS.add(job_id)
    try:
        thread = threading.Thread(
            target=_verification_background,
            args=(job_id,),
            name=f"lead-verification-{job_id}",
            daemon=True,
        )
        thread.start()
        return True
    except Exception:
        with _ACTIVE_VERIFICATION_LOCK:
            _ACTIVE_VERIFICATION_JOBS.discard(job_id)
        raise


def recover_verification_jobs(db: Session) -> list[int]:
    """Подготовить незавершённые jobs к автопродолжению после рестарта."""
    jobs = (
        db.query(LeadVerificationJob)
        .filter(LeadVerificationJob.status.in_(["queued", "running", "cancel_requested"]))
        .order_by(LeadVerificationJob.id)
        .all()
    )
    resume: list[int] = []
    for job in jobs:
        if job.status == "cancel_requested":
            job.status = "cancelled"
            job.completed_at = datetime.now(UTC)
            continue
        job.status = "queued"
        job.last_result = "Продолжаем после перезапуска сервера"
        resume.append(job.id)
    if jobs:
        db.commit()
    return resume


@router.get(
    "/databases/{database_id}/verification",
    response_model=LeadVerificationJobOut,
)
def get_verification_job(
    database_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> LeadVerificationJobOut:
    _owned_db(database_id, db, user)
    job = (
        db.query(LeadVerificationJob)
        .filter(
            LeadVerificationJob.database_id == database_id,
            LeadVerificationJob.owner_id == user.id,
        )
        .order_by(LeadVerificationJob.id.desc())
        .first()
    )
    if job is None:
        raise HTTPException(404, "Верификация этой базы ещё не запускалась")
    return _verification_job_out(job)


@router.post(
    "/databases/{database_id}/verification/start",
    response_model=LeadVerificationJobOut,
)
def start_verification_job(
    database_id: int,
    data: LeadVerificationStartIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> LeadVerificationJobOut:
    """Одной кнопкой проверить всю оставшуюся базу в фоне."""
    database_row = _owned_db(database_id, db, user)
    accounts = _verification_accounts(db, user.id)
    if not accounts:
        raise HTTPException(409, "Нет подключённых Telegram-аккаунтов для проверки")

    with _VERIFICATION_START_LOCK:
        active = (
            db.query(LeadVerificationJob)
            .filter(
                LeadVerificationJob.database_id == database_id,
                LeadVerificationJob.owner_id == user.id,
                LeadVerificationJob.status.in_(["queued", "running", "cancel_requested"]),
            )
            .order_by(LeadVerificationJob.id.desc())
            .first()
        )
        if active is not None:
            if active.status != "cancel_requested" and not data.wait:
                spawn_verification_job(active.id)
            return _verification_job_out(active)

        statuses = _verification_statuses(data.retry_errors)
        total = (
            db.query(func.count(Lead.id))
            .filter(Lead.database_id == database_id, Lead.tg_status.in_(statuses))
            .scalar()
            or 0
        )
        job = LeadVerificationJob(
            owner_id=user.id,
            database_id=database_id,
            status="queued" if total else "completed",
            retry_errors=bool(data.retry_errors),
            total=int(total),
            completed_at=None if total else datetime.now(UTC),
            last_result="Очередь подготовлена" if total else "Новых кандидатов нет",
        )
        db.add(job)
        if total:
            database_row.pipeline_status = "verifying"
        db.commit()
        db.refresh(job)

    if not total:
        return _verification_job_out(job)
    if data.wait:
        _process_verification_job(db, job.id)
        db.refresh(job)
    else:
        spawn_verification_job(job.id)
    return _verification_job_out(job)


@router.post(
    "/databases/{database_id}/verification/cancel",
    response_model=LeadVerificationJobOut,
)
def cancel_verification_job(
    database_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> LeadVerificationJobOut:
    _owned_db(database_id, db, user)
    job = (
        db.query(LeadVerificationJob)
        .filter(
            LeadVerificationJob.database_id == database_id,
            LeadVerificationJob.owner_id == user.id,
            LeadVerificationJob.status.in_(["queued", "running", "cancel_requested"]),
        )
        .order_by(LeadVerificationJob.id.desc())
        .first()
    )
    if job is None:
        raise HTTPException(409, "Активной верификации нет")
    job.status = "cancel_requested"
    job.last_result = "Остановка после текущего лида"
    db.commit()
    db.refresh(job)
    return _verification_job_out(job)


@router.post("/{lead_id}/telegram", response_model=LeadTelegramOut)
def resolve_lead_telegram(
    lead_id: int,
    data: LeadTelegramIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> LeadTelegramOut:
    """Подтянуть имя и фамилию одного лида из Telegram."""
    lead = (
        db.query(Lead)
        .join(LeadDatabase, Lead.database_id == LeadDatabase.id)
        .filter(Lead.id == lead_id, LeadDatabase.owner_id == user.id)
        .one_or_none()
    )
    if not lead:
        raise HTTPException(404, "Лид не найден")

    accounts = _verification_accounts(db, user.id)
    if not accounts:
        raise HTTPException(409, "Нет подключённых Telegram-аккаунтов для проверки")
    profile = contacts.verify_lead(
        lead,
        user.id,
        accounts,
        extra_handles=[data.username] if data.username.strip() else None,
    )
    db.commit()
    result = _telegram_out(lead, profile.sendable)
    if profile.sendable:
        log_event(
            db,
            user.id,
            "leads",
            f"Telegram подтверждён для «{lead.title}»: @{result.username}",
            "success",
            "lead",
            lead.id,
        )
    return result


@router.post("/resolve-telegram", response_model=LeadTelegramResolveOut)
def resolve_leads_telegram(
    data: LeadTelegramResolveIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> LeadTelegramResolveOut:
    """Старый пакетный API, оставленный для совместимости старых клиентов."""
    _owned_db(data.database_id, db, user)
    active = (
        db.query(LeadVerificationJob.id)
        .filter(
            LeadVerificationJob.database_id == data.database_id,
            LeadVerificationJob.owner_id == user.id,
            LeadVerificationJob.status.in_(["queued", "running", "cancel_requested"]),
        )
        .first()
    )
    if active:
        raise HTTPException(
            409,
            "Вся база уже проверяется. Следите за прогрессом в едином задании.",
        )
    accounts = _verification_accounts(db, user.id)
    if not accounts:
        raise HTTPException(409, "Нет подключённых Telegram-аккаунтов для проверки")
    limit = max(1, min(int(data.limit or 30), TG_RESOLVE_MAX))

    query = db.query(Lead).filter(Lead.database_id == data.database_id)
    if data.only_missing:
        # Финальные channel/bot/missing/error не берём повторно: иначе первые
        # 30 неудачных строк навсегда закрывают доступ к следующей пачке.
        query = query.filter(Lead.tg_status.in_(["pending", "unknown"]))
    leads = query.order_by(Lead.score.desc(), Lead.id).limit(limit).all()

    results: list[LeadTelegramOut] = []
    for index, lead in enumerate(leads):
        profile = contacts.verify_lead(
            lead,
            user.id,
            accounts,
            start_index=index,
        )
        results.append(_telegram_out(lead, profile.sendable))

    resolved = sum(1 for r in results if r.ok)
    database = _owned_db(data.database_id, db, user)
    _telegram_summary(db, database, user.id)
    db.commit()
    log_event(
        db,
        user.id,
        "leads",
        f"Верификация Telegram: подтверждено {resolved} из {len(results)}",
        "success" if resolved else "warn",
        "database",
        data.database_id,
    )
    return LeadTelegramResolveOut(
        resolved=resolved,
        failed=len(results) - resolved,
        checked=len(results),
        results=results,
    )
