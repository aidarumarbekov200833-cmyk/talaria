"""Настройки пользователя и журнал событий.

Обе вещи нужны почти каждому роутеру, поэтому вынесены сюда, а не в отдельный
роутер: журнал заполняется на сервере при реальных действиях, а не браузером
«для вида».
"""

from __future__ import annotations

import math

from sqlalchemy.orm import Session

from ..ai_prompt import DEFAULT_SYSTEM_PROMPT, build_system_prompt  # noqa: F401

# DEFAULT_SYSTEM_PROMPT и build_system_prompt живут в ai_prompt.py — там же
# лежит карта интерфейса, которую промпт описывает. Реэкспорт оставлен, чтобы
# не переписывать импорты в роутерах.

WARMUP_DISCLAIMER = (
    "Параметры прогрева назначаются автоматически после включения. "
    "Сессии подключённых аккаунтов изолированы в Telegram gateway; код входа и "
    "пароль не сохраняются."
)



def resolve_ai_provider(db: Session) -> tuple[str, str, str, str, bool]:
    """Действующие параметры провайдера: (base_url, api_key, model, prompt, tools).

    Конфигурация только из окружения: ключ и endpoint не отображаются и не
    редактируются через сайт. Системный промпт встроен в релиз и не может
    незаметно устареть в строке БД.
    """
    from ..config import settings as app_config

    _ = db  # единая сигнатура сохранена для существующих вызывающих мест
    return (
        app_config.ai_base_url.strip(),
        app_config.ai_api_key.strip(),
        app_config.ai_model.strip(),
        "",
        True,
    )


def ai_provider_ready(db: Session) -> bool:
    base_url, api_key, model, _prompt, _tools = resolve_ai_provider(db)
    return bool(base_url and api_key and model)




# --------------------------------------------------------------------------
# Скоринг лидов
# --------------------------------------------------------------------------
#
# Зачем. В базе на тысячу каналов нет способа понять, кому писать первым.
# Сортировка по подписчикам обманывает: миллионник с мёртвыми просмотрами
# хуже живого канала на 50 000.
#
# Честное предупреждение о точности. Это эвристика, а не модель. Она считается
# только из четырёх полей, которые реально отдаёт YouTube Data API. Веса подобраны
# руками и ни на чём не обучены — оценка годится чтобы упорядочить список, а не
# чтобы считать её вероятностью ответа. Поэтому веса собраны в константы ниже:
# их можно менять в одном месте, когда накопится статистика ответов.

# Вовлечённость весит больше размера намеренно. При обратном соотношении
# миллионник с мёртвыми просмотрами всё равно обгонял живой микроканал
# только за счёт числа подписчиков — ровно тот случай, ради которого оценка
# и заводилась.
SCORE_WEIGHT_AUDIENCE = 30  # размер аудитории
SCORE_WEIGHT_ENGAGEMENT = 40  # средние просмотры на ролик
SCORE_WEIGHT_ACTIVITY = 15  # есть ли вообще контент
SCORE_WEIGHT_RELEVANCE = 15  # попал ли канал по ключевому слову

# Границы логарифмической шкалы. Шкала именно логарифмическая, потому что
# разница между 1 000 и 10 000 подписчиков значит для решения больше, чем между
# 900 000 и 1 000 000, хотя в абсолютных числах второй разрыв в 10 раз больше.
SCORE_SUBS_FLOOR = 100
SCORE_SUBS_CEIL = 1_000_000
SCORE_VPV_FLOOR = 100  # просмотров на ролик
SCORE_VPV_CEIL = 100_000


def _log_scale(value: float, floor: float, ceil: float) -> float:
    """Перевести величину в долю 0..1 по логарифмической шкале."""
    if value <= floor:
        return 0.0
    if value >= ceil:
        return 1.0
    return (math.log10(value) - math.log10(floor)) / (math.log10(ceil) - math.log10(floor))


def compute_lead_score(
    subscribers: int | None,
    video_count: int | None,
    view_count: int | None,
    matched_keyword: str | None,
) -> int:
    """Оценка канала от 0 до 100.

    Функция берёт отдельные значения, а не объект Lead, чтобы её можно было
    вызывать ещё до создания строки — прямо на сыром ответе YouTube в поиске
    и на строках CSV при импорте.
    """
    # Аудитория. subscribers = None означает не «ноль подписчиков», а  «канал
    # скрыл счётчик». Обнулять такой канал нельзя — скрывают счётчик часто как
    # раз крупные. Даём середину, чтобы такой лид не улетел в конец списка.
    if subscribers is None:
        audience = 0.5
    else:
        audience = _log_scale(subscribers, SCORE_SUBS_FLOOR, SCORE_SUBS_CEIL)

    # Вовлечённость через средние просмотры на ролик. Это главный отсев
    # накрученных каналов: подписчиков купить легко, стабильные просмотры — нет.
    if view_count and video_count:
        engagement = _log_scale(view_count / max(1, video_count), SCORE_VPV_FLOOR, SCORE_VPV_CEIL)
    else:
        engagement = 0.0

    # Активность. YouTube в этом ответе не говорит, когда был последний ролик
    # (published_at — это дата создания канала, а не последней публикации), поэтому
    # выдавать возраст за свежесть было бы враньём. Честно судим только по тому,
    # есть ли на канале заметное число роликов: пустой канал — плохой лид.
    videos = video_count or 0
    if videos >= 50:
        activity = 1.0
    elif videos >= 10:
        activity = 0.7
    elif videos >= 3:
        activity = 0.4
    elif videos >= 1:
        activity = 0.2
    else:
        activity = 0.0

    relevance = 1.0 if (matched_keyword or "").strip() else 0.0

    total = (
        audience * SCORE_WEIGHT_AUDIENCE
        + engagement * SCORE_WEIGHT_ENGAGEMENT
        + activity * SCORE_WEIGHT_ACTIVITY
        + relevance * SCORE_WEIGHT_RELEVANCE
    )
    return max(0, min(100, round(total)))


def explain_lead_score(
    subscribers: int | None,
    video_count: int | None,
    view_count: int | None,
    matched_keyword: str | None,
) -> list[str]:
    """Коротко объяснить балл теми же факторами, которыми он рассчитан."""
    reasons: list[str] = []
    if subscribers is None:
        reasons.append("Подписчики скрыты — аудитория оценена нейтрально")
    elif subscribers >= 100_000:
        reasons.append(f"Крупная аудитория: {subscribers:,}".replace(",", " "))
    elif subscribers >= 10_000:
        reasons.append(f"Устойчивая аудитория: {subscribers:,}".replace(",", " "))
    elif subscribers >= 1_000:
        reasons.append(f"Небольшая аудитория: {subscribers:,}".replace(",", " "))
    else:
        reasons.append("Мало подписчиков")

    videos = int(video_count or 0)
    views = int(view_count or 0)
    if videos and views:
        average = round(views / videos)
        if average >= 10_000:
            reasons.append(f"Сильные средние просмотры: {average:,} на видео".replace(",", " "))
        elif average >= 1_000:
            reasons.append(f"Средние просмотры: {average:,} на видео".replace(",", " "))
        else:
            reasons.append(f"Низкие средние просмотры: {average:,} на видео".replace(",", " "))
    else:
        reasons.append("Недостаточно данных о просмотрах")

    if videos >= 50:
        reasons.append("Большая библиотека контента")
    elif videos >= 10:
        reasons.append("Канал регулярно наполнялся")
    elif videos >= 3:
        reasons.append("Контента пока немного")
    else:
        reasons.append("Канал почти пустой")

    keyword = (matched_keyword or "").strip()
    if keyword:
        reasons.append(f"Найден по запросу «{keyword[:80]}»")
    return reasons

