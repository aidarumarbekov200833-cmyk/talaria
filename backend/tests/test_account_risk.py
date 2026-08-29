"""Оценка «насколько аккаунт близок к блокировке».

Проверяется не текст формулировок, а логика: какой признак поднимает тревогу,
что уровень берётся по худшему, и что здоровый аккаунт не пугают без причины.
Ложная тревога здесь так же вредна, как пропущенная: если карточка всё время
красная, на неё перестают смотреть.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app import models
from app.services import account_risk


def make(**kwargs) -> models.WarmupAccount:
    """Подключённый месяц назад здоровый аккаунт; поля переопределяются."""
    defaults = {
        "id": 1,
        "label": "Основной",
        "operational_status": "ready",
        "spambot_status": "clear",
        "consecutive_errors": 0,
        "daily_send_limit": 30,
        "unread_growth": 0,
        "connected_at": datetime.now(UTC) - timedelta(days=40),
        "last_tg_error_code": "",
    }
    defaults.update(kwargs)
    return models.WarmupAccount(**defaults)


# --- Уровни ----------------------------------------------------------------


def test_здоровый_аккаунт_не_тревожат():
    risk = account_risk.assess(make())
    assert risk.level == "ok"
    assert risk.can_send is True
    # Пустая карточка выглядит как сбой загрузки, поэтому текст обязателен.
    assert risk.reasons and risk.advice


def test_бан_виден_сразу_и_запрещает_отправку():
    risk = account_risk.assess(make(operational_status="banned"))
    assert risk.level == "blocked"
    assert risk.can_send is False
    assert any("SpamBot" in line for line in risk.advice)


def test_разлогиненная_сессия_это_опасность_а_не_бан():
    risk = account_risk.assess(make(operational_status="reauth"))
    assert risk.level == "danger"
    assert risk.can_send is False


def test_peer_flood_даёт_опасность_и_конкретный_совет():
    risk = account_risk.assess(make(last_tg_error_code="PEER_FLOOD"))
    assert risk.level == "danger"
    assert any("сутки" in line for line in risk.advice)


def test_обычный_flood_wait_это_только_предупреждение():
    """Слишком частая отправка — повод сбавить темп, а не паниковать."""
    risk = account_risk.assess(make(last_tg_error_code="FLOOD_WAIT_30"))
    assert risk.level == "watch"


def test_ограничение_от_спамбота_поднимает_тревогу():
    risk = account_risk.assess(make(spambot_status="limited"))
    assert risk.level == "danger"


# --- Счётчики --------------------------------------------------------------


def test_три_ошибки_подряд_предупреждение_шесть_опасность():
    assert account_risk.assess(make(consecutive_errors=2)).level == "ok"
    assert account_risk.assess(make(consecutive_errors=3)).level == "watch"
    assert account_risk.assess(make(consecutive_errors=6)).level == "danger"


def test_молодой_аккаунт_с_большим_лимитом_предупреждают():
    young = make(connected_at=datetime.now(UTC) - timedelta(days=3), daily_send_limit=50)
    risk = account_risk.assess(young)
    assert risk.level == "watch"
    assert any("3 дн" in line for line in risk.reasons)


def test_молодой_аккаунт_с_маленьким_лимитом_не_трогают():
    young = make(connected_at=datetime.now(UTC) - timedelta(days=3), daily_send_limit=15)
    assert account_risk.assess(young).level == "ok"


def test_подход_к_дневному_лимиту_предупреждает():
    assert account_risk.assess(make(daily_send_limit=30), sent_today=20).level == "ok"
    assert account_risk.assess(make(daily_send_limit=30), sent_today=28).level == "watch"


def test_резкий_рост_непрочитанных_замечают():
    risk = account_risk.assess(make(unread_growth=35))
    assert risk.level == "watch"
    assert any("непрочитанные" in line for line in risk.reasons)


def test_пауза_показывает_сколько_осталось():
    until = datetime.now(UTC) + timedelta(hours=5)
    risk = account_risk.assess(make(cooldown_until=until))
    assert risk.level == "watch"
    assert risk.cooldown_until == until
    assert any("ч" in line for line in risk.reasons)


def test_истёкшая_пауза_не_считается():
    past = datetime.now(UTC) - timedelta(hours=5)
    assert account_risk.assess(make(cooldown_until=past)).level == "ok"


def test_время_без_пояса_не_ломает_проверку():
    """SQLite отдаёт даты без часового пояса — сравнение упало бы с TypeError."""
    naive = (datetime.now(UTC) + timedelta(hours=3)).replace(tzinfo=None)
    risk = account_risk.assess(make(cooldown_until=naive))
    assert risk.level == "watch"


# --- Несколько причин и несколько аккаунтов --------------------------------


def test_причины_показываются_все_а_уровень_по_худшей():
    risk = account_risk.assess(
        make(operational_status="banned", consecutive_errors=4, unread_growth=30)
    )
    assert risk.level == "blocked"
    assert len(risk.reasons) >= 3


def test_общий_уровень_берётся_по_худшему_аккаунту():
    items = [
        account_risk.assess(make(id=1)),
        account_risk.assess(make(id=2, operational_status="banned")),
        account_risk.assess(make(id=3, consecutive_errors=3)),
    ]
    assert account_risk.worst(items) == "blocked"
    assert account_risk.worst([]) == "ok"


def test_проблемные_аккаунты_идут_первыми():
    items = [
        account_risk.assess(make(id=1)),
        account_risk.assess(make(id=2, consecutive_errors=3)),
        account_risk.assess(make(id=3, operational_status="banned")),
    ]
    items.sort(key=account_risk.sort_key)
    assert [item.account_id for item in items] == [3, 2, 1]


# --- Ручка -----------------------------------------------------------------


def test_ручка_отдаёт_только_подключённые_аккаунты(client, db, user, auth_headers):
    db.add(models.WarmupAccount(owner_id=user.id, label="Не подключён"))
    db.add(
        models.WarmupAccount(
            owner_id=user.id,
            label="Рабочий",
            connected_at=datetime.now(UTC) - timedelta(days=30),
            tg_id="777",
        )
    )
    db.commit()
    body = client.get("/api/warmup/risk", headers=auth_headers).json()
    assert [item["label"] for item in body["items"]] == ["Рабочий"]
    assert body["level"] == "ok"


def test_ручка_не_показывает_чужие_аккаунты(client, db, user, auth_headers):
    stranger = models.User(telegram_id=8008, username="other3")
    db.add(stranger)
    db.commit()
    db.refresh(stranger)
    db.add(
        models.WarmupAccount(
            owner_id=stranger.id,
            label="Чужой",
            connected_at=datetime.now(UTC),
            operational_status="banned",
        )
    )
    db.commit()
    body = client.get("/api/warmup/risk", headers=auth_headers).json()
    assert body["items"] == []
    assert body["level"] == "ok"


def test_риск_доступен_только_своим(client):
    assert client.get("/api/warmup/risk").status_code in (401, 403)
