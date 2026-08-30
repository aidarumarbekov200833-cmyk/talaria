"""Напоминание об окончании тарифа и предупреждение о квоте YouTube.

Проверяем не факт отправки в Telegram (сеть в тестах не трогаем), а решения:
кому напоминать, сколько раз и что происходит после продления.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

from app import models
from app.services import notices


def _subscription(
    db, owner_id: int, days: float, *, status="active", blocked=False
) -> models.Subscription:
    row = models.Subscription(
        owner_id=owner_id,
        plan="zephyr",
        status=status,
        blocked=blocked,
        paid_until=datetime.now(UTC) + timedelta(days=days),
    )
    db.add(row)
    db.commit()
    return row


def _notifications(db, owner_id: int) -> list[str]:
    rows = (
        db.query(models.EventLog)
        .filter(models.EventLog.owner_id == owner_id, models.EventLog.scope == "notification")
        .order_by(models.EventLog.id)
        .all()
    )
    return [row.message for row in rows]


def test_за_три_дня_напоминание_уходит_один_раз(db, user, monkeypatch):
    sent: list[tuple[int, str]] = []
    monkeypatch.setattr(notices, "notify_user", lambda tg, text, **kw: sent.append((tg, text)))
    _subscription(db, user.id, days=2.5)

    assert notices.send_expiry_notices(db) == 1
    # Повторный обход в тот же период ничего не добавляет: иначе человек
    # получал бы одно и то же сообщение каждый час.
    assert notices.send_expiry_notices(db) == 0

    messages = _notifications(db, user.id)
    assert len(messages) == 1
    assert "3 дн" in messages[0]
    assert len(sent) == 1
    assert sent[0][0] == user.telegram_id


def test_за_день_приходит_второе_напоминание(db, user, monkeypatch):
    monkeypatch.setattr(notices, "notify_user", lambda *a, **kw: True)
    subscription = _subscription(db, user.id, days=2.5)
    notices.send_expiry_notices(db)

    # Тариф не продлевали, срок подошёл ближе — этап сменился.
    subscription.paid_until = datetime.now(UTC) + timedelta(hours=20)
    db.commit()
    assert notices.send_expiry_notices(db) == 1

    messages = _notifications(db, user.id)
    assert len(messages) == 2
    assert "завтра" in messages[1]


def test_продление_сбрасывает_напоминания(db, user, monkeypatch):
    monkeypatch.setattr(notices, "notify_user", lambda *a, **kw: True)
    subscription = _subscription(db, user.id, days=1.0)
    notices.send_expiry_notices(db)

    # Человек продлил на месяц: до следующего окончания далеко, напоминать не о чем.
    subscription.paid_until = datetime.now(UTC) + timedelta(days=30)
    db.commit()
    assert notices.send_expiry_notices(db) == 0

    # А когда новый срок подойдёт к концу — напоминание обязано прийти снова,
    # хотя этап «за три дня» для прошлого периода уже отмечался.
    subscription.paid_until = datetime.now(UTC) + timedelta(days=2.5)
    db.commit()
    assert notices.send_expiry_notices(db) == 1
    assert len(_notifications(db, user.id)) == 2


def test_заблокированному_и_далёкому_сроку_не_напоминаем(db, user, monkeypatch):
    monkeypatch.setattr(notices, "notify_user", lambda *a, **kw: True)
    stranger = models.User(telegram_id=424_242, username="blocked")
    db.add(stranger)
    db.commit()
    _subscription(db, stranger.id, days=1.0, blocked=True)
    _subscription(db, user.id, days=10.0)

    assert notices.send_expiry_notices(db) == 0
    assert _notifications(db, user.id) == []
    assert _notifications(db, stranger.id) == []


def test_истёкший_тариф_повторно_не_дёргает(db, user, monkeypatch):
    """Окончание уже позади — это не напоминание, а спам вдогонку."""
    monkeypatch.setattr(notices, "notify_user", lambda *a, **kw: True)
    _subscription(db, user.id, days=-1.0)
    assert notices.send_expiry_notices(db) == 0


def test_порог_квоты_считается_по_проценту():
    assert notices.quota_stage_for(0, 10_000) == 0
    assert notices.quota_stage_for(7_999, 10_000) == 0
    assert notices.quota_stage_for(8_000, 10_000) == 80
    assert notices.quota_stage_for(9_500, 10_000) == 95
    # Лимит нулевой или отрицательный — считать проценты не от чего.
    assert notices.quota_stage_for(100, 0) == 0


def test_о_квоте_предупреждаем_один_раз_на_порог(db, user, monkeypatch):
    sent: list[str] = []
    monkeypatch.setattr(notices, "notify_user", lambda tg, text, **kw: sent.append(text))
    key = models.ApiKey(owner_id=user.id, key="AIzaTest", daily_quota=10_000)
    db.add(key)
    db.commit()
    usage = models.QuotaUsage(api_key_id=key.id, day=date.today(), units=8_100, calls=3)
    db.add(usage)
    db.commit()

    assert notices.notify_quota(db, user.id, usage, 10_000) is True
    # Расход растёт, но порог тот же — второго сообщения быть не должно.
    usage.units = 8_900
    assert notices.notify_quota(db, user.id, usage, 10_000) is False
    # А переход через следующий порог — это уже новая новость.
    usage.units = 9_700
    assert notices.notify_quota(db, user.id, usage, 10_000) is True

    assert len(sent) == 2
    assert "80%" in sent[0]
    assert "95%" in sent[1]


def test_квота_списывается_и_предупреждает_через_spend(db, user, monkeypatch):
    """Проверяем, что предупреждение реально подключено к списанию квоты."""
    from app import quota

    sent: list[str] = []
    monkeypatch.setattr(notices, "notify_user", lambda tg, text, **kw: sent.append(text))
    key = models.ApiKey(owner_id=user.id, key="AIzaSpend", daily_quota=110)
    db.add(key)
    db.commit()

    quota.spend(db, key, "search.list", times=1)
    row = db.query(models.QuotaUsage).filter_by(api_key_id=key.id).one()
    assert row.units >= 100  # поиск стоит 100 единиц
    assert sent, "переход через порог должен был вызвать предупреждение"


def test_заметка_у_лида_сохраняется_и_видна_только_владельцу(client, db, user, auth_headers):
    """Заметка — личный текст владельца базы, чужому она недоступна."""
    database = models.LeadDatabase(owner_id=user.id, name="База")
    db.add(database)
    db.commit()
    lead = models.Lead(database_id=database.id, channel_id="UC1", title="Канал")
    db.add(lead)
    db.commit()

    response = client.put(
        f"/api/leads/{lead.id}/note",
        json={"note": "  созвон в четверг  "},
        headers=auth_headers,
    )
    assert response.status_code == 200
    # Пробелы по краям срезаем: иначе «пустая» заметка из пробелов выглядела бы
    # как заполненная.
    assert response.json()["note"] == "созвон в четверг"

    # И заметка возвращается в обычном списке лидов, а не только в ответе на запись.
    listing = client.get(f"/api/leads?database_id={database.id}", headers=auth_headers)
    assert listing.status_code == 200
    assert listing.json()["leads"][0]["note"] == "созвон в четверг"

    # Стереть — пустой строкой, отдельного эндпоинта для этого не нужно.
    cleared = client.put(f"/api/leads/{lead.id}/note", json={"note": ""}, headers=auth_headers)
    assert cleared.json()["note"] == ""


def test_заметка_к_чужому_лиду_не_пишется(client, db, user, auth_headers):
    stranger = models.User(telegram_id=515_151, username="stranger2")
    db.add(stranger)
    db.commit()
    database = models.LeadDatabase(owner_id=stranger.id, name="Чужая база")
    db.add(database)
    db.commit()
    lead = models.Lead(database_id=database.id, channel_id="UC2", title="Чужой канал")
    db.add(lead)
    db.commit()

    response = client.put(
        f"/api/leads/{lead.id}/note", json={"note": "перехват"}, headers=auth_headers
    )
    assert response.status_code == 404
    db.refresh(lead)
    assert lead.note == ""
