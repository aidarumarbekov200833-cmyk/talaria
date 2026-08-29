"""Общая настройка тестов.

Тесты не требуют Postgres: поднимаем SQLite в памяти и подменяем зависимость
get_db. Переменные окружения выставляем ДО импорта приложения — pydantic-settings
читает их в момент создания `settings` (на импорте app.config), поэтому порядок
важен: сначала env, потом любые `import app...`.

Запуск (в окружении с установленными зависимостями, см. requirements-dev.txt):

    cd backend
    pytest
"""

from __future__ import annotations

import os

# --- окружение до импорта приложения ---------------------------------------
# SQLite в памяти вместо Postgres; StaticPool ниже держит одно соединение, чтобы
# база жила между сессиями и потоками TestClient.
os.environ.setdefault("DATABASE_URL", "sqlite+pysqlite:///:memory:")
# Не демо-режим: заданы BOT_TOKEN и BOT_USERNAME (нужно для /api/config и проверки
# подписи Telegram). Значения фиксированные — подпись в тестах считаем от них.
os.environ.setdefault("BOT_TOKEN", "test-bot-token")
os.environ.setdefault("BOT_USERNAME", "test_bot")
os.environ.setdefault("JWT_SECRET", "test-secret")
os.environ.setdefault("ADMIN_USERNAME", "admin_user")
# Админ по username, а не по telegram_id: в проде админ определяется по ID
# (ADMIN_TELEGRAM_IDS), и сверка по имени включается только этим флагом.
os.environ.setdefault("ADMIN_USERNAME_FALLBACK", "1")
# Лимитер выключаем, иначе повторные вызовы auth в тестах ловят 429.
os.environ.setdefault("RATE_LIMIT_ENABLED", "false")
# Паузы между сообщениями рассылки в тестах не нужны — иначе каждый запуск
# ждал бы секунды на ровном месте.
os.environ.setdefault("SEND_DELAY_MIN_SECONDS", "0")
os.environ.setdefault("SEND_DELAY_MAX_SECONDS", "0")

from datetime import UTC

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import models  # noqa: F401  (регистрация моделей на метаданных)
from app.database import Base, get_db
from app.models import PromoCode
from app.main import app
from app.security import create_jwt
from app.telegram_gateway import gateway

# Один общий движок SQLite в памяти на весь прогон. StaticPool + одно соединение +
# check_same_thread=False — база видна и в запросе (другой поток TestClient), и в
# фикстуре db.
engine = create_engine(
    "sqlite+pysqlite:///:memory:",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


def _override_get_db():  # noqa: ANN202 — генератор-зависимость FastAPI
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()


app.dependency_overrides[get_db] = _override_get_db


@pytest.fixture(autouse=True)
def _schema():  # noqa: ANN202 — pytest-фикстура
    """Свежая схема на каждый тест: создаём все таблицы, после — сносим.

    Промокод ZEPHYR30 засеиваем здесь же: в бою его создаёт миграция 0025, а
    тесты поднимают схему из моделей и миграции не проходят. Без него расчёт
    цены в тестах отличался бы от боевого.
    """
    Base.metadata.create_all(bind=engine)
    session = TestingSessionLocal()
    try:
        session.add(
            PromoCode(code="ZEPHYR30", discount_pct=30, active=True, max_uses=0, used_count=0)
        )
        session.commit()
    finally:
        session.close()
    yield
    Base.metadata.drop_all(bind=engine)


@pytest.fixture(autouse=True)
def _telegram_gateway_session_stub(monkeypatch):  # noqa: ANN202
    """API-тесты не ходят в сеть, но считают подготовленные слоты авторизованными."""
    monkeypatch.setattr(gateway, "session_status", lambda *_args: {"authorized": True})
    monkeypatch.setattr(
        gateway,
        "account_safety",
        lambda *_args: {"authorized": True, "unread_count": 0, "unread_dialogs": 0},
    )
    monkeypatch.setattr(gateway, "drop_session", lambda *_args: None)


@pytest.fixture
def db():
    """Сессия к тестовой БД для прямой подготовки данных."""
    session = TestingSessionLocal()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def client():
    """HTTP-клиент к приложению. get_db уже подменён на тестовую БД."""
    return TestClient(app)


@pytest.fixture
def user(db):
    """Обычный пользователь в тестовой БД — сразу в белом списке.

    Вход в приложение закрыт по умолчанию (app/access.py): не внесённый в белый
    список получает 403 на любом запросе под токеном. Тесты про API не должны
    проверять белый список — для него есть свои тесты, — поэтому фикстура
    создаёт разрешённого пользователя.
    """
    u = models.User(telegram_id=1001, username="tester", first_name="Test")
    db.add(u)
    db.add(models.AccessWhitelist(username="tester", note="фикстура тестов"))
    db.commit()
    db.refresh(u)
    return u


@pytest.fixture
def paid_user(db, user):
    """Пользователь с активным единым тарифом Talaria.

    Без тарифа все лимиты объектов нулевые (плана «бесплатно» в продукте нет),
    и создание оффера/базы честно отвечает 402. Тестам про CRUD нужен тариф.
    """
    from datetime import datetime, timedelta

    db.add(
        models.Subscription(
            owner_id=user.id,
            plan="zephyr",
            status="active",
            paid_until=datetime.now(UTC) + timedelta(days=30),
        )
    )
    db.commit()
    return user


@pytest.fixture
def auth_headers(user):
    """Заголовок Authorization с валидным JWT для `user`."""
    token, _jti = create_jwt(user.id)
    return {"Authorization": f"Bearer {token}"}
