"""Фоновый прогон нейрокомментинга.

Порядок действий на каждый комментарий сознательно «медленный»:

    решение о допуске → выбор поста → генерация текста → отправка → пауза

Проверка допуска стоит ПЕРЕД каждым комментарием, а не один раз на старте:
пока идёт прогон, владелец может запустить рассылку, аккаунт может словить
FLOOD_WAIT, а сутки — смениться. Решение, принятое пять минут назад, в этой
области уже неактуально.

Комментирование никогда не создаёт личных сообщений и не вступает в чаты:
вступление — отдельное явное действие человека в интерфейсе.
"""

from __future__ import annotations

import logging
import threading
import time
from datetime import UTC, datetime

from sqlalchemy import func
from sqlalchemy.orm import Session

from . import account_health, database, neuro_comment
from .config import settings
from .models import Campaign, CampaignSend, NeuroComment, NeuroRun, NeuroTarget, WarmupAccount
from .services import neuro_budget
from .telegram_gateway import TelegramGatewayError, gateway

logger = logging.getLogger("outly.neuro")

# Ошибки, после которых прогон останавливается целиком, а не переходит к
# следующей цели: это состояния аккаунта, а не проблема конкретного чата.
STOP_CODES = {
    "PEER_FLOOD",
    "FLOOD_PREMIUM_WAIT",
    "PHONE_NUMBER_BANNED",
    "USER_DEACTIVATED_BAN",
    "AUTH_KEY_UNREGISTERED",
    "AUTH_KEY_DUPLICATED",
}
# Сколько последних комментариев аккаунта показываем модели, чтобы она не
# повторялась. Больше десяти — лишние токены без пользы.
RECENT_WINDOW = 10


def actions_today(db: Session, account_id: int, now: datetime | None = None) -> int:
    """Общий дневной расход аккаунта: личные отправки ПЛЮС комментарии.

    Ровно та функция, ради которой затевался общий котёл. Если считать
    комментарии отдельно, две «безопасные» фичи вместе дадут двойную нагрузку
    на один и тот же спам-скор.
    """
    start, end = neuro_budget.day_bounds(now)
    sends = (
        db.query(func.count(CampaignSend.id))
        .filter(
            CampaignSend.account_id == account_id,
            CampaignSend.sent_at >= start,
            CampaignSend.sent_at < end,
        )
        .scalar()
        or 0
    )
    comments = (
        db.query(func.count(NeuroComment.id))
        .filter(
            NeuroComment.account_id == account_id,
            NeuroComment.status == "sent",
            NeuroComment.created_at >= start,
            NeuroComment.created_at < end,
        )
        .scalar()
        or 0
    )
    return int(sends) + int(comments)


def account_busy_with_campaign(db: Session, owner_id: int, account_id: int) -> bool:
    """Занят ли аккаунт активной рассылкой прямо сейчас."""
    running = (
        db.query(Campaign.account_ids)
        .filter(Campaign.owner_id == owner_id, Campaign.status == "running")
        .all()
    )
    for (raw,) in running:
        if account_id in neuro_budget.campaign_accounts(raw):
            return True
    return False


def recent_texts(db: Session, account_id: int, limit: int = RECENT_WINDOW) -> list[str]:
    rows = (
        db.query(NeuroComment.text)
        .filter(NeuroComment.account_id == account_id, NeuroComment.status == "sent")
        .order_by(NeuroComment.id.desc())
        .limit(limit)
        .all()
    )
    return [row[0] for row in rows if row and row[0]]


def pick_post(posts: list[dict], last_post_id: int | None) -> dict | None:
    """Самый свежий пост, который ещё не комментировали.

    Свои посты пропускаем: комментарий под собственной публикацией не даёт
    ничего и выглядит странно. Посты без текста тоже — комментировать нечего,
    а «Класс!» под картинкой это ровно тот машинный комментарий, которого мы
    избегаем.
    """
    floor = int(last_post_id or 0)
    candidates = [
        post
        for post in posts
        if int(post.get("id") or 0) > floor
        and not post.get("out")
        and (post.get("text") or "").strip()
    ]
    if not candidates:
        return None
    candidates.sort(key=lambda p: int(p.get("id") or 0), reverse=True)
    return candidates[0]


def _note(run: NeuroRun, db: Session, message: str) -> None:
    run.last_note = message[:500]
    db.commit()
    logger.info("neuro run %s: %s", run.id, message)


def _ai_provider() -> tuple[str, str, str]:
    return (settings.ai_base_url, settings.ai_api_key, settings.ai_model)


def run_once(
    db: Session,
    run: NeuroRun,
    *,
    sleeper=time.sleep,
    now_fn=lambda: datetime.now(UTC),
    respect_hours: bool = True,
) -> dict:
    """Один проход по одобренным целям. Возвращает сводку для журнала."""
    stats = {"sent": 0, "failed": 0, "skipped": 0, "stopped": ""}
    account = db.get(WarmupAccount, int(run.account_id or 0))
    if not account or account.owner_id != run.owner_id:
        stats["stopped"] = "Аккаунт не выбран или не принадлежит владельцу"
        _note(run, db, stats["stopped"])
        return stats

    targets = (
        db.query(NeuroTarget)
        .filter(
            NeuroTarget.owner_id == run.owner_id,
            NeuroTarget.status == "approved",
            NeuroTarget.joined.is_(True),
        )
        # Начинаем с тех, кого дольше всего не трогали: так активность
        # размазывается по площадкам, а не долбит одну и ту же.
        .order_by(NeuroTarget.last_comment_at.asc().nullsfirst(), NeuroTarget.id.asc())
        .all()
    )
    if not targets:
        stats["stopped"] = "Нет одобренных площадок, куда вступил аккаунт"
        _note(run, db, stats["stopped"])
        return stats

    base_url, api_key, model = _ai_provider()

    for target in targets:
        decision = neuro_budget.evaluate(
            account,
            used_today=actions_today(db, account.id, now_fn()),
            run_daily_limit=run.daily_limit,
            busy_with_campaign=account_busy_with_campaign(db, run.owner_id, account.id),
            now=now_fn(),
            respect_hours=respect_hours,
        )
        if not decision.allowed:
            stats["stopped"] = decision.reason
            _note(run, db, decision.reason)
            break

        try:
            posts = gateway.neuro_posts(run.owner_id, account.id, target.username, limit=5)
        except TelegramGatewayError as exc:
            stats["failed"] += 1
            target.last_error = str(exc)[:500]
            _record(
                db, run, target, account, None, "", "failed", str(exc), getattr(exc, "code", "")
            )
            if str(getattr(exc, "code", "")) in STOP_CODES:
                account_health.apply_error(
                    account, code=exc.code, error=str(exc), retry_after=exc.retry_after
                )
                stats["stopped"] = f"Аккаунт остановлен: {exc}"
                _note(run, db, stats["stopped"])
                break
            continue

        post = pick_post(posts, target.last_post_id)
        if not post:
            stats["skipped"] += 1
            continue

        try:
            text = neuro_comment.generate(
                post.get("text") or "",
                chat_title=target.title or target.username,
                persona=run.persona,
                link=run.link,
                base_url=base_url,
                api_key=api_key,
                model=model,
                recent=recent_texts(db, account.id),
            )
        except neuro_comment.NeuroCommentError as exc:
            stats["skipped"] += 1
            _record(db, run, target, account, post.get("id"), "", "skipped", str(exc), "")
            continue

        try:
            result = gateway.neuro_comment(
                run.owner_id,
                account.id,
                target.username,
                text,
                post_id=int(post.get("id") or 0),
                kind=target.kind or "channel",
            )
        except TelegramGatewayError as exc:
            stats["failed"] += 1
            code = str(getattr(exc, "code", ""))
            target.last_error = str(exc)[:500]
            _record(db, run, target, account, post.get("id"), text, "failed", str(exc), code)
            account_health.apply_error(
                account, code=code, error=str(exc), retry_after=exc.retry_after
            )
            db.commit()
            if code in STOP_CODES:
                stats["stopped"] = f"Аккаунт остановлен: {exc}"
                _note(run, db, stats["stopped"])
                break
            continue

        stats["sent"] += 1
        target.last_post_id = int(post.get("id") or 0)
        target.last_comment_at = now_fn()
        target.comments_sent = int(target.comments_sent or 0) + 1
        target.last_error = ""
        account_health.mark_success(account)
        _record(
            db,
            run,
            target,
            account,
            post.get("id"),
            text,
            "sent",
            "",
            "",
            message_id=result.get("message_id"),
        )

        pause = neuro_budget.slowmode_pause(
            int(target.slowmode_seconds or 0), decision.next_pause()
        )
        sleeper(pause)

    run.last_finished_at = now_fn()
    db.commit()
    return stats


def _record(
    db: Session,
    run: NeuroRun,
    target: NeuroTarget,
    account: WarmupAccount,
    post_id,
    text: str,
    status: str,
    error: str,
    code: str,
    message_id=None,
) -> None:
    db.add(
        NeuroComment(
            owner_id=run.owner_id,
            run_id=run.id,
            target_id=target.id,
            account_id=account.id,
            post_id=int(post_id or 0) or None,
            message_id=int(message_id or 0) or None,
            text=text[:4000],
            status=status,
            error=(error or "")[:500],
            error_code=(code or "")[:64],
        )
    )
    db.commit()


def _run_in_background(run_id: int) -> None:
    """Своя сессия БД: объекты из HTTP-запроса в поток передавать нельзя."""
    db = database.SessionLocal()
    try:
        run = db.get(NeuroRun, run_id)
        if not run:
            return
        run.status = "running"
        run.last_started_at = datetime.now(UTC)
        db.commit()
        stats = run_once(db, run)
        run.status = "idle"
        run.last_note = (
            f"Отправлено {stats['sent']}, пропущено {stats['skipped']}, "
            f"ошибок {stats['failed']}." + (f" {stats['stopped']}" if stats["stopped"] else "")
        )[:500]
        db.commit()
    except Exception:  # noqa: BLE001 - поток не должен падать молча
        logger.exception("Прогон нейрокомментинга упал")
        try:
            run = db.get(NeuroRun, run_id)
            if run:
                run.status = "idle"
                run.last_note = "Внутренняя ошибка прогона, подробности в журнале сервера"
                db.commit()
        except Exception:  # noqa: BLE001
            logger.exception("Не удалось пометить прогон как остановленный")
    finally:
        db.close()


def start(run_id: int) -> None:
    thread = threading.Thread(
        target=_run_in_background, args=(run_id,), name=f"neuro-{run_id}", daemon=True
    )
    thread.start()
