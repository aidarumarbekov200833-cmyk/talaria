"""Учёт полученных денег и сводка выручки.

Оплата в Talaria ручная: человек переводит деньги, админ активирует тариф.
Значит, единственный источник правды о выручке — то, что админ отметил руками.
Сервис не выдумывает суммы из тарифа: у одного и того же плана бывает скидка,
частичная оплата и оплата за друга, и подставленная «цена по прайсу» превратила
бы отчёт в ложь, которую невозможно опровергнуть.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import func
from sqlalchemy.orm import Session

from ..models import Payment, User
from . import promo as promo_service
from .errors import DomainError

# Валюты, в которых сервис принимает оплату. Список закрыт: свободное поле
# рано или поздно даст «руб», «RUB» и «рубли» тремя разными строками, и
# сложить их в одну сумму будет уже нельзя.
CURRENCIES = ("RUB", "KZT", "USD", "EUR")

# Потолок на одну запись. Не ограничение бизнеса, а защита от опечатки:
# лишний ноль в сумме искажает всю статистику, и заметить это потом трудно.
MAX_AMOUNT_MINOR = 100_000_000  # миллион в основной валюте


def add_payment(
    db: Session,
    owner_id: int,
    *,
    amount_minor: int,
    currency: str,
    admin_id: int,
    plan: str = "",
    months: int = 1,
    promo_code: str = "",
    note: str = "",
    paid_at: datetime | None = None,
) -> Payment:
    """Записать полученный платёж."""
    if db.get(User, owner_id) is None:
        raise DomainError(404, "Пользователь не найден")

    amount = int(amount_minor or 0)
    # Отрицательная сумма разрешена: возврат — тоже движение денег, и прятать
    # его значит завышать выручку. А ноль записывать незачем.
    if amount == 0:
        raise DomainError(400, "Сумма платежа не может быть нулевой")
    if abs(amount) > MAX_AMOUNT_MINOR:
        raise DomainError(400, "Сумма выглядит как опечатка — проверьте количество нулей")

    code = (currency or "RUB").strip().upper()
    if code not in CURRENCIES:
        raise DomainError(400, f"Валюта {code} не поддерживается")

    moment = paid_at or datetime.now(UTC)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    # Платёж «из будущего» — почти всегда опечатка в дате. Сутки запаса
    # оставляем на разницу часовых поясов.
    if moment > datetime.now(UTC) + timedelta(days=1):
        raise DomainError(400, "Дата платежа в будущем — проверьте её")

    payment = Payment(
        owner_id=owner_id,
        amount_minor=amount,
        currency=code,
        plan=(plan or "")[:20],
        months=max(0, int(months or 0)),
        promo_code=(promo_code or "").strip().upper()[:40],
        note=(note or "").strip()[:2000],
        created_by=admin_id,
        paid_at=moment,
    )
    db.add(payment)
    db.commit()
    db.refresh(payment)
    # Настоящее применение промокода — оплаченные деньги, а не показ формы с
    # введённым кодом (см. promo.mark_used). До этого вызова счётчик применений
    # не рос нигде, и max_uses не работал вовсе. Возвраты (amount < 0) кодом
    # не считаются.
    if amount > 0 and payment.promo_code:
        promo_service.mark_used(db, payment.promo_code)
    return payment


def delete_payment(db: Session, payment_id: int) -> None:
    """Удалить ошибочно заведённый платёж."""
    payment = db.get(Payment, payment_id)
    if payment is None:
        raise DomainError(404, "Платёж не найден")
    db.delete(payment)
    db.commit()


def revenue_summary(db: Session, *, now: datetime | None = None) -> dict:
    """Выручка по валютам: за текущий месяц, за 30 дней и за всё время.

    По валютам раздельно и без пересчёта в одну. Курс на дату платежа мы не
    храним, а пересчитать по сегодняшнему — значит каждый день показывать
    другую цифру за один и тот же прошлый месяц.
    """
    moment = now or datetime.now(UTC)
    month_start = moment.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    days30 = moment - timedelta(days=30)

    def totals(since: datetime | None) -> dict[str, int]:
        query = db.query(Payment.currency, func.sum(Payment.amount_minor))
        if since is not None:
            query = query.filter(Payment.paid_at >= since)
        return {row[0]: int(row[1] or 0) for row in query.group_by(Payment.currency).all()}

    paying = (
        db.query(func.count(func.distinct(Payment.owner_id)))
        .filter(Payment.paid_at >= month_start)
        .scalar()
    )
    return {
        "month": totals(month_start),
        "last_30_days": totals(days30),
        "all_time": totals(None),
        "payments_total": int(db.query(func.count(Payment.id)).scalar() or 0),
        "paying_users_this_month": int(paying or 0),
    }
