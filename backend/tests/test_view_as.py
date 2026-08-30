"""Режим «смотреть глазами пользователя».

Функция по своей природе опасная: администратор получает доступ к чужому
кабинету. Поэтому здесь проверяется не «страница открылась», а границы —
что изменить ничего нельзя, что доступ короткий, что о нём знает владелец
кабинета, и что из просмотра нельзя вернуться в админку.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import jwt
import pytest
from fastapi import Depends

from app import models
from app.config import settings
from app.dependencies import get_current_user
from app.security import create_jwt, create_view_token

ADMIN_TG_ID = 555001


@pytest.fixture
def admin(db, monkeypatch):
    """Пользователь, которого конфигурация считает администратором.

    Права определяются по telegram_id из настроек, поэтому список подменяем —
    иначе пришлось бы полагаться на реальный ADMIN_TELEGRAM_IDS окружения.
    """
    monkeypatch.setattr(settings, "admin_telegram_ids", str(ADMIN_TG_ID))
    row = models.User(telegram_id=ADMIN_TG_ID, username="boss")
    db.add(row)
    db.add(models.AccessWhitelist(username="boss", note="фикстура"))
    db.commit()
    db.refresh(row)
    return row


@pytest.fixture
def admin_headers(admin):
    token, _jti = create_jwt(admin.id)
    return {"Authorization": f"Bearer {token}"}


def _view_headers(admin_id: int, target_id: int, minutes: int = 30) -> dict[str, str]:
    token, _jti = create_view_token(admin_id, target_id, ttl_minutes=minutes)
    return {"Authorization": f"Bearer {token}"}


# --- Выдача ----------------------------------------------------------------


def test_админ_получает_короткий_токен(client, db, admin, admin_headers, user):
    response = client.post(f"/api/admin/users/{user.id}/view-as", headers=admin_headers)
    assert response.status_code == 200
    body = response.json()
    assert body["expires_in"] <= 3600
    claims = jwt.decode(body["access_token"], settings.jwt_secret, algorithms=["HS256"])
    assert claims["sub"] == str(user.id)
    assert claims["act"] == str(admin.id)
    assert claims["ro"] is True


def test_обычный_пользователь_не_может_смотреть_чужой_кабинет(client, db, user, auth_headers):
    other = models.User(telegram_id=7007, username="victim")
    db.add(other)
    db.commit()
    db.refresh(other)
    response = client.post(f"/api/admin/users/{other.id}/view-as", headers=auth_headers)
    assert response.status_code == 403


def test_просмотр_виден_владельцу_кабинета(client, db, admin, admin_headers, user):
    """Тихий просмотр чужих данных — слежка, а не поддержка."""
    client.post(f"/api/admin/users/{user.id}/view-as", headers=admin_headers)
    owner_events = (
        db.query(models.EventLog)
        .filter(models.EventLog.owner_id == user.id, models.EventLog.scope == "security")
        .all()
    )
    assert owner_events
    assert any("просмотр" in row.message.lower() for row in owner_events)


def test_просмотр_записан_и_админу(client, db, admin, admin_headers, user):
    client.post(f"/api/admin/users/{user.id}/view-as", headers=admin_headers)
    admin_events = (
        db.query(models.EventLog).filter(models.EventLog.owner_id == admin.id).all()
    )
    assert any(str(user.id) in row.message for row in admin_events)


def test_свой_кабинет_смотреть_незачем(client, admin, admin_headers):
    response = client.post(f"/api/admin/users/{admin.id}/view-as", headers=admin_headers)
    assert response.status_code == 400


# --- Границы режима --------------------------------------------------------


def test_чтение_работает(client, db, admin, user):
    response = client.get("/api/me", headers=_view_headers(admin.id, user.id))
    assert response.status_code == 200
    body = response.json()
    assert body["view_only"] is True
    assert body["name"] == user.display_name


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("post", "/api/auth/logout"),
        ("post", "/api/offers"),
        ("post", "/api/databases"),
        ("patch", "/api/settings"),
        ("delete", "/api/offers/1"),
        ("put", "/api/leads/1/note"),
    ],
)
def test_любое_изменение_запрещено(client, admin, user, method, path):
    """Запрет стоит в middleware, поэтому распространяется и на новые ручки."""
    headers = _view_headers(admin.id, user.id)
    # У TestClient.delete нет параметра json — тело для удаления и не нужно.
    call = getattr(client, method)
    response = call(path, headers=headers) if method == "delete" else call(
        path, headers=headers, json={}
    )
    assert response.status_code == 403
    assert "просмотр" in response.json()["detail"].lower()


def test_запрет_срабатывает_даже_на_несуществующем_пути(client, admin, user):
    """Иначе про запрет узнавали бы только те ручки, что уже написаны."""
    headers = _view_headers(admin.id, user.id)
    response = client.post("/api/такой-ручки-нет", headers=headers, json={})
    assert response.status_code == 403


def test_админка_из_режима_просмотра_закрыта(client, db, admin):
    """Даже если смотреть кабинет самого администратора."""
    second_admin = models.User(telegram_id=ADMIN_TG_ID + 1, username="boss2")
    db.add(second_admin)
    db.add(models.AccessWhitelist(username="boss2", note="фикстура"))
    db.commit()
    db.refresh(second_admin)
    headers = _view_headers(admin.id, second_admin.id)
    assert client.get("/api/admin/overview", headers=headers).status_code == 403


def test_в_режиме_просмотра_нет_прав_администратора(client, db, admin):
    other_admin = models.User(telegram_id=ADMIN_TG_ID + 2, username="boss3")
    db.add(other_admin)
    db.add(models.AccessWhitelist(username="boss3", note="фикстура"))
    db.commit()
    db.refresh(other_admin)
    body = client.get("/api/me", headers=_view_headers(admin.id, other_admin.id)).json()
    # Кнопок админки в интерфейсе быть не должно: они всё равно упрутся в запрет.
    assert body["is_admin"] is False


def test_просроченный_доступ_не_работает(client, admin, user):
    expired = jwt.encode(
        {
            "sub": str(user.id),
            "act": str(admin.id),
            "ro": True,
            "jti": "expired-view",
            "iat": datetime.now(UTC) - timedelta(hours=2),
            "exp": datetime.now(UTC) - timedelta(hours=1),
        },
        settings.jwt_secret,
        algorithm="HS256",
    )
    response = client.get("/api/me", headers={"Authorization": f"Bearer {expired}"})
    assert response.status_code == 401


def test_просмотр_не_оживляет_список_входов(client, db, admin, user):
    """У режима просмотра нет своего сеанса, и он не должен появляться в списке."""
    client.get("/api/me", headers=_view_headers(admin.id, user.id))
    assert db.query(models.UserSession).filter_by(owner_id=user.id).count() == 0


def test_обычный_токен_менять_данные_может(client, db, user, auth_headers):
    """Страховка от чрезмерного запрета: middleware не должен ломать обычную работу."""
    response = client.post("/api/auth/logout", headers=auth_headers)
    assert response.status_code == 200


# --- Кто выполняет запрос ---------------------------------------------------


def test_в_логи_попадает_кто_выполнял_запрос(client, user, auth_headers):
    """Раньше user_id в логах всегда был пустым.

    Синхронные зависимости и обработчики FastAPI выполняются в разных потоках
    пула, каждый со своей копией контекста, поэтому присваивание contextvar
    внутри зависимости до обработчика не доезжало — и разбор инцидента упирался
    в строки без автора.
    """
    from app.logging_setup import current_user_id

    seen: list[int | None] = []

    from app.main import app as fastapi_app

    @fastapi_app.get("/api/__probe_user")
    def _probe(_u=Depends(get_current_user)):  # noqa: ANN202
        seen.append(current_user_id())
        return {"ok": True}

    try:
        assert client.get("/api/__probe_user", headers=auth_headers).status_code == 200
        assert seen == [user.id]
    finally:
        fastapi_app.router.routes = [
            route
            for route in fastapi_app.router.routes
            if getattr(route, "path", "") != "/api/__probe_user"
        ]
