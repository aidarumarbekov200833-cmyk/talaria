"""Нейрокомментинг: площадки, прогон и журнал.

Отдельный роутер, а не режим рассылки. Причина не в аккуратности, а в риске:
рассылка пишет незнакомым людям в личку, комментинг — на публичные площадки,
где аккаунт состоит участником. Это разные действия с разной ценой ошибки, и
единственное, что у них общее — дневной котёл аккаунта (см. services/neuro_budget).

Автовступление в найденные чаты в API отсутствует намеренно: `joinChannel`
сильнее всего бьёт по репутации аккаунта, поэтому вступление — только явная
кнопка человека, по одной цели за раз.
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from .. import neuro_comment, neuro_worker
from ..config import settings as app_config
from ..database import get_db
from ..dependencies import get_current_user
from ..models import NeuroComment, NeuroRun, NeuroTarget, User, WarmupAccount
from ..plans import TRIAL_PLAN, effective_plan_slug, neuro_allowed
from ..schemas import (
    NeuroBudgetOut,
    NeuroLogOut,
    NeuroPreviewIn,
    NeuroPreviewOut,
    NeuroRunIn,
    NeuroRunOut,
    NeuroSearchIn,
    NeuroSearchItem,
    NeuroSearchOut,
    NeuroTargetIn,
    NeuroTargetOut,
    NeuroTargetsOut,
    NeuroTargetStatusIn,
    OkOut,
)
from ..services import log_event, neuro_budget
from ..telegram_gateway import TelegramGatewayError, gateway

router = APIRouter(prefix="/api/neuro", tags=["neuro"])

TARGET_LIMIT = 200
STATUSES = {"approved", "rejected", "paused", "candidate"}


def _require_plan(db: Session, user: User) -> None:
    if neuro_allowed(db, user.id):
        return
    # На пробном тарифе причина другая, чем «не входит в тариф»: человек только
    # что зашёл и должен понять, что это временно и почему.
    if effective_plan_slug(db, user.id) == TRIAL_PLAN:
        raise HTTPException(
            402,
            "На пробном тарифе нейрокомментинг недоступен: он пишет от вашего имени "
            "в чужих чатах. Откроется после оплаты тарифа Talaria.",
        )
    raise HTTPException(403, "Нейрокомментинг не входит в ваш тариф")


def _run_for(db: Session, user: User) -> NeuroRun:
    """Настройка прогона у пользователя одна; создаём при первом обращении."""
    run = db.query(NeuroRun).filter(NeuroRun.owner_id == user.id).order_by(NeuroRun.id).first()
    if run:
        return run
    run = NeuroRun(owner_id=user.id, name="Нейрокомментинг")
    db.add(run)
    db.commit()
    db.refresh(run)
    return run


def _account_for(db: Session, user: User, run: NeuroRun) -> WarmupAccount | None:
    if run.account_id:
        account = db.get(WarmupAccount, int(run.account_id))
        if account and account.owner_id == user.id:
            return account
    return (
        db.query(WarmupAccount)
        .filter(WarmupAccount.owner_id == user.id, WarmupAccount.connected_at.isnot(None))
        .order_by(WarmupAccount.is_primary.desc(), WarmupAccount.id)
        .first()
    )


def _target_out(target: NeuroTarget) -> NeuroTargetOut:
    data = NeuroTargetOut.model_validate(target)
    # Канал без чата обсуждений комментировать негде — показываем это явно,
    # чтобы человек не гадал, почему цель молчит.
    data.can_comment = target.kind == "group" or bool(target.linked_chat_id)
    return data


@router.get("/targets", response_model=NeuroTargetsOut)
def list_targets(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    rows = (
        db.query(NeuroTarget)
        .filter(NeuroTarget.owner_id == user.id)
        .order_by(NeuroTarget.status, NeuroTarget.id.desc())
        .all()
    )
    return NeuroTargetsOut(targets=[_target_out(row) for row in rows])


@router.post("/targets", response_model=NeuroTargetOut)
def add_target(
    payload: NeuroTargetIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Добавить площадку вручную. Проверяем её в Telegram сразу, а не при прогоне."""
    _require_plan(db, user)
    username = payload.username.strip().lstrip("@").lower()
    existing = (
        db.query(NeuroTarget)
        .filter(NeuroTarget.owner_id == user.id, NeuroTarget.username == username)
        .first()
    )
    if existing:
        raise HTTPException(409, "Эта площадка уже добавлена")
    if db.query(NeuroTarget).filter(NeuroTarget.owner_id == user.id).count() >= TARGET_LIMIT:
        raise HTTPException(400, f"Больше {TARGET_LIMIT} площадок хранить нельзя")

    run = _run_for(db, user)
    account = _account_for(db, user, run)
    if not account:
        raise HTTPException(400, "Сначала подключите Telegram-аккаунт в настройках")
    try:
        info = gateway.neuro_info(user.id, account.id, username)
    except TelegramGatewayError as exc:
        raise HTTPException(502, f"Telegram не отдал данные площадки: {exc}") from exc
    if not info.get("kind"):
        raise HTTPException(400, "Это не канал и не группа")

    target = NeuroTarget(
        owner_id=user.id,
        username=username,
        tg_id=str(info.get("id") or "")[:40],
        title=str(info.get("title") or "")[:300],
        kind=str(info.get("kind") or "channel"),
        members=info.get("participants"),
        source="manual",
        status="approved",
        joined=bool(info.get("joined")),
        joined_at=datetime.now(UTC) if info.get("joined") else None,
        slowmode_seconds=int(info.get("slowmode_seconds") or 0),
        linked_chat_id=str(info.get("linked_chat_id") or "")[:40],
    )
    db.add(target)
    db.commit()
    db.refresh(target)
    log_event(db, user.id, "neuro", f"Добавлена площадка @{username}", ref_type="neuro_target")
    return _target_out(target)


@router.post("/search", response_model=NeuroSearchOut)
def search_targets(
    payload: NeuroSearchIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Поиск площадок по теме.

    Результат — кандидаты, а НЕ готовые цели: ни вступления, ни комментариев
    здесь не происходит. Человек сам решает, куда лезть, потому что часть
    найденного окажется чатами, где за рекламу банят в первую минуту.
    """
    _require_plan(db, user)
    run = _run_for(db, user)
    account = _account_for(db, user, run)
    if not account:
        raise HTTPException(400, "Сначала подключите Telegram-аккаунт в настройках")
    try:
        found = gateway.neuro_search(user.id, account.id, payload.query, payload.limit)
    except TelegramGatewayError as exc:
        raise HTTPException(502, f"Поиск в Telegram не удался: {exc}") from exc

    known = {
        row[0]
        for row in db.query(NeuroTarget.username).filter(NeuroTarget.owner_id == user.id).all()
    }
    items = [
        NeuroSearchItem(
            username=str(item.get("username") or ""),
            title=str(item.get("title") or ""),
            kind=str(item.get("kind") or ""),
            participants=item.get("participants"),
            joined=bool(item.get("joined")),
            known=str(item.get("username") or "").lower() in known,
        )
        for item in found
        if item.get("username")
    ]
    return NeuroSearchOut(
        results=items,
        note="Проверьте правила каждого чата: во многих сообществах реклама запрещена.",
    )


@router.post("/targets/{target_id}/status", response_model=NeuroTargetOut)
def set_status(
    target_id: int,
    payload: NeuroTargetStatusIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    _require_plan(db, user)
    if payload.status not in STATUSES:
        raise HTTPException(400, "Неизвестный статус")
    target = db.get(NeuroTarget, target_id)
    if not target or target.owner_id != user.id:
        raise HTTPException(404, "Площадка не найдена")
    target.status = payload.status
    db.commit()
    db.refresh(target)
    return _target_out(target)


@router.post("/targets/{target_id}/join", response_model=NeuroTargetOut)
def join_target(
    target_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Вступить в площадку. Одно нажатие — одно вступление, автоматизации нет."""
    _require_plan(db, user)
    target = db.get(NeuroTarget, target_id)
    if not target or target.owner_id != user.id:
        raise HTTPException(404, "Площадка не найдена")
    run = _run_for(db, user)
    account = _account_for(db, user, run)
    if not account:
        raise HTTPException(400, "Сначала подключите Telegram-аккаунт в настройках")

    # Вступление тратит тот же дневной котёл: это действие в Telegram, а не
    # бесплатная операция в нашей базе.
    decision = neuro_budget.evaluate(
        account,
        used_today=neuro_worker.actions_today(db, account.id),
        run_daily_limit=run.daily_limit,
        busy_with_campaign=neuro_worker.account_busy_with_campaign(db, user.id, account.id),
    )
    if not decision.allowed:
        raise HTTPException(400, decision.reason)

    try:
        gateway.neuro_join(user.id, account.id, target.username)
        info = gateway.neuro_info(user.id, account.id, target.username)
    except TelegramGatewayError as exc:
        target.last_error = str(exc)[:500]
        db.commit()
        raise HTTPException(502, f"Вступить не удалось: {exc}") from exc

    target.joined = True
    target.joined_at = datetime.now(UTC)
    target.status = "approved"
    target.slowmode_seconds = int(info.get("slowmode_seconds") or 0)
    target.linked_chat_id = str(info.get("linked_chat_id") or "")[:40]
    target.members = info.get("participants")
    target.last_error = ""
    db.commit()
    db.refresh(target)
    log_event(db, user.id, "neuro", f"Вступление в @{target.username}", ref_type="neuro_join")
    return _target_out(target)


@router.delete("/targets/{target_id}", response_model=OkOut)
def delete_target(
    target_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    target = db.get(NeuroTarget, target_id)
    if not target or target.owner_id != user.id:
        raise HTTPException(404, "Площадка не найдена")
    db.delete(target)
    db.commit()
    return OkOut(ok=True)


@router.get("/run", response_model=NeuroRunOut)
def read_run(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return NeuroRunOut.model_validate(_run_for(db, user))


@router.put("/run", response_model=NeuroRunOut)
def update_run(
    payload: NeuroRunIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    _require_plan(db, user)
    run = _run_for(db, user)
    if payload.account_id is not None:
        account = db.get(WarmupAccount, int(payload.account_id))
        if not account or account.owner_id != user.id:
            raise HTTPException(404, "Аккаунт не найден")
        run.account_id = account.id
    run.name = payload.name
    run.mode = payload.mode if payload.mode in {"targets", "topic"} else "targets"
    run.topic = payload.topic
    run.persona = payload.persona
    run.link = payload.link
    run.daily_limit = payload.daily_limit
    db.commit()
    db.refresh(run)
    return NeuroRunOut.model_validate(run)


@router.get("/budget", response_model=NeuroBudgetOut)
def read_budget(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """Остаток общего дневного котла аккаунта."""
    run = _run_for(db, user)
    account = _account_for(db, user, run)
    if not account:
        return NeuroBudgetOut(reason="Telegram-аккаунт не подключён")
    used = neuro_worker.actions_today(db, account.id)
    decision = neuro_budget.evaluate(
        account,
        used_today=used,
        run_daily_limit=run.daily_limit,
        busy_with_campaign=neuro_worker.account_busy_with_campaign(db, user.id, account.id),
    )
    tier = neuro_budget.tier_for(account)
    return NeuroBudgetOut(
        account_id=account.id,
        account_label=account.label or "",
        tier=tier.slug,
        tier_title=tier.title,
        used_today=used,
        left_today=decision.left_today,
        allowed=decision.allowed,
        reason=decision.reason,
    )


@router.post("/start", response_model=NeuroRunOut)
def start_run(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    _require_plan(db, user)
    run = _run_for(db, user)
    if run.status == "running":
        raise HTTPException(409, "Прогон уже идёт")
    account = _account_for(db, user, run)
    if not account:
        raise HTTPException(400, "Сначала подключите Telegram-аккаунт в настройках")
    run.account_id = account.id
    ready = (
        db.query(NeuroTarget)
        .filter(
            NeuroTarget.owner_id == user.id,
            NeuroTarget.status == "approved",
            NeuroTarget.joined.is_(True),
        )
        .count()
    )
    if not ready:
        raise HTTPException(400, "Нет площадок, куда аккаунт вступил")
    if not (app_config.ai_base_url and app_config.ai_api_key and app_config.ai_model):
        raise HTTPException(400, "ИИ не настроен: комментарии генерировать нечем")
    run.status = "running"
    run.last_note = "Запуск…"
    db.commit()
    neuro_worker.start(run.id)
    db.refresh(run)
    return NeuroRunOut.model_validate(run)


@router.post("/stop", response_model=NeuroRunOut)
def stop_run(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    run = _run_for(db, user)
    run.status = "stopped"
    run.last_note = "Остановлено вручную"
    db.commit()
    db.refresh(run)
    return NeuroRunOut.model_validate(run)


@router.post("/preview", response_model=NeuroPreviewOut)
def preview_comment(
    payload: NeuroPreviewIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Показать, что бот написал бы под последним постом. Ничего не отправляет.

    Нужен, чтобы человек увидел тон до того, как текст уйдёт в чужой чат:
    единственный способ понять, «звучит ли это как человек», — прочитать.
    """
    _require_plan(db, user)
    run = _run_for(db, user)
    account = _account_for(db, user, run)
    if not account:
        raise HTTPException(400, "Сначала подключите Telegram-аккаунт в настройках")
    username = payload.username.strip().lstrip("@").lower()
    try:
        posts = gateway.neuro_posts(user.id, account.id, username, limit=5)
    except TelegramGatewayError as exc:
        raise HTTPException(502, f"Telegram не отдал посты: {exc}") from exc
    post = neuro_worker.pick_post(posts, 0)
    if not post:
        raise HTTPException(400, "У площадки нет постов с текстом")
    try:
        comment = neuro_comment.generate(
            post.get("text") or "",
            chat_title=username,
            persona=run.persona,
            link=run.link,
            base_url=app_config.ai_base_url,
            api_key=app_config.ai_api_key,
            model=app_config.ai_model,
            recent=neuro_worker.recent_texts(db, account.id),
        )
    except neuro_comment.NeuroCommentError as exc:
        raise HTTPException(400, str(exc)) from exc
    return NeuroPreviewOut(post=(post.get("text") or "")[:1000], comment=comment)


@router.get("/log", response_model=NeuroLogOut)
def read_log(
    limit: int = 50,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    rows = (
        db.query(NeuroComment)
        .filter(NeuroComment.owner_id == user.id)
        .order_by(NeuroComment.id.desc())
        .limit(max(1, min(200, limit)))
        .all()
    )
    return NeuroLogOut(items=list(rows))
