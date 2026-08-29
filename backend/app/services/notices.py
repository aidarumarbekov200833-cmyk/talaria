"""Напоминания, которые сервис отправляет сам: тариф на исходе и квота на исходе.

Почему это отдельный модуль, а не проверка при заходе в кабинет. Оба сообщения
нужны как раз тому, кто в кабинет не заходит: человек не продлевает тариф именно
потому, что забыл о нём. Проверка «на входе» доходила бы только до тех, кому
напоминание уже не нужно.

Оба напоминания идемпотентны по состоянию в базе, а не по времени запуска:
перезапуск сервиса, второй экземпляр процесса или ручной вызов не должны
приводить к повторному сообщению.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session

from ..alerts import notify_target_of, notify_user
from ..models import AppSettings, EventLog, Subscription

logger = logging.getLogger("outly.notices")

# Этапы напоминания об окончании тарифа, в днях до даты окончания. По убыванию:
# первым срабатывает более ранний. Двух хватает — три и больше превращают
# полезное напоминание в назойливость, и человек отключает уведомления совсем.
EXPIRY_STAGES = (3, 1)

# Пороги расхода дневной квоты YouTube. 80% — ещё можно спокойно доработать
# день, 95% — поиск вот-вот перестанет находить. Сообщаем один раз на порог за
# квотный день.
QUOTA_STAGES = (95, 80)


def _settings_row(db: Session, owner_id: int) -> AppSettings:
    row = db.query(AppSettings).filter_by(owner_id=owner_id).one_or_none()
    if row is None:
        row = AppSettings(owner_id=owner_id)
        db.add(row)
        db.flush()
    return row


def _in_app(db: Session, owner_id: int, message: str, level: str = "warning") -> None:
    """Уведомление внутри кабинета. Тот же канал, что у объявлений админа."""
    db.add(
        EventLog(
            owner_id=owner_id,
            scope="notification",
            level=level,
            message=message[:2000],
            ref_type="notice",
        )
    )


def expiry_stage_for(days_left: int) -> int:
    """Какому этапу соответствует остаток в днях. 0 — напоминать пока рано.

    Границы включающие сверху: за 2 дня до конца человек всё ещё должен
    получить напоминание «через три дня», если предыдущий запуск его пропустил
    (сервис лежал, перезапускался). Иначе редкая пауза стоила бы продления.
    """
    for stage in EXPIRY_STAGES:
        if days_left <= stage:
            return stage
    return 0


def _expiry_text(days_left: int, plan: str) -> str:
    if days_left <= 0:
        return "Тариф Talaria закончился. Поиск и рассылка остановлены до продления."
    if days_left == 1:
        return f"Тариф Talaria ({plan}) заканчивается завтра. Продлите, чтобы рассылка не встала."
    return (
        f"Тариф Talaria ({plan}) заканчивается через {days_left} дн. "
        "Продлите заранее — рассылка и поиск остановятся в день окончания."
    )


def send_expiry_notices(db: Session, now: datetime | None = None) -> int:
    """Разослать напоминания об окончании тарифа. Возвращает число отправленных.

    Заблокированных пропускаем: им напоминать о продлении нечестно, доступ им
    закрыл админ, а не календарь.
    """
    moment = now or datetime.now(UTC)
    horizon = moment + timedelta(days=max(EXPIRY_STAGES))
    rows = (
        db.query(Subscription)
        .filter(
            Subscription.paid_until.isnot(None),
            Subscription.paid_until <= horizon,
            Subscription.paid_until > moment,
            Subscription.blocked.is_(False),
            Subscription.status == "active",
        )
        .all()
    )

    sent = 0
    for subscription in rows:
        paid_until = subscription.paid_until
        if paid_until.tzinfo is None:
            paid_until = paid_until.replace(tzinfo=UTC)
        # Округляем вверх: «осталось 0.4 дня» для человека — это «завтра
        # кончится», а не «уже кончилось».
        seconds_left = (paid_until - moment).total_seconds()
        days_left = max(1, -(-int(seconds_left) // 86400))
        stage = expiry_stage_for(days_left)
        if not stage:
            continue

        settings_row = _settings_row(db, subscription.owner_id)
        same_period = (
            settings_row.expiry_notice_for is not None
            and abs((settings_row.expiry_notice_for.replace(tzinfo=UTC) - paid_until).total_seconds())
            < 60
        )
        already = int(settings_row.expiry_notice_stage or 0)
        if same_period and already and already <= stage:
            # Про этот этап (или более поздний) уже сообщали для этой же даты.
            continue

        text = _expiry_text(days_left, subscription.plan or "—")
        _in_app(db, subscription.owner_id, text)
        settings_row.expiry_notice_stage = stage
        settings_row.expiry_notice_for = paid_until
        sent += 1

        target = notify_target_of(db, subscription.owner_id)
        if target:
            notify_user(
                target,
                text,
                dedupe_key=f"expiry:{subscription.owner_id}:{paid_until.date()}:{stage}",
            )

    if sent:
        db.commit()
    return sent


def quota_stage_for(used: int, limit: int) -> int:
    """Порог, который перешагнул расход. 0 — до порогов ещё далеко."""
    if limit <= 0:
        return 0
    percent = used * 100 // limit
    for stage in QUOTA_STAGES:
        if percent >= stage:
            return stage
    return 0


def notify_quota(db: Session, owner_id: int, usage_row, limit: int) -> bool:
    """Предупредить владельца, что дневная квота ключа на исходе.

    Вызывается сразу после списания, а не отдельным обходом: расход меняется
    только здесь, и ловить переход через порог в момент перехода — точнее и
    дешевле, чем сканировать таблицу по расписанию.

    Ошибки гасим: уведомление не имеет права ломать поиск, ради которого квоту
    и тратят.
    """
    try:
        stage = quota_stage_for(int(usage_row.units or 0), limit)
        if not stage or int(usage_row.notified_pct or 0) >= stage:
            return False
        usage_row.notified_pct = stage
        left = max(0, limit - int(usage_row.units or 0))
        # Время сброса считаем, а не пишем константой: YouTube обнуляет квоту в
        # полночь по тихоокеанскому времени, и относительно Москвы этот момент
        # сдвигается на час при переходе на летнее время.
        from ..quota import next_reset

        reset_msk = (next_reset() + timedelta(hours=3)).strftime("%H:%M")
        text = (
            f"Дневная квота YouTube израсходована на {stage}%. "
            f"Осталось примерно {left} единиц. "
            f"Квота обнулится в {reset_msk} по Москве."
        )
        _in_app(db, owner_id, text)
        target = notify_target_of(db, owner_id)
        if target:
            notify_user(
                target,
                text,
                dedupe_key=f"quota:{owner_id}:{usage_row.day}:{stage}",
            )
        return True
    except Exception as exc:  # noqa: BLE001 — предупреждение вторично по отношению к поиску
        logger.warning("Не удалось предупредить о квоте: %s", type(exc).__name__)
        return False
