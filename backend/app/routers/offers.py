"""CRUD for offers (message creatives) plus AI-assisted offer writing."""

import logging

import httpx
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from .. import ai_offer as ai_offer_mod
from .. import quota as q
from .. import youtube as yt
from ..database import get_db
from ..dependencies import get_current_user
from ..models import ApiKey, Lead, LeadDatabase, Offer, User
from ..placeholders import PLACEHOLDERS, render, unresolved_tokens
from ..plans import (
    ai_allowed,
    ai_offer_allowed,
    ai_offer_plan_names,
    check_object_limit,
)
from ..schemas import (
    AiOfferIn,
    AiOfferOut,
    AiOfferSettingsIn,
    AiOfferSettingsOut,
    OfferIn,
    OfferListOut,
    OfferOut,
    OkOut,
    PlaceholderListOut,
    PlaceholderOut,
    PreviewIn,
    PreviewOut,
)
from ..services import ai_provider_ready, log_event, resolve_ai_provider
from ..services.offers import (
    MATERIALS_CHOICES as _MATERIALS_CHOICES,
)
from ..services.offers import (
    clamp_materials as _clamp_materials,
)
from ..services.offers import (
    create_offer as create_offer_service,
)
from ..services.offers import (
    delete_offer as delete_offer_service,
)
from ..services.offers import (
    list_offers as list_offers_service,
)
from ..services.offers import (
    owned_offer as owned_offer_service,
)
from ..services.offers import (
    preview_offer as preview_offer_service,
)
from ..services.offers import (
    read_ai_offer_settings as read_ai_offer_settings_service,
)
from ..services.offers import (
    save_ai_offer_settings as save_ai_offer_settings_service,
)
from ..services.offers import (
    update_offer as update_offer_service,
)
from ..services.offers import (
    validated_image as _validated_image,
)
from ..telegram_gateway import gateway

logger = logging.getLogger("outly.offers")

router = APIRouter(prefix="/api/offers", tags=["offers"])

MATERIALS_CHOICES = _MATERIALS_CHOICES


def _owned_offer(offer_id: int, db: Session, user: User) -> Offer:
    return owned_offer_service(db, user.id, offer_id)


@router.get("", response_model=OfferListOut)
def list_offers(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return list_offers_service(db, user.id)


@router.post("", response_model=OfferOut)
def create_offer(
    data: OfferIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    return create_offer_service(
        db,
        user.id,
        data,
        check_object_limit_fn=check_object_limit,
        validate_image_fn=_validated_image,
        log_event_fn=log_event,
    )


@router.patch("/{offer_id}", response_model=OfferOut)
def update_offer(
    offer_id: int,
    data: OfferIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    return update_offer_service(
        db,
        user.id,
        offer_id,
        data,
        validate_image_fn=_validated_image,
    )


@router.delete("/{offer_id}", response_model=OkOut)
def delete_offer(
    offer_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    return delete_offer_service(db, user.id, offer_id)


@router.get("/placeholders", response_model=PlaceholderListOut)
def list_placeholders() -> PlaceholderListOut:
    """Плейсхолдеры для вставки в текст оффера."""
    return PlaceholderListOut(
        placeholders=[
            PlaceholderOut(
                token=p["token"],
                title=p["title"],
                example=p["example"],
                available=p["available"],
                note=p["note"],
                source=p["source"],
            )
            for p in PLACEHOLDERS
        ]
    )


@router.post("/preview", response_model=PreviewOut)
def preview_offer(
    data: PreviewIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Предпросмотр текста на настоящем лиде.

    Без lead_id берём первого лида пользователя. Если лидов нет — возвращаем
    текст с плейсхолдерами как есть: подставлять пример вместо реальных данных
    значило бы показать несуществующего человека.
    """
    return preview_offer_service(
        db,
        user.id,
        data,
        render_fn=render,
        unresolved_tokens_fn=unresolved_tokens,
    )


# ---------- ИИ-оффер ----------
#
# Один скелет на пользователя (users.ai_offer_template) вместо набора полей.
# Раньше здесь выбирались лид, источник, тон и «что мы предлагаем» — четыре
# настройки, три из которых уже сказаны словами в самом скелете, а последняя
# (лид) вообще не имеет смысла: в рассылке лидов сотни, и текст нужен каждому
# свой. Теперь человек описывает, как примерно должен выглядеть оффер, а ИИ
# сканирует контент каждого канала и переписывает скелет под него.
#
# Генерация здесь и генерация при рассылке идут через один модуль ai_offer:
# иначе «проверка на лиде» показывала бы не то, что реально уйдёт людям.


def _ai_key(db: Session, user: User, need_units: int) -> ApiKey:
    """Ключ YouTube с запасом квоты — или понятный отказ."""
    if not db.query(ApiKey).filter(ApiKey.owner_id == user.id).count():
        raise HTTPException(400, "Сначала добавьте ключ YouTube API в настройках.")
    key = q.pick_key(db, user.id, need_units)
    if key is None:
        raise HTTPException(
            429,
            f"Не хватает квоты YouTube: нужно {need_units} единиц, осталось "
            f"{q.summary(db, user.id)['left_today']}.",
        )
    return key


@router.get("/ai-settings", response_model=AiOfferSettingsOut)
def get_ai_offer_settings(
    db: Session = Depends(get_db), user: User = Depends(get_current_user)
) -> AiOfferSettingsOut:
    """Скелет ИИ-оффера и доступность режима на текущем тарифе."""
    return read_ai_offer_settings_service(
        db,
        user,
        clamp_materials_fn=_clamp_materials,
        ai_offer_allowed_fn=ai_offer_allowed,
        ai_offer_plan_names_fn=ai_offer_plan_names,
        ai_provider_ready_fn=ai_provider_ready,
    )


@router.put("/ai-settings", response_model=AiOfferSettingsOut)
def save_ai_offer_settings(
    data: AiOfferSettingsIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> AiOfferSettingsOut:
    """Сохранить скелет. Проверку тарифа делаем на запуске рассылки, не здесь:
    настроить оффер заранее полезно и на дешёвом тарифе — это часть решения
    купить дорогой, а не то, за что надо наказывать ошибкой 403."""
    return save_ai_offer_settings_service(
        db,
        user,
        data,
        clamp_materials_fn=_clamp_materials,
        log_event_fn=log_event,
        ai_offer_allowed_fn=ai_offer_allowed,
        ai_offer_plan_names_fn=ai_offer_plan_names,
        ai_provider_ready_fn=ai_provider_ready,
    )


@router.post("/ai-generate", response_model=AiOfferOut)
def ai_generate_offer(
    data: AiOfferIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Проверка: написать оффер по скелету на самом свежем лиде.

    Это ровно тот же путь, которым пойдёт рассылка — тот же промпт, те же
    факты о канале, тот же модуль. Разница лишь в том, что лид один и текст
    никуда не отправляется.
    """
    if not ai_allowed(db, user.id):
        raise HTTPException(402, "ИИ доступен на платных тарифах.")

    base_url, api_key, model, _prompt, _tools = resolve_ai_provider(db)
    if not (base_url and api_key and model):
        raise HTTPException(400, "ИИ не настроен: укажите провайдера и ключ в админке.")

    template = (data.template or "").strip() or (user.ai_offer_template or "").strip()
    if not template:
        raise HTTPException(
            400,
            "Сначала напишите скелет оффера — по нему ИИ пишет текст каждому лиду.",
        )
    materials = _clamp_materials(
        data.materials if data.materials is not None else user.ai_offer_materials
    )

    # Лид — самый свежий из своих баз. Выбирать его вручную незачем: проверка
    # нужна, чтобы увидеть стиль результата, а не конкретное письмо.
    lead = (
        db.query(Lead)
        .join(LeadDatabase, Lead.database_id == LeadDatabase.id)
        .filter(LeadDatabase.owner_id == user.id)
        .order_by(Lead.id.desc())
        .first()
    )
    if lead is None:
        raise HTTPException(
            400,
            "Нет лида для проверки. Сначала соберите базу лидов в разделе «Поиск».",
        )

    analyzed: list[str] = []
    notes: list[str] = []

    # Telegram-канал автора, если он найден при поиске: посты — самый живой
    # контент, по которому видно, о чём человек говорит прямо сейчас.
    tg_channel = (getattr(lead, "telegram_channel", "") or "").strip()
    tg_posts: list[str] = []
    if tg_channel:
        tg_posts = _telegram_posts(tg_channel, materials, user.id)
        if tg_posts:
            analyzed += [f"Telegram: {p[:70]}" for p in tg_posts]
        else:
            notes.append("Посты Telegram-канала получить не удалось.")

    # Последние видео YouTube. Стоят квоты, поэтому только если есть id канала.
    if lead.channel_id:
        key = _ai_key(db, user, q.COST_CHANNELS_LIST * 2)
        try:
            videos = yt.channel_recent_videos(key.key, lead.channel_id, materials)
        except yt.YouTubeError as exc:
            raise HTTPException(502, f"YouTube: {exc.message}") from exc
        q.spend(db, key, "channels.list")
        q.spend(db, key, "playlistItems.list")
        db.commit()
        if videos:
            analyzed += [f"YouTube: {v['title']}" for v in videos]
            # Названия последних видео — это и есть «контент», по которому ИИ
            # цепляется за деталь. Отдаём их модели тем же блоком, что и посты.
            tg_posts = [f"Видео на канале: {v['title']}" for v in videos] + tg_posts
        else:
            notes.append("У канала нет доступных видео — писали по описанию канала.")

    if not analyzed:
        notes.append("Контента для анализа не нашлось — ИИ писал по описанию канала.")

    try:
        variants = ai_offer_mod.generate_variants(
            template,
            lead,
            base_url=base_url,
            api_key=api_key,
            model=model,
            tg_posts=tg_posts,
        )
    except ai_offer_mod.AiOfferError as exc:
        logger.warning("Генерация оффера не удалась: %s", exc)
        log_event(db, user.id, "offers", f"ИИ не смог написать оффер: {exc}", "error")
        raise HTTPException(502, str(exc)) from exc

    log_event(
        db,
        user.id,
        "offers",
        f"ИИ написал оффер по каналу «{lead.title}» ({len(analyzed)} материалов)",
        "success",
    )
    return AiOfferOut(
        text=variants[0]["text"],
        variants=variants,
        lead_title=lead.title or "",
        analyzed=analyzed[:20],
        note=" ".join(notes),
    )


def _telegram_posts(channel: str, limit: int, owner_id: int) -> list[str]:
    """Последние посты канала через серверный Telegram-gateway.

    Пустой список — не ошибка: если клиент не поднят или аккаунт не подключён,
    оффер всё равно можно написать по описанию канала — просто честно скажем об этом.
    """
    channel = (channel or "").strip().lstrip("@")
    if not channel:
        return []
    try:
        return gateway.channel_posts(owner_id, channel, limit)
    except RuntimeError:
        logger.info("Посты Telegram не получены", exc_info=True)
        return []


def _chat(base_url: str, api_key: str, model: str, system: str, user_msg: str) -> str:
    """Один запрос к OpenAI-совместимому API без tool calling."""
    resp = httpx.post(
        base_url.rstrip("/") + "/chat/completions",
        json={
            "model": model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user_msg},
            ],
        },
        headers={"Authorization": f"Bearer {api_key}"},
        timeout=60.0,
    )
    resp.raise_for_status()
    return resp.json().get("choices", [{}])[0].get("message", {}).get("content", "") or ""
