"""Immutable tariff catalog and framework-free public quote helpers."""

from __future__ import annotations

from collections.abc import Callable

# Порядок важен: так планы идут в тарифах слева направо.
# Показываются только покупаемые планы; служебный NO_PLAN сюда не входит.
PLAN_ORDER = ["zephyr"]
LEGACY_PLAN_ALIASES = {"pro": "zephyr", "max": "zephyr", "ultra": "zephyr"}

# Пробный доступ. Не покупается и не входит в PLAN_ORDER (в сетке тарифов он не
# карточка, а объяснение под кнопкой входа), но это полноценный план: у него
# есть срок и квоты, и он выдаётся автоматически при первом входе.
TRIAL_PLAN = "trial"
TRIAL_DAYS = 14

# Служебный «нет тарифа»: сюда попадают те, кому админ ещё не активировал план,
# и те, у кого оплаченный срок истёк. Не покупается и не показывается в сетке
# тарифов — нужен, чтобы лимиты и проверки не падали на отсутствующем плане.
NO_PLAN = "none"

# monthly_leads — сколько лидов (найденных каналов) можно собрать за календарный
# месяц. Остальные лимиты — на общее число объектов у пользователя.
PLANS: dict[str, dict] = {
    NO_PLAN: {
        "slug": NO_PLAN,
        "sending": False,
        "name": "Без тарифа",
        "price_kzt": 0,
        "price_rub": 0,
        "tagline": "Тариф не активирован",
        "monthly_leads": 0,
        "monthly_messages": 0,
        "monthly_ai_offers": 0,
        "max_per_launch": 0,
        "max_databases": 0,
        "max_offers": 0,
        "max_campaigns": 0,
        "max_warmup": 0,
        "ai": False,
        "ai_offer": False,
        "features": [],
    },
    "pro": {
        "slug": "pro",
        "sending": True,
        "name": "Про",
        "price_kzt": 14_900,
        "price_rub": 2_900,
        "tagline": "Для стабильного потока",
        "monthly_leads": 10_000,
        "monthly_messages": 2_000,
        "monthly_ai_offers": 0,
        "max_per_launch": 50,
        "max_databases": 50,
        "max_offers": 50,
        "max_campaigns": 100,
        "max_warmup": 5,
        "ai": True,
        "ai_offer": False,
        "features": [
            "До 10 000 лидов в месяц",
            "До 2 000 отправок в месяц, 50 за запуск",
            "До 50 баз и 50 офферов",
            "5 аккаунтов для прогрева",
            "ИИ-помощник по вашим данным",
            "Обычные офферы со спинтаксом",
            "AI-офферы — только в Макс и Ультра",
        ],
    },
    "max": {
        "slug": "max",
        "sending": True,
        "name": "Макс",
        "price_kzt": 39_900,
        "price_rub": 7_900,
        "tagline": "Для команд и агентств",
        "monthly_leads": 50_000,
        "monthly_messages": 10_000,
        "monthly_ai_offers": 2_000,
        "max_per_launch": 150,
        "max_databases": 200,
        "max_offers": 200,
        "max_campaigns": 500,
        "max_warmup": 20,
        "ai": True,
        "ai_offer": True,
        "features": [
            "До 50 000 лидов в месяц",
            "До 10 000 отправок в месяц, 150 за запуск",
            "До 200 баз и 200 офферов",
            "20 аккаунтов для прогрева",
            "AI-офферы: индивидуальный текст под каждый лид",
            "ИИ-помощник и приоритетная поддержка",
        ],
    },
    "ultra": {
        "slug": "ultra",
        "sending": True,
        "name": "Ультра",
        "price_kzt": 89_900,
        "price_rub": 17_900,
        "tagline": "Максимальный объём",
        "monthly_leads": -1,
        "monthly_messages": 50_000,
        "monthly_ai_offers": 10_000,
        "max_per_launch": 200,
        "max_databases": -1,
        "max_offers": -1,
        "max_campaigns": -1,
        "max_warmup": -1,
        "ai": True,
        "ai_offer": True,
        "features": [
            "Безлимит лидов, баз и офферов; 50 000 отправок",
            "Неограниченный прогрев",
            "До 10 000 индивидуальных AI-офферов в месяц",
            "Персональный менеджер",
        ],
    },
    TRIAL_PLAN: {
        "slug": TRIAL_PLAN,
        "sending": False,
        "name": "Пробный",
        "price_kzt": 0,
        "price_rub": 0,
        "tagline": f"Поиск и проверка лидов, {TRIAL_DAYS} дней бесплатно",
        # Ключ YouTube API у каждого пользователя свой, поэтому бесплатный поиск
        # не тратит нашу квоту — жать лимит лидов незачем.
        "monthly_leads": 1_000,
        # Ноль отправок — это и есть суть пробного доступа, а не жадность:
        # рассылка с чужого аккаунта в чужую личку требует доверия к тому, кто
        # её запускает. Заодно это снимает главный риск бесплатного тарифа —
        # регистрировать пачку аккаунтов ради рассылки становится бессмысленно.
        "monthly_messages": 0,
        "monthly_ai_offers": 0,
        "max_per_launch": 0,
        "max_databases": 3,
        "max_offers": 3,
        "max_campaigns": 0,
        # Один аккаунт нужен, чтобы проверить лидов в Telegram: без него видно
        # только YouTube-часть, и пробный доступ ничего не доказывает.
        "max_warmup": 1,
        "ai": False,
        "ai_offer": False,
        "features": [
            "Поиск каналов по YouTube и сбор контактов",
            "До 1 000 лидов в месяц, 3 базы и 3 оффера",
            "Один аккаунт Telegram для проверки лидов",
            "Отправка сообщений недоступна",
            "ИИ-помощник недоступен",
        ],
    },
    "zephyr": {
        "slug": "zephyr",
        "sending": True,
        "name": "Talaria",
        "price_kzt": 25_000,
        "price_rub": 5_000,
        "tagline": "Все инструменты для персональных Telegram-кампаний",
        # У Talaria нет продуктовых квот. Безопасный темп Telegram (паузы,
        # дневная нагрузка отправителей, FloodWait) — отдельный операционный
        # контур и не превращается в тарифный лимит.
        "monthly_leads": -1,
        "monthly_messages": -1,
        "monthly_ai_offers": -1,
        "max_per_launch": -1,
        "max_databases": -1,
        "max_offers": -1,
        "max_campaigns": -1,
        "max_warmup": -1,
        "ai": True,
        "ai_offer": True,
        "features": [
            "Поиск и проверка лидов по YouTube",
            "Базы лидов, офферы и Telegram-рассылки",
            "Персонализация вступлений по тематике канала",
            "Статистика доставки, прочтений, ответов и интереса",
            "Тепловая карта лучшего времени отправки",
            "Центр безопасности аккаунтов и автопауза",
            "ИИ-помощник по данным рабочего пространства",
        ],
    },
}

DEFAULT_PLAN = NO_PLAN

# Скидка на первую покупку. Оплата ручная (админ активирует тариф после оплаты
# в личку), поэтому скидка — это оффер: цену со скидкой видит только тот, кто
# ещё ни разу не покупал (см. is_first_purchase). Так на сайте не появляется
# ложная «скидка» для того, кто уже платит. 0 — акция выключена.
FIRST_PURCHASE_DISCOUNT_PCT = 0
QUARTER_DISCOUNT_PCT = 15
PROMO_CODE = "ZEPHYR30"
PROMO_DISCOUNT_PCT = 30


def first_purchase_price(full_price: int) -> int:
    """Цена со скидкой на первую покупку, округлённая до целого.

    Округляем вниз до десятков — так цена выглядит как цена (14 900 → 8 940),
    а не как результат деления. 0 остаётся 0.
    """
    if full_price <= 0 or FIRST_PURCHASE_DISCOUNT_PCT <= 0:
        return full_price
    discounted = full_price * (100 - FIRST_PURCHASE_DISCOUNT_PCT) / 100
    return int(discounted // 10 * 10)


def get_plan(slug: str | None) -> dict:
    """Описание плана по slug; неизвестный slug — «без тарифа»."""
    normalized = (slug or "").strip().lower()
    normalized = LEGACY_PLAN_ALIASES.get(normalized, normalized)
    return PLANS.get(normalized, PLANS[DEFAULT_PLAN])


def _legacy_promo(code: str) -> tuple[str, int]:
    """Запасной поиск промокода — тот единственный код, что был зашит в коде.

    Используется там, где базы нет (чистые тесты расчёта цены). В приложении
    коды ищутся в таблице promo_codes, см. services.promo.
    """
    return (PROMO_CODE, PROMO_DISCOUNT_PCT) if (code or "").strip().upper() == PROMO_CODE else ("", 0)


def quote_plan(
    months: int,
    promo_code: str = "",
    currency: str = "RUB",
    *,
    promo_lookup: Callable[[str], tuple[str, int]] | None = None,
) -> dict:
    """Серверный расчёт ручной оплаты; фронт только анимирует готовые цифры.

    `promo_lookup` отвечает на вопрос «что это за код»: так расчёт остаётся
    чистой функцией без обращений к базе, а источник кодов подменяем снаружи.
    """
    months = 3 if int(months or 1) == 3 else 1
    normalized_currency = "KZT" if str(currency or "").upper() == "KZT" else "RUB"
    unit = int(PLANS["zephyr"][f"price_{normalized_currency.lower()}"])
    base = unit * months
    bundle_discount = round(base * QUARTER_DISCOUNT_PCT / 100) if months == 3 else 0
    matched_code, matched_pct = (promo_lookup or _legacy_promo)(promo_code or "")
    promo_valid = bool(matched_code and matched_pct > 0)
    promo_discount = round(unit * matched_pct / 100) if promo_valid else 0
    return {
        "plan_slug": "zephyr",
        "plan_name": "Talaria",
        "period_months": months,
        "currency": normalized_currency,
        "base_total": base,
        "bundle_discount": bundle_discount,
        "promo_discount": promo_discount,
        "total_amount": max(0, base - bundle_discount - promo_discount),
        # Совместимость: поля ниже раньше были единственным контрактом quote.
        # Для KZT они остаются нулевыми, чтобы старый клиент не показал тенге
        # со знаком рубля во время плавного обновления вкладки.
        "base_total_rub": base if normalized_currency == "RUB" else 0,
        "bundle_discount_rub": bundle_discount if normalized_currency == "RUB" else 0,
        "promo_discount_rub": promo_discount if normalized_currency == "RUB" else 0,
        "total_rub": max(0, base - bundle_discount - promo_discount)
        if normalized_currency == "RUB"
        else 0,
        "promo_code": matched_code if promo_valid else "",
        "promo_valid": promo_valid,
        "promo_message": (
            f"Промокод применён: −{matched_pct}% от первого месяца"
            if promo_valid
            else ("Промокод не найден" if (promo_code or "").strip() else "")
        ),
    }


def public_plan(slug: str) -> dict:
    """То, что можно показать на публичной странице тарифов (без внутренних полей).

    Цену со скидкой на первую покупку считаем здесь же и отдаём отдельными
    полями — фронт не пересчитывает, чтобы цифры не разошлись.
    """
    p = get_plan(slug)
    return {
        "slug": p["slug"],
        "name": p["name"],
        "price_kzt": p["price_kzt"],
        "price_rub": p["price_rub"],
        "price_kzt_first": first_purchase_price(p["price_kzt"]),
        "price_rub_first": first_purchase_price(p["price_rub"]),
        "tagline": p["tagline"],
        "monthly_leads": p["monthly_leads"],
        "monthly_messages": p["monthly_messages"],
        "monthly_ai_offers": p["monthly_ai_offers"],
        "max_per_launch": p["max_per_launch"],
        "max_databases": p["max_databases"],
        "max_offers": p["max_offers"],
        "max_warmup": p["max_warmup"],
        "ai": p["ai"],
        "ai_offer": p["ai_offer"],
        "sending": bool(p.get("sending")),
        "features": list(p["features"]),
    }


def all_public_plans() -> list[dict]:
    return [public_plan(slug) for slug in PLAN_ORDER]


__all__ = [
    "PLAN_ORDER",
    "TRIAL_PLAN",
    "TRIAL_DAYS",
    "LEGACY_PLAN_ALIASES",
    "NO_PLAN",
    "PLANS",
    "DEFAULT_PLAN",
    "FIRST_PURCHASE_DISCOUNT_PCT",
    "QUARTER_DISCOUNT_PCT",
    "PROMO_CODE",
    "PROMO_DISCOUNT_PCT",
    "first_purchase_price",
    "get_plan",
    "quote_plan",
    "public_plan",
    "all_public_plans",
]
