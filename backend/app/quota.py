"""Учёт квоты YouTube Data API v3.

Почему считаем сами
-------------------
У YouTube нет метода, который вернул бы остаток квоты. Реальное число видно
только в Google Cloud Console (IAM & Admin → Quotas). Поэтому здесь мы ведём
собственный счётчик: перед каждым вызовом знаем его документированную цену и
прибавляем её к расходу за сутки. Цифра в интерфейсе — это наш счётчик, и
подписан он именно так, без обещания «данные от Google».

Что ещё важно и о чём легко соврать
-----------------------------------
1. Квота выдаётся на *проект* Cloud, а не на ключ. Два ключа из одного проекта
   делят одни 10 000 единиц. Поэтому суммируем лимиты только по различным
   project_label; ключи без метки считаются отдельными проектами (пользователь
   сам решает, что верно — мы не угадываем).
2. Сброс — в полночь по тихоокеанскому времени, не по UTC и не по местному.
3. Наш счётчик может расходиться с Google: сюда не попадают вызовы, сделанные
   тем же ключом из других приложений. Об этом сказано в подписи.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

from sqlalchemy.orm import Session

from .models import ApiKey, QuotaUsage

# Документированная стоимость вызовов (units).
# https://developers.google.com/youtube/v3/determine_quota_cost
COST_SEARCH_LIST = 100
COST_CHANNELS_LIST = 1
COST_VIDEOS_LIST = 1

COSTS = {
    "search.list": COST_SEARCH_LIST,
    "channels.list": COST_CHANNELS_LIST,
    "videos.list": COST_VIDEOS_LIST,
}

DEFAULT_DAILY_QUOTA = 10_000

SOURCE_NOTE = (
    "Счётчик наш: YouTube не отдаёт остаток квоты через API — точное число "
    "только в Google Cloud Console. Считаем по документированной цене вызовов "
    "(search.list — 100 единиц, channels.list и videos.list — по 1). "
    "Расход этих же ключей в других приложениях мы не видим."
)


def _pacific_offset(moment: datetime) -> timedelta:
    """Смещение тихоокеанского времени без сторонних библиотек.

    PST — UTC-8, PDT — UTC-7. Летнее время в США: со второго воскресенья марта
    по первое воскресенье ноября, переключение в 02:00 местного времени.
    Считаем по дате в UTC — на границе возможна ошибка в пару часов, и это
    честнее, чем тянуть зависимость ради двух дней в году.
    """
    year = moment.year

    def second_sunday_march() -> datetime:
        d = datetime(year, 3, 8, tzinfo=UTC)
        while d.weekday() != 6:  # 6 = воскресенье
            d += timedelta(days=1)
        return d

    def first_sunday_november() -> datetime:
        d = datetime(year, 11, 1, tzinfo=UTC)
        while d.weekday() != 6:
            d += timedelta(days=1)
        return d

    if second_sunday_march() <= moment < first_sunday_november():
        return timedelta(hours=-7)
    return timedelta(hours=-8)


def pacific_now() -> datetime:
    now = datetime.now(UTC)
    return now + _pacific_offset(now)


def quota_day() -> date:
    """Текущие «квотные сутки» — дата по тихоокеанскому времени."""
    return pacific_now().date()


def next_reset() -> datetime:
    """Ближайший сброс квоты в UTC (полночь по тихоокеанскому времени)."""
    now = datetime.now(UTC)
    offset = _pacific_offset(now)
    local = now + offset
    midnight_local = datetime.combine(local.date() + timedelta(days=1), datetime.min.time())
    return midnight_local.replace(tzinfo=UTC) - offset


def mask_key(key: str) -> str:
    """Ключ наружу — только хвостом: полное значение не покидает сервер."""
    if not key:
        return ""
    if len(key) <= 8:
        return "•" * len(key)
    return "•" * 6 + key[-4:]


def used_today(db: Session, key_id: int) -> int:
    row = (
        db.query(QuotaUsage)
        .filter(QuotaUsage.api_key_id == key_id, QuotaUsage.day == quota_day())
        .one_or_none()
    )
    return int(row.units or 0) if row else 0


def spend(db: Session, key: ApiKey, call: str, times: int = 1) -> int:
    """Записать расход на вызов `call`. Возвращает списанные единицы.

    Пишем расход до фактического запроса: если сеть оборвётся посередине,
    Google всё равно уже мог списать квоту, и завышенный счётчик безопаснее
    заниженного — иначе пользователь запустит поиск, который упадёт на 403.
    """
    units = COSTS.get(call, 1) * max(1, times)
    day = quota_day()
    row = (
        db.query(QuotaUsage)
        .filter(QuotaUsage.api_key_id == key.id, QuotaUsage.day == day)
        .one_or_none()
    )
    if row is None:
        row = QuotaUsage(api_key_id=key.id, day=day, units=0, calls=0)
        db.add(row)
        db.flush()
    row.units = int(row.units or 0) + units
    row.calls = int(row.calls or 0) + max(1, times)
    db.flush()

    # Предупреждение о заканчивающейся квоте ставим сразу после списания:
    # расход меняется только здесь, и переход через порог видно точно в момент
    # перехода. Отдельный обход по расписанию сообщал бы об этом с опозданием.
    from .services.notices import notify_quota

    notify_quota(db, key.owner_id, row, int(key.daily_quota or DEFAULT_DAILY_QUOTA))
    return units


def summary(db: Session, owner_id: int) -> dict:
    """Сводка по квоте пользователя.

    total_daily суммируется по различным проектам Cloud, а не по числу ключей:
    два ключа одного проекта делят одну квоту, и показать 20 000 было бы ложью.
    """
    keys = db.query(ApiKey).filter(ApiKey.owner_id == owner_id).all()
    enabled = [k for k in keys if k.enabled]

    seen_projects: set[str] = set()
    total = 0
    for k in enabled:
        label = (k.project_label or "").strip().lower()
        marker = label or f"__key_{k.id}"  # без метки — считаем отдельным проектом
        if marker in seen_projects:
            continue
        seen_projects.add(marker)
        total += int(k.daily_quota or DEFAULT_DAILY_QUOTA)

    day = quota_day()
    used = 0
    if enabled:
        rows = (
            db.query(QuotaUsage)
            .filter(
                QuotaUsage.api_key_id.in_([k.id for k in enabled]),
                QuotaUsage.day == day,
            )
            .all()
        )
        used = sum(int(r.units or 0) for r in rows)

    return {
        "total_daily": total,
        "used_today": used,
        "left_today": max(0, total - used),
        "keys_total": len(keys),
        "keys_enabled": len(enabled),
        "projects": len(seen_projects),
        "resets_at": next_reset(),
        "measured_locally": True,
        "source_note": SOURCE_NOTE,
    }


def _flagged_exhausted_today(key: ApiKey) -> bool:
    """True, если ключ помечен quota_exceeded именно в эти квотные сутки.

    Статус хранится в БД и сам не сбрасывается. Но квота Google обнуляется в
    полночь по тихоокеанскому времени, поэтому вчерашний quota_exceeded сегодня
    уже не приговор — такой ключ снова стоит пробовать. Пропускаем ключ, только
    если ошибку об исчерпании мы видели в текущих квотных сутках.
    """
    if key.status != "quota_exceeded" or not key.last_checked_at:
        return False
    checked = key.last_checked_at
    if checked.tzinfo is None:
        checked = checked.replace(tzinfo=UTC)
    return (checked + _pacific_offset(checked)).date() == quota_day()


def pick_key(db: Session, owner_id: int, need_units: int = 1) -> ApiKey | None:
    """Ключ с наибольшим остатком, которого хватает на `need_units`.

    Пропускаем:
    - invalid — ключ уже отвечал ошибкой доступа, тратить время бессмысленно;
    - quota_exceeded, помеченный сегодня — Google сказал, что квота выбрана.

    Из оставшихся берём ключ с самым большим остатком, а не первый по id: так
    нагрузка распределяется между проектами и один ключ не выжигается первым.
    """
    keys = (
        db.query(ApiKey)
        .filter(ApiKey.owner_id == owner_id, ApiKey.enabled.is_(True))
        .order_by(ApiKey.id)
        .all()
    )
    best: ApiKey | None = None
    best_left = -1
    for k in keys:
        if k.status == "invalid" or _flagged_exhausted_today(k):
            continue
        left = int(k.daily_quota or DEFAULT_DAILY_QUOTA) - used_today(db, k.id)
        if left >= need_units and left > best_left:
            best, best_left = k, left
    return best
