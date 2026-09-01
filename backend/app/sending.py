"""Движок рассылки: запуск сразу, две раздельные ветки логики.

Почему отдельный модуль, а не тело эндпоинта: отправка — самая рискованная
часть сервиса (чужой аккаунт, чужой API, баны за спам), и её надо было
отделить от разбора HTTP-запроса, чтобы тестировать и читать по частям.

Две ветки принципиально разные, поэтому и функции разные:

* обычные офферы  — текст уже написан человеком; подставляем плейсхолдеры,
  по желанию разворачиваем спинтакс. Ничего не стоит, работает всегда.
* AI-оффер        — на каждого лида отдельный запрос к модели по шаблону.
  Дорого и медленно, поэтому доступно только на дорогих тарифах, а ошибка
  генерации отменяет отправку именно этому лиду, а не всей рассылке.
"""

from __future__ import annotations

import logging
import random
import time
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from . import ai_offer as ai_offer_mod
from . import alerts
from . import spintax as spintax_mod
from .account_health import apply_error, mark_success, release_expired
from .config import settings
from .events_hub import hub
from .models import (
    AppSettings,
    Campaign,
    CampaignLog,
    CampaignSend,
    Lead,
    Offer,
    User,
    WarmupAccount,
)
from .placeholders import render
from .plans import effective_plan_slug, get_plan
from .services import get_settings, log_event, roll_month
from .services.offers import album_of
from .telegram_gateway import gateway
from .telegram_sessions import live_safe_accounts, probe_account_safety

logger = logging.getLogger("outly.sending")

# Сколько сообщений максимум уходит за один запуск. Отправка синхронная через
# MTProto: тысяча сообщений в одном HTTP-запросе гарантированно упрётся в
# таймаут, а аккаунт — в ограничение Telegram за спам.
MAX_PER_LAUNCH = 200


def clog(db: Session, campaign_id: int, message: str, level: str = "info") -> None:
    """Строка в журнал рассылки. Журнал — единственное, что видит человек."""
    db.add(CampaignLog(campaign_id=campaign_id, message=message[:2000], level=level))


def pick_accounts(db: Session, owner_id: int, ids: list[int]) -> list[WarmupAccount]:
    """С каких подключённых аккаунтов отправлять.

    Пустой список ids = «со всех подключённых». Аккаунт без tg_id не подключён
    (человек не подключил Telegram-сессию), и отправлять с него нечем.
    """
    release_expired(db, owner_id)
    query = db.query(WarmupAccount).filter(
        WarmupAccount.owner_id == owner_id,
        or_(
            WarmupAccount.connected_at.isnot(None),
            WarmupAccount.is_primary.is_(False)
            & WarmupAccount.tg_id.isnot(None)
            & (WarmupAccount.tg_id != ""),
        ),
        WarmupAccount.tg_id.isnot(None),
        WarmupAccount.tg_id != "",
        WarmupAccount.sending_enabled.is_(True),
        WarmupAccount.operational_status == "ready",
    )
    if ids:
        query = query.filter(WarmupAccount.id.in_(ids))
    rows = query.order_by(WarmupAccount.id).all()
    # Fail-closed: если gateway недоступен или Telegram отозвал сессию, слот
    # не получает ни одного сообщения. Старый connected_at сам по себе не ключ.
    return live_safe_accounts(db, rows, fail_closed=True)


def account_label(acc: WarmupAccount) -> str:
    if acc.tg_username:
        return "@" + acc.tg_username.lstrip("@")
    return acc.label or f"аккаунт {acc.id}"


def recipient_username(lead: Lead) -> str:
    """Кому писать в личку.

    Только личный аккаунт (tg_username). telegram_channel — это канал автора,
    в канал в личку не напишешь, поэтому подменять одно другим нельзя.
    """
    if (lead.tg_status or "unknown") in {
        "channel",
        "bot",
        "rejected",
        "error",
        "missing",
        "pending",
    }:
        return ""
    return (lead.tg_username or "").strip().lstrip("@")


@dataclass(frozen=True)
class SendOutcome:
    ok: bool
    error: str = ""
    error_code: str = ""
    retry_after: int | None = None
    message_id: int | None = None
    recipient_tg_id: str = ""
    recipient_username: str = ""

    def __iter__(self):  # noqa: ANN204 — сохраняет совместимость с `(ok, error)`
        yield self.ok
        yield self.error


def send_via_client(
    owner_id: int,
    account_id: int,
    username: str,
    text: str,
    *,
    image: str = "",
    images: list[str] | None = None,
    watch_seconds: int = 3600,
) -> SendOutcome:
    """Отправить одно сообщение через tg-mini-client. Возвращает (успех, ошибка).

    Сетевой сбой «до шлюза» повторяем один раз с паузой: tg-mini-client при
    падении перезапускается docker'ом за считанные секунды, и терять лида
    из-за одного неудачного момента жалко. Ошибки 4xx (нет такого ника, нет
    сессии) не повторяем — они не лечатся ожиданием.

    Отдельный случай — GATEWAY_INTERRUPTED (таймаут/обрыв уже после отправки
    запроса): сообщение могло реально уйти в Telegram, и повтор означал бы
    дубль первого сообщения живому человеку — это жалоба и бан аккаунта.
    Такой исход не повторяем никогда и возвращаем SEND_UNCERTAIN.
    """
    last_error = ""
    error_code = ""
    retry_after = None
    for attempt in (1, 2):
        try:
            body = gateway.send(
                owner_id,
                account_id,
                username,
                text,
                image=image,
                # Пустой список, а не None: шлюз ждёт массив.
                images=list(images or []),
                watch_seconds=watch_seconds,
            )
            raw_message_id = body.get("message_id")
            message_id = int(raw_message_id) if raw_message_id is not None else None
            return SendOutcome(
                True,
                message_id=message_id,
                recipient_tg_id=str(body.get("peer_tg_id") or "")[:64],
                recipient_username=str(body.get("peer_username") or username)
                .strip()
                .lstrip("@")[:64],
            )
        except RuntimeError as exc:
            last_error = str(exc)[:500]
            error_code = str(getattr(exc, "code", ""))
            retry_after = getattr(exc, "retry_after", None)
            if error_code == "GATEWAY_INTERRUPTED":
                # Исход неизвестен: шлюз получил запрос, но ответа нет. Не
                # повторяем (риск дубля лиду) и отдаём узнаваемый код — по
                # нему статистика отличит «точно не ушло» от «неизвестно».
                return SendOutcome(
                    False,
                    "Ответ шлюза не получен; сообщение могло уйти — повтор не выполнялся, "
                    "чтобы не отправить лиду дубль",
                    "SEND_UNCERTAIN",
                    retry_after,
                )
            if error_code in {
                "FLOOD_WAIT",
                "FLOOD_PREMIUM_WAIT",
                "PEER_FLOOD",
                "PHONE_NUMBER_BANNED",
                "USER_DEACTIVATED_BAN",
                "AUTH_KEY_UNREGISTERED",
                "AUTH_KEY_DUPLICATED",
            }:
                return SendOutcome(False, last_error, error_code, retry_after)
            # Ошибки данных/сессии ожиданием не лечатся.
            if any(
                marker in last_error.lower()
                for marker in ("не найден", "не подключ", "username", "peer")
            ):
                return SendOutcome(False, last_error, error_code, retry_after)
        if attempt == 1:
            logger.warning("Отправка @%s: %s — повтор через 3 с", username, last_error)
            time.sleep(3)
    return SendOutcome(False, last_error, error_code, retry_after)


def build_regular_text(offer: Offer, lead: Lead, use_spintax: bool) -> str:
    """Текст обычного оффера: сначала спинтакс, потом плейсхолдеры.

    Порядок важен. Спинтакс работает по фигурным скобкам, и плейсхолдеры тоже
    в фигурных скобках: если сначала подставить значения, а потом развернуть
    спинтакс, он попытается разобрать подставленный текст. Наоборот безопасно —
    spintax.expand не трогает блоки без разделителя «|».
    """
    text = offer.text or ""
    if use_spintax:
        text = spintax_mod.expand(text)
    return render(text, lead)


def _telegram_posts_for_lead(lead: Lead, owner_id: int, limit: int) -> list[str]:
    """Последние посты Telegram-канала лида — контент, по которому ИИ пишет оффер.

    Пустой список не ошибка: у большинства лидов Telegram-канала нет, и тогда ИИ
    опирается на данные YouTube-канала. Молча ловим любую сетевую ошибку —
    из-за недоступного mini-client рассылка останавливаться не должна.
    """
    channel = (getattr(lead, "telegram_channel", "") or "").strip().lstrip("@")
    if not channel or limit <= 0:
        return []
    try:
        return gateway.channel_posts(owner_id, channel, limit)
    except RuntimeError:
        return []


def _record_send(
    db: Session,
    campaign: Campaign,
    lead: Lead,
    account: WarmupAccount | None,
    *,
    status: str,
    text: str,
    offer: Offer | None,
    error: str = "",
    telegram_message_id: int | None = None,
) -> None:
    """Одна строка истории отправки.

    Уникальный индекс (campaign_id, lead_id) — последняя защита от двойной
    отправки, поэтому вставку заворачиваем в savepoint: конфликт не должен
    ронять весь запуск.
    """
    row = CampaignSend(
        owner_id=campaign.owner_id,
        campaign_id=campaign.id,
        lead_id=lead.id,
        status=status,
        error=(error or "")[:2000],
        offer_id=offer.id if offer else None,
        offer_kind=campaign.offer_mode or "regular",
        rendered_text=(text or "")[:8000],
        account_id=account.id if account else None,
        telegram_message_id=telegram_message_id,
        delivered_at=datetime.now(UTC) if status == "sent" else None,
    )
    try:
        with db.begin_nested():
            db.add(row)
    except Exception:  # noqa: BLE001 — дубль отправки не повод падать
        logger.info("Повторная отправка лиду %s в рассылке %s пропущена", lead.id, campaign.id)


def run_launch(
    db: Session,
    campaign: Campaign,
    leads: list[Lead],
    accounts: list[WarmupAccount],
    offers: list[Offer],
    *,
    ai_provider: tuple[str, str, str] | None = None,
    ai_materials: int = 5,
    resume_from: int = 0,
) -> dict:
    """Отправить партию сообщений и вернуть итог: sent / failed / problems.

    Что здесь важно по существу:

    * Обычные офферы чередуются по кругу — лид №1 получает первый оффер,
      лид №2 второй и так далее. Человек не выбирает офферы поштучно: берутся
      все его офферы, и чередование делает переписку менее шаблонной.
    * Аккаунты-отправители тоже идут по кругу: сто сообщений с одного аккаунта
      Telegram считает спамом.
    * У AI-оффера каждый текст свой. Неудачная генерация отменяет отправку
      только этому лиду (status="skipped"), а не всю рассылку: одна недоступная
      модель не должна обнулять запуск, за который уже списаны лимиты.
    * Прогресс пишем в БД и в живую ленту по ходу дела — запуск синхронный и
      длинный, и человек должен видеть движение, а не пустой экран.
    * resume_from — сколько получателей было обработано до рестарта сервера.
      Ненулевое значение означает продолжение: счётчики стартуют с сохранённых
      в кампании, прогресс и ротация офферов/аккаунтов учитывают смещение.
    """
    sent = int(campaign.sent_count or 0) if resume_from else 0
    failed = int(campaign.error_count or 0) if resume_from else 0
    problems: list[str] = []
    total = max(1, resume_from + len(leads))
    limit_row = roll_month(db, get_settings(db, campaign.owner_id))
    unlimited_messages = int(
        get_plan(effective_plan_slug(db, campaign.owner_id))["monthly_messages"]
    ) < 0
    today_start = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    sent_today = dict(
        db.query(CampaignSend.account_id, func.count(CampaignSend.id))
        .filter(
            CampaignSend.owner_id == campaign.owner_id,
            # uncertain — сообщение могло уйти: для дневного темпа аккаунта
            # считаем его потраченной отправкой (консервативно, от бана).
            CampaignSend.status.in_(("sent", "uncertain")),
            CampaignSend.sent_at >= today_start,
            CampaignSend.account_id.isnot(None),
        )
        .group_by(CampaignSend.account_id)
        .all()
    )
    account_left = {
        account.id: max(0, int(account.daily_send_limit or 30) - int(sent_today.get(account.id, 0)))
        for account in accounts
    }

    clog(db, campaign.id, f"Запуск: {len(leads)} получателей, {len(accounts)} аккаунт(ов)")
    if campaign.offer_mode == "ai":
        clog(db, campaign.id, "Режим: AI-оффер — текст пишется под каждый лид отдельно")
    else:
        names = ", ".join(o.name for o in offers[:5]) or "—"
        clog(
            db,
            campaign.id,
            f"Режим: обычные офферы ({len(offers)} шт., чередуются): {names}"
            + (" · спинтакс" if campaign.spintax else ""),
        )
    db.commit()

    cancelled = False
    for index, lead in enumerate(leads):
        # Сквозная позиция получателя в кампании: при возобновлении ротация
        # офферов и аккаунтов продолжается с того же места, что до рестарта.
        position = resume_from + index
        db.refresh(campaign)
        if campaign.status == "cancel_requested":
            cancelled = True
            clog(db, campaign.id, "Остановка подтверждена перед следующим получателем", "warning")
            db.commit()
            break
        account = None
        for offset in range(len(accounts)):
            candidate = accounts[(position + offset) % len(accounts)]
            if account_left.get(candidate.id, 0) > 0:
                account = candidate
                break
        if account is None:
            left = len(leads) - index
            failed += left
            problems.append("Дневной лимит подключённых Telegram-аккаунтов исчерпан")
            clog(
                db,
                campaign.id,
                f"Остановлено: дневной лимит аккаунтов; не обработано {left}",
                "warning",
            )
            break
        username = recipient_username(lead)
        offer: Offer | None = None
        title = lead.title or f"лид #{lead.id}"

        # --- текст сообщения ---
        if campaign.offer_mode == "ai":
            try:
                base_url, api_key, model = ai_provider or ("", "", "")
                text = ai_offer_mod.generate(
                    campaign.ai_template or "",
                    lead,
                    base_url=base_url,
                    api_key=api_key,
                    model=model,
                    tg_posts=_telegram_posts_for_lead(lead, campaign.owner_id, ai_materials),
                )
            except ai_offer_mod.AiOfferError as exc:
                failed += 1
                reason = str(exc)
                clog(db, campaign.id, f"{title}: ИИ не написал оффер — {reason}", "error")
                problems.append(f"{title}: {reason}")
                _record_send(
                    db,
                    campaign,
                    lead,
                    account,
                    status="skipped",
                    text="",
                    offer=None,
                    error=reason,
                )
                db.commit()
                continue
        else:
            offer = offers[position % len(offers)]
            text = build_regular_text(offer, lead, bool(campaign.spintax))

        # --- отправка ---
        # Счётчик нужен для статистики даже без тарифной квоты. Для старых
        # служебных планов сохраняем атомарную проверку лимита; Talaria только
        # атомарно увеличивает факт отправки.
        limit_query = db.query(AppSettings).filter(AppSettings.id == limit_row.id)
        if unlimited_messages:
            reserved = limit_query.update(
                {AppSettings.monthly_used: AppSettings.monthly_used + 1},
                synchronize_session=False,
            )
        else:
            reserved = limit_query.filter(
                AppSettings.monthly_used < AppSettings.monthly_limit,
            ).update(
                {AppSettings.monthly_used: AppSettings.monthly_used + 1},
                synchronize_session=False,
            )
        db.commit()
        if not reserved:
            left = len(leads) - index
            failed += left
            problems.append("Месячный лимит отправок исчерпан")
            clog(db, campaign.id, f"Остановлено: месячный лимит; не обработано {left}", "warning")
            break

        # Если Telegram ограничил отправителя, тот же лид без повторного списания
        # месячной квоты уходит через следующий здоровый аккаунт. Получательские
        # ошибки не гоняем по всему пулу: другой отправитель их не исправит.
        attempted_accounts: set[int] = set()
        last_account: WarmupAccount = account
        ok = False
        error = ""
        raw_outcome = SendOutcome(False, "Нет безопасного аккаунта для отправки")
        while account is not None:
            last_account = account
            attempted_accounts.add(account.id)
            # Повторяем safety-проверку внутри долгой кампании. Вызов дешёвый:
            # telegram_sessions кэширует снимок на 2 минуты, но после интервала
            # сразу увидит рост непрочитанных и выведет слот из ротации.
            safety_probe = probe_account_safety(db, account)
            if safety_probe.changed:
                db.commit()
            if not safety_probe.authorized:
                error = safety_probe.reason or "Аккаунт поставлен на автопаузу"
                raw_outcome = SendOutcome(False, error, error_code="SAFETY_PAUSE")
                account_left[account.id] = 0
                clog(
                    db,
                    campaign.id,
                    f"{account_label(account)} выведен из ротации: {error}",
                    "warning",
                )
                account = next(
                    (
                        candidate
                        for offset in range(len(accounts))
                        if (candidate := accounts[(index + offset + 1) % len(accounts)]).id
                        not in attempted_accounts
                        and account_left.get(candidate.id, 0) > 0
                        and candidate.operational_status == "ready"
                    ),
                    None,
                )
                continue
            raw_outcome = send_via_client(
                campaign.owner_id,
                account.id,
                username,
                text,
                images=album_of(offer) if offer else [],
            )
            ok, error = raw_outcome
            if ok:
                mark_success(account)
                break
            decision = apply_error(
                account,
                code=str(getattr(raw_outcome, "error_code", "")),
                error=error,
                retry_after=getattr(raw_outcome, "retry_after", None),
            )
            if not decision.quarantine:
                break
            account_left[account.id] = 0
            until = (
                decision.cooldown_until.isoformat(timespec="seconds")
                if decision.cooldown_until
                else "до переподключения"
            )
            clog(
                db,
                campaign.id,
                f"{account_label(account)} выведен из ротации ({decision.status}, {until}); "
                "пробуем следующий аккаунт",
                "warning",
            )
            account = next(
                (
                    candidate
                    for offset in range(len(accounts))
                    if (candidate := accounts[(index + offset + 1) % len(accounts)]).id
                    not in attempted_accounts
                    and account_left.get(candidate.id, 0) > 0
                    and candidate.operational_status == "ready"
                ),
                None,
            )
        # Для истории и расчёта паузы нужен фактически последний отправитель,
        # даже когда после его карантина здоровых аккаунтов больше не осталось.
        account = last_account
        if ok:
            sent += 1
            account_left[account.id] -= 1
            clog(
                db,
                campaign.id,
                f"@{username} ({title}) ← {'AI-оффер' if campaign.offer_mode == 'ai' else offer.name}"
                f" · с {account_label(account)}",
                "success",
            )
        else:
            uncertain = str(getattr(raw_outcome, "error_code", "")) == "SEND_UNCERTAIN"
            # Возвращаем месячную квоту только когда сообщение точно не ушло.
            # При SEND_UNCERTAIN оно могло дойти до лида — возврат позволил бы
            # отправить сверх лимита реально доставленных сообщений.
            if not uncertain:
                db.query(AppSettings).filter(AppSettings.id == limit_row.id).update(
                    {AppSettings.monthly_used: AppSettings.monthly_used - 1},
                    synchronize_session=False,
                )
            failed += 1
            problems.append(f"@{username}: {error}")
            clog(db, campaign.id, f"@{username} ({title}): {error}", "error")

        if ok:
            send_status = "sent"
        elif str(getattr(raw_outcome, "error_code", "")) == "SEND_UNCERTAIN":
            # Исход неизвестен: сообщение могло дойти. Отдельный статус, чтобы
            # дедупликация «кому уже писали» видела такие строки и не отправила
            # человеку первое сообщение второй раз, а «повторить неудачные»
            # их не подхватывал.
            send_status = "uncertain"
        else:
            send_status = "failed"
        _record_send(
            db,
            campaign,
            lead,
            account,
            status=send_status,
            text=text if ok else "",
            offer=offer,
            error="" if ok else error,
            telegram_message_id=getattr(raw_outcome, "message_id", None) if ok else None,
        )

        campaign.sent_count = sent
        campaign.error_count = failed
        campaign.progress = round((position + 1) * 100.0 / total, 1)
        db.commit()
        hub.campaign_update(campaign.owner_id, campaign.id, campaign.progress, campaign.status)

        # Пауза со случайным разбросом перед следующим лидом: очередь без пауз —
        # верный способ словить бан за спам. Диапазон задаётся у аккаунта;
        # SEND_DELAY_MAX_SECONDS=0 глобально выключает ожидание для тестов.
        if index + 1 < len(leads):
            if settings.send_delay_max_seconds <= 0:
                hi = lo = 0.0
            else:
                hi = max(0.0, float(account.delay_max_seconds or settings.send_delay_max_seconds))
                lo = min(max(0.0, float(account.delay_min_seconds or 0)), hi)
            if hi > 0:
                # Спим короткими отрезками: кнопка «Остановить» должна сработать
                # и при паузе 60 секунд, а не ждать следующего получателя.
                remaining = random.uniform(lo, hi)
                while remaining > 0:
                    step = min(1.0, remaining)
                    time.sleep(step)
                    remaining -= step
                    db.refresh(campaign)
                    if campaign.status == "cancel_requested":
                        cancelled = True
                        break
                if cancelled:
                    clog(db, campaign.id, "Остановка подтверждена во время паузы", "warning")
                    db.commit()
                    break

    campaign.status = "cancelled" if cancelled else "completed"
    if not cancelled:
        campaign.progress = 100.0
    campaign.sent_count = sent
    campaign.error_count = failed
    campaign.completed_at = datetime.now(UTC)
    final_message = (
        f"Остановлено пользователем: отправлено {sent}, ошибок {failed}"
        if cancelled
        else f"Готово: отправлено {sent}, ошибок {failed}"
    )
    clog(
        db, campaign.id, final_message, "warning" if cancelled else ("success" if sent else "warn")
    )
    db.commit()
    hub.campaign_update(campaign.owner_id, campaign.id, campaign.progress, campaign.status)

    log_event(
        db,
        campaign.owner_id,
        "campaigns",
        f"Рассылка «{campaign.name}»: "
        + ("остановлена; " if cancelled else "")
        + f"отправлено {sent} из {total}"
        + (f", ошибок {failed}" if failed else ""),
        "success" if sent else "warn",
    )

    # Пуш владельцу в Telegram: рассылка идёт в фоне, вкладка может быть давно
    # закрыта. Бот сможет написать только тем, кто хоть раз нажимал ему Start —
    # отказ Telegram уведомление глотает, отправку он сломать не должен.
    owner = db.get(User, campaign.owner_id)
    if owner is not None:
        alerts.notify_user(
            owner.telegram_id,
            f"Talaria: рассылка «{campaign.name}» "
            + ("остановлена — " if cancelled else "завершена — ")
            + f"отправлено {sent} из {total}"
            + (f", ошибок {failed}" if failed else "")
            + ".",
            dedupe_key=f"campaign-finish:{campaign.id}",
        )

    # Полностью провалившийся запуск — это не «пользователь ошибся», а обычно
    # сломанный mini-client или провайдер ИИ. Об этом должен узнать админ.
    if leads and not sent and not cancelled:
        alerts.report_error(
            "Рассылка",
            "отправка партии",
            "Ни одно сообщение не ушло",
            owner_id=campaign.owner_id,
            entity=f"рассылка #{campaign.id} «{campaign.name}»",
            details={"получателей": len(leads), "первая ошибка": problems[0] if problems else "—"},
            dedupe_key=f"launch-all-failed-{campaign.owner_id}",
        )

    return {"sent": sent, "failed": failed, "problems": problems[:20]}
