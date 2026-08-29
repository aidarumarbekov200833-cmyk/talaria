#!/usr/bin/env python3
"""Seed-данные для локального развертывания.

Создаёт минимальный набор объектов, чтобы интерфейс не выглядел пустым после первого docker compose up.

Запуск:
    docker compose exec backend python scripts/seed.py
Или локально (export DATABASE_URL=...):
    cd backend && python scripts/seed.py

Сценарий идемпотентен: повторный запуск не добавывает дублей и не падает,
если объекты уже существуют.
"""

from __future__ import annotations

import os
import sys

# Даём возможность запустить скрипт любым из двух способов:
#   1) из папки backend/ с префиксом sys.path,
#   2) из папки backend/scripts/ через родительскую.
script_dir = os.path.dirname(os.path.abspath(__file__))
backend_dir = os.path.dirname(script_dir)
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

from sqlalchemy.exc import IntegrityError  # noqa: E402

from app.database import SessionLocal  # noqa: E402
from app.models import (  # noqa: E402
    Offer,
    User,
    WarmupAccount,
)
from app.plans import get_subscription  # noqa: E402
from app.services import log_event  # noqa: E402

SEED_TG_ID = 9_999_999
SEED_USERNAME = "demo_user"


def main() -> None:
    db = SessionLocal()
    try:
        _seed(db)
        print("✓ seed выполнен")
    except Exception as exc:
        db.rollback()
        print(f"✗ seed ошибка: {exc}", file=sys.stderr)
        raise
    finally:
        db.close()


def _seed(db) -> None:  # type: ignore[type-arg]
    # 1. Демо-пользователь (idempotent: ищем по telegram_id).
    user = db.query(User).filter_by(telegram_id=SEED_TG_ID).one_or_none()
    if not user:
        user = User(
            telegram_id=SEED_TG_ID,
            username=SEED_USERNAME,
            first_name="Демо",
            last_name="Пользователь",
        )
        db.add(user)
        db.flush()
        print(f"  → создан пользователь @{SEED_USERNAME} (id={user.id})")
    else:
        print(f"  → пользователь @{SEED_USERNAME} уже есть (id={user.id})")

    # 2. Подписка «Про» для демо-пользователя, чтобы все разделы интерфейса были доступны.
    sub = get_subscription(db, user.id)
    if sub.plan != "pro":
        sub.plan = "pro"
        db.flush()
        print("  → подписка повышена до pro")

    # 3. Офферы.
    sample_offers = [
        (
            "Пример оффера — реклама",
            "Привет! Я нашёл ваш канал — очень крутой контент! Хочу предложить "
            "рекламное сотрудничество — ваша аудитория идеально подходит. Удобно поговорить?",
        ),
        (
            "Пример оффера — интеграция",
            "Добрый день! Мы продвигаем сервисы для YouTube-каналов. У вас отличная ниша — "
            "давайте обсудим детали ?",
        ),
    ]
    for name, text in sample_offers:
        exists = db.query(Offer).filter_by(owner_id=user.id, name=name).one_or_none()
        if not exists:
            db.add(Offer(owner_id=user.id, name=name, text=text))
            print(f"  → оффер «{name}» создан")

    # 4. Аккаунт прогрева.
    warmup_exists = db.query(WarmupAccount).filter_by(owner_id=user.id).first()
    if not warmup_exists:
        db.add(
            WarmupAccount(
                owner_id=user.id,
                label="Демо-аккаунт",
                note="Создан сидом — для проверки раздела прогрева.",
                plan_days=30,
            )
        )
        print("  → аккаунт прогрева создан")

    # 5. Событие в журнале — чтобы админка не была пустой.
    log_event(db, user.id, "settings", "Сейд запущен: данные для демо созданы.", "success")

    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        print(f"  …всё уже существует (IntegrityError): {exc.orig}")


if __name__ == "__main__":
    main()
