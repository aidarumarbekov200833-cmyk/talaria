"""Ниши, генерация тем и ключевых слов, поиск каналов на YouTube."""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from .. import alerts, contacts
from .. import quota as q
from .. import youtube as yt
from ..database import get_db
from ..dependencies import get_current_user
from ..models import ApiKey, Lead, LeadDatabase, TopicRun, User
from ..niches import (
    KEYWORDS_NOTE,
    NICHES,
    build_keywords,
    seeds_for,
)
from ..plans import check_leads_budget, check_object_limit
from ..ratelimit import limiter
from ..schemas import (
    KeywordsIn,
    KeywordsOut,
    LeadDatabaseOut,
    NicheListOut,
    NicheOut,
    SearchEstimateOut,
    SearchIn,
    TopicItem,
    TopicRunIn,
    TopicRunOut,
)
from ..services import compute_lead_score, log_event

router = APIRouter(prefix="/api", tags=["search"])

NO_KEY_MESSAGE = (
    "Не добавлен ключ YouTube API. Откройте «Настройки» → «Ключи YouTube API» "
    "и добавьте ключ — без него поиск невозможен."
)

# Служебные слова, которые не годятся в поисковый запрос.
STOPWORDS = {
    "и",
    "в",
    "во",
    "на",
    "с",
    "со",
    "по",
    "за",
    "из",
    "от",
    "до",
    "для",
    "как",
    "что",
    "это",
    "или",
    "но",
    "а",
    "не",
    "же",
    "бы",
    "ли",
    "у",
    "о",
    "об",
    "the",
    "a",
    "an",
    "of",
    "and",
    "to",
    "in",
    "on",
    "for",
    "with",
    "is",
    "it",
    "my",
    "your",
    "you",
    "we",
    "i",
    "vs",
    "new",
    "official",
    "video",
    "shorts",
}


@router.get("/niches", response_model=NicheListOut)
def list_niches() -> NicheListOut:
    """Готовые ниши — наш фиксированный список, не данные YouTube."""
    return NicheListOut(
        niches=[NicheOut(slug=n["slug"], title=n["title"], icon=n["icon"]) for n in NICHES]
    )


def _require_key(db: Session, user: User, need_units: int) -> ApiKey:
    """Ключ с достаточным остатком, иначе понятная ошибка."""
    has_any = db.query(ApiKey).filter(ApiKey.owner_id == user.id).count()
    if not has_any:
        raise HTTPException(400, NO_KEY_MESSAGE)

    key = q.pick_key(db, user.id, need_units)
    if key is None:
        summary = q.summary(db, user.id)
        raise HTTPException(
            429,
            f"Не хватает квоты: нужно {need_units} единиц, по нашему счётчику "
            f"осталось {summary['left_today']}. Сброс — в полночь по "
            f"тихоокеанскому времени.",
        )
    return key


def _phrase_from_title(title: str) -> str:
    """Короткая тема из заголовка видео.

    Берём осмысленные слова заголовка, а не весь заголовок: полный заголовок с
    эмодзи и хештегами в поиске каналов бесполезен. Тема остаётся привязанной к
    реальному видео — его название уходит в evidence.
    """
    cleaned = re.sub(r"[^\w\s\-]", " ", title, flags=re.UNICODE)
    words = [w for w in cleaned.split() if len(w) > 2 and w.lower() not in STOPWORDS]
    return " ".join(words[:4]).strip()


@router.post(
    "/topics",
    response_model=TopicRunOut,
    dependencies=[Depends(limiter("search"))],
)
def generate_topics(
    payload: TopicRunIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> TopicRunOut:
    """Сгенерировать темы: из настоящих трендов YouTube или из выбранных ниш.

    Режим trends делает один вызов videos.list (1 единица квоты) и берёт темы из
    заголовков и тегов реально популярных видео региона. У каждой темы указано
    происхождение — название видео и канал, чтобы было видно, откуда она.
    """
    count = max(1, min(50, payload.count))
    mode = payload.mode if payload.mode in {"trends", "niches"} else "trends"
    topics: list[TopicItem] = []
    units = 0

    if mode == "niches":
        if not payload.niches:
            raise HTTPException(400, "Не выбрана ни одна ниша")
        seeds = seeds_for(payload.niches)
        for phrase in build_keywords(seeds, count):
            topics.append(TopicItem(topic=phrase, source="niche", evidence="выбранные ниши"))
    else:
        # Мультирегион: по одному вызову videos.list на регион. Единицы квоты
        # тратятся за каждый — считаем их, а не одну.
        regions = payload.region_codes
        videos: list[dict] = []
        failures: list[str] = []
        for code in regions:
            key = _require_key(db, user, q.COST_VIDEOS_LIST)
            units += q.spend(db, key, "videos.list")
            db.commit()
            try:
                videos.extend(yt.most_popular(key.key, code, 50))
            except yt.YouTubeError as exc:
                if exc.is_quota:
                    key.status, key.status_detail = "quota_exceeded", exc.message
                elif exc.is_key_problem:
                    key.status, key.status_detail = "invalid", f"{exc.reason}: {exc.message}"
                key.last_checked_at = datetime.now(UTC)
                db.commit()
                failures.append(f"{code}: {exc.message}")
        if not videos:
            reason = "; ".join(failures) or "YouTube не вернул популярных видео"
            log_event(db, user.id, "search", f"Тренды не получены: {reason}", "error")
            raise HTTPException(502, f"YouTube: {reason}")
        if failures:
            log_event(
                db,
                user.id,
                "search",
                "Часть регионов не ответила — " + "; ".join(failures),
                "warn",
            )

        seen: set[str] = set()
        for video in videos:
            # Сначала теги: это слова автора о теме видео, они точнее заголовка.
            for tag in video["tags"][:3]:
                phrase = tag.strip()
                if len(phrase) < 3 or phrase.lower() in seen:
                    continue
                seen.add(phrase.lower())
                topics.append(
                    TopicItem(
                        topic=phrase,
                        source="youtube_trends",
                        evidence=video["title"],
                        channel=video["channel"],
                        views=video["views"],
                    )
                )
                if len(topics) >= count:
                    break
            if len(topics) >= count:
                break
            phrase = _phrase_from_title(video["title"])
            if phrase and phrase.lower() not in seen:
                seen.add(phrase.lower())
                topics.append(
                    TopicItem(
                        topic=phrase,
                        source="youtube_trends",
                        evidence=video["title"],
                        channel=video["channel"],
                        views=video["views"],
                    )
                )
            if len(topics) >= count:
                break

    run = TopicRun(
        owner_id=user.id,
        mode=mode,
        region=",".join(payload.region_codes)[:64] if mode == "trends" else (payload.region or "KZ"),
        niches=json.dumps(payload.niches, ensure_ascii=False),
        topics=json.dumps([t.model_dump() for t in topics], ensure_ascii=False),
        requested=count,
        units_spent=units,
    )
    db.add(run)
    db.commit()
    db.refresh(run)

    got = len(topics)
    note = f"Собрано тем: {got}"
    if got < count:
        note += f" из запрошенных {count} — больше подходящих не нашлось"
    log_event(db, user.id, "search", note, "success" if got else "warn", "topic_run", run.id)

    return TopicRunOut(
        id=run.id,
        mode=mode,
        region=run.region,
        niches=payload.niches,
        topics=topics,
        requested=count,
        units_spent=units,
        created_at=run.created_at,
    )


@router.post("/keywords", response_model=KeywordsOut)
def generate_keywords(payload: KeywordsIn) -> KeywordsOut:
    """Ключевые слова из тем и/или ниш. Квота не тратится — это перебор строк."""
    count = max(1, min(200, payload.count))
    seeds = [t.strip() for t in payload.topics if t and t.strip()]
    seeds += seeds_for(payload.niches)
    if not seeds:
        raise HTTPException(400, "Нет ни тем, ни ниш для генерации")
    return KeywordsOut(keywords=build_keywords(seeds, count), source_note=KEYWORDS_NOTE)


# Сколько каналов максимум за один запуск поиска (сумма по всем словам).
MAX_TOTAL_CHANNELS = 5000


def _estimate(db: Session, owner_id: int, keywords: int, limit: int) -> SearchEstimateOut:
    """Стоимость запуска.

    `limit` — сколько каналов искать **на одно ключевое слово**. Раньше это
    был общий потолок на весь запуск, и 50 слов делили одни и те же 50 каналов
    на всех — по одному на слово. Теперь 50 слов × 50 = 2500 каналов.
    """
    total = keywords * limit
    pages, detail_calls = yt.calls_for_channels(keywords, total)
    units = pages * q.COST_SEARCH_LIST + detail_calls * q.COST_CHANNELS_LIST
    left = q.summary(db, owner_id)["left_today"]
    return SearchEstimateOut(
        keywords=keywords,
        limit=limit,
        pages=pages,
        units_estimate=units,
        left_today=left,
        enough=left >= units,
        total_channels=total,
        note=(
            f"{keywords} ключевых слов × {limit} каналов = до {total} каналов. "
            f"{pages} × search.list по 100 единиц + {detail_calls} × channels.list "
            f"по 1 = {units} единиц. Остаток по нашему счётчику: {left}."
        ),
    )


@router.post("/search/estimate", response_model=SearchEstimateOut)
def estimate_search(
    payload: SearchIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> SearchEstimateOut:
    """Стоимость поиска до запуска — чтобы квота не исчезала неожиданно."""
    keywords = [k.strip() for k in payload.keywords if k and k.strip()]
    if not keywords:
        raise HTTPException(400, "Не заданы ключевые слова")
    if not db.query(ApiKey).filter(ApiKey.owner_id == user.id).count():
        raise HTTPException(400, NO_KEY_MESSAGE)
    return _estimate(db, user.id, len(keywords), max(1, min(1000, payload.limit)))


@router.post(
    "/search",
    response_model=LeadDatabaseOut,
    dependencies=[Depends(limiter("search"))],
)
def run_search(
    payload: SearchIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> LeadDatabaseOut:
    """Найти каналы и сохранить их в новую базу лидов.

    Идём по ключевым словам по очереди и останавливаемся, когда набрали limit
    каналов либо кончилась квота. Частичный результат сохраняем и пишем в
    журнал, сколько реально нашли — молча урезать запрос было бы обманом.
    """
    keywords = [k.strip() for k in payload.keywords if k and k.strip()]
    if not keywords:
        raise HTTPException(400, "Не заданы ключевые слова")
    # Выбранное значение — на каждое ключевое слово. Общий потолок —
    # произведение, но с верхней границей: без неё 5000 каналов в одном
    # синхронном запросе гарантированно уйдут в таймаут.
    per_keyword = max(1, min(1000, payload.limit))
    limit = min(per_keyword * len(keywords), MAX_TOTAL_CHANNELS)

    # Лимиты тарифа: сколько каналов ещё можно найти в этом месяце и не
    # превышено ли число баз лидов. Проверяем до траты квоты.
    allowed = check_leads_budget(db, user.id, limit)
    limit = min(limit, allowed)
    check_object_limit(db, user.id, "databases")

    plan = _estimate(db, user.id, len(keywords), per_keyword)
    key = _require_key(db, user, q.COST_SEARCH_LIST)  # хватило бы хоть на страницу
    found: dict[str, str] = {}  # channel_id -> ключевое слово
    stop_reason = ""

    for keyword in keywords:
        if len(found) >= limit:
            break
        page_token = ""
        collected = 0
        while collected < per_keyword and len(found) < limit:
            key = q.pick_key(db, user.id, q.COST_SEARCH_LIST)
            if key is None:
                stop_reason = "закончилась квота по нашему счётчику"
                break
            q.spend(db, key, "search.list")
            db.commit()
            try:
                ids, page_token = yt.search_channels(
                    key.key, keyword, page_token, 50, payload.region
                )
            except yt.YouTubeError as exc:
                if exc.is_quota:
                    key.status, key.status_detail = "quota_exceeded", exc.message
                elif exc.is_key_problem:
                    key.status, key.status_detail = "invalid", f"{exc.reason}: {exc.message}"
                key.last_checked_at = datetime.now(UTC)
                db.commit()
                stop_reason = f"YouTube: {exc.message}"
                break

            for cid in ids:
                if cid not in found:
                    found[cid] = keyword
                    collected += 1
                    if len(found) >= limit:
                        break
            if not page_token:
                break
        if stop_reason:
            break

    if not found:
        message = stop_reason or "YouTube не вернул ни одного канала по этим словам"
        log_event(db, user.id, "search", f"Поиск без результата: {message}", "warn")
        raise HTTPException(502 if stop_reason else 404, message)

    return _save_results(db, user, payload, keywords, found, plan, stop_reason)


def _extract_contacts(db: Session, database_id: int) -> int:
    """Этап 1½: извлечь публичные ссылки, не обращаясь к Telegram.

    Проверка через пользовательские аккаунты запускается отдельно со страницы
    поиска. Так база YouTube появляется сразу, а человек видит и контролирует
    второй, более медленный этап pipeline.
    """
    leads = (
        db.query(Lead)
        .filter(Lead.database_id == database_id)
        .order_by(Lead.score.desc(), Lead.id.desc())
        .all()
    )
    now = datetime.now(UTC)
    with_tg = 0

    for lead in leads:
        candidates = contacts.extract_telegram_candidates(lead.description or "", lead.title or "")
        email = contacts.extract_email(lead.description or "")
        lead.contacts_checked_at = now
        if email:
            lead.contact_email = email[:200]
        if not candidates:
            lead.tg_status = "missing"
            lead.tg_error = "Telegram-контакты не найдены в публичных данных"
            continue

        with_tg += 1
        lead.telegram_channel = candidates[0].handle[:64]
        lead.telegram_url = f"https://t.me/{candidates[0].handle}"[:256]
        lead.tg_status = "pending"
        lead.tg_error = "Ожидает проверки через Telegram-аккаунт"

    db.commit()
    return with_tg


def _filter_config(payload: SearchIn) -> dict[str, object]:
    return {
        "min_subscribers": payload.min_subscribers,
        "max_subscribers": payload.max_subscribers,
        "min_views": payload.min_views,
        "min_average_views": payload.min_average_views,
        "min_view_subscriber_ratio": payload.min_view_subscriber_ratio,
        "min_videos": payload.min_videos,
        "max_channel_age_days": payload.max_channel_age_days,
        "country": payload.country.upper(),
        "countries": sorted(payload.country_codes),
        "include_hidden_subscribers": payload.include_hidden_subscribers,
        "require_telegram_link": payload.require_telegram_link,
    }


def _matches_filters(item: dict, payload: SearchIn, now: datetime) -> bool:
    subscribers = item.get("subscribers")
    if subscribers is None:
        if not payload.include_hidden_subscribers or payload.min_subscribers > 0:
            return False
    else:
        if subscribers < payload.min_subscribers:
            return False
        if payload.max_subscribers is not None and subscribers > payload.max_subscribers:
            return False
    view_count = int(item.get("view_count") or 0)
    video_count = int(item.get("video_count") or 0)
    if view_count < payload.min_views:
        return False
    if video_count < payload.min_videos:
        return False
    average_views = view_count / video_count if video_count else 0
    if average_views < payload.min_average_views:
        return False
    if payload.min_view_subscriber_ratio:
        if not subscribers:
            return False
        ratio_percent = average_views * 100 / subscribers
        if ratio_percent < payload.min_view_subscriber_ratio:
            return False
    countries = payload.country_codes
    if countries and str(item.get("country") or "").upper() not in countries:
        return False
    if payload.max_channel_age_days:
        raw = str(item.get("published_at") or "")
        try:
            published = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            return False
        if published.tzinfo is None:
            published = published.replace(tzinfo=UTC)
        if (now - published).days > payload.max_channel_age_days:
            return False
    if payload.require_telegram_link and not contacts.extract_telegram_candidates(
        str(item.get("description") or ""), str(item.get("title") or "")
    ):
        return False
    return True


def _save_results(
    db: Session,
    user: User,
    payload: SearchIn,
    keywords: list[str],
    found: dict[str, str],
    plan: SearchEstimateOut,
    stop_reason: str,
) -> LeadDatabaseOut:
    """Дотянуть подробности каналов и сохранить базу лидов."""
    details: list[dict] = []
    ids = list(found.keys())
    detail_error = ""

    for start in range(0, len(ids), 50):
        chunk = ids[start : start + 50]
        key = q.pick_key(db, user.id, q.COST_CHANNELS_LIST)
        if key is None:
            detail_error = "квота кончилась до загрузки подробностей"
            break
        q.spend(db, key, "channels.list")
        db.commit()
        try:
            details.extend(yt.channels_details(key.key, chunk))
        except yt.YouTubeError as exc:
            detail_error = exc.message
            break

    if not details:
        message = detail_error or "YouTube не отдал данные каналов"
        log_event(db, user.id, "search", f"Поиск: {message}", "error")
        raise HTTPException(502, message)

    unfiltered_count = len(details)
    now = datetime.now(UTC)
    details = [item for item in details if _matches_filters(item, payload, now)]
    if not details:
        raise HTTPException(
            404,
            f"YouTube вернул {unfiltered_count} каналов, но ни один не прошёл фильтры",
        )

    name = (payload.database_name or "").strip() or (
        f"{keywords[0]} — {datetime.now(UTC):%d.%m.%Y %H:%M}"
    )
    database = LeadDatabase(
        owner_id=user.id,
        name=name[:200],
        source="youtube",
        mode=payload.mode if payload.mode in {"trends", "niches", "custom"} else "custom",
        query=", ".join(keywords)[:4000],
        filters_json=json.dumps(_filter_config(payload), ensure_ascii=False),
        pipeline_status="youtube_ready",
        units_spent=0,
    )
    db.add(database)
    db.flush()

    for item in details:
        published = None
        raw = item.get("published_at") or ""
        if raw:
            try:
                published = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            except ValueError:
                published = None
        db.add(
            Lead(
                database_id=database.id,
                channel_id=item["channel_id"],
                title=item["title"][:300],
                description=item["description"],
                custom_url=item["custom_url"][:200],
                country=item["country"][:8],
                thumbnail=item["thumbnail"][:512],
                subscribers=item["subscribers"],
                video_count=item["video_count"],
                view_count=item["view_count"],
                published_at=published,
                matched_keyword=found.get(item["channel_id"], "")[:200],
                # Считаем сразу при сохранении: все четыре поля уже в руках, а
                # отдельный проход по базе потом стоил бы лишних запросов.
                score=compute_lead_score(
                    item["subscribers"],
                    item["video_count"],
                    item["view_count"],
                    found.get(item["channel_id"], ""),
                ),
            )
        )

    database.units_spent = plan.units_estimate
    db.commit()
    db.refresh(database)

    # Сразу после сохранения достраиваем контакты: база без Telegram — мёртвый
    # груз, рассылку по ней запустить не получится. Сбой достройки не должен
    # терять уже оплаченные квотой результаты поиска.
    with_tg = 0
    try:
        with_tg = _extract_contacts(db, database.id)
    except Exception as exc:  # noqa: BLE001 — контакты вторичны по отношению к поиску
        db.rollback()
        alerts.report_error(
            "Поиск лидов",
            "contacts",
            "Не удалось достроить кросс-контакты к найденным каналам",
            exc=exc,
            owner_id=user.id,
            entity=f"база лидов «{database.name}»",
        )

    saved = len(details)
    parts = [f"Найдено каналов: {saved}", f"база «{database.name}»"]
    parts.append(f"Telegram-ссылка найдена у {with_tg}; база ждёт верификации")
    filtered_out = unfiltered_count - saved
    if filtered_out:
        parts.append(f"фильтрами исключено {filtered_out}")
    # plan.limit — уже нормализованное значение; payload.limit мог быть больше
    # допустимого, и писать его в журнал значило бы врать о запросе.
    if saved < plan.limit:
        parts.append(f"запрошено было {plan.limit}")
    if stop_reason:
        parts.append(f"остановка: {stop_reason}")
    if detail_error:
        parts.append(f"подробности неполные: {detail_error}")
    log_event(
        db,
        user.id,
        "search",
        ". ".join(parts),
        "success" if saved else "warn",
        "database",
        database.id,
    )

    return LeadDatabaseOut(
        id=database.id,
        name=database.name,
        source=database.source,
        mode=database.mode,
        query=database.query,
        units_spent=database.units_spent,
        filters_json=database.filters_json,
        pipeline_status=database.pipeline_status,
        created_at=database.created_at,
        leads_count=saved,
    )
