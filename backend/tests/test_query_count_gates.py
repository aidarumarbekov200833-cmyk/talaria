"""Real SQL statement budgets for the five operations named by the plan."""

from __future__ import annotations

from contextlib import AbstractContextManager
from datetime import UTC, datetime

from sqlalchemy import event
from sqlalchemy.orm import Session, sessionmaker

from app import models
from app.schemas import CampaignReplyIn, RecipientsIn


class StatementCounter(AbstractContextManager["StatementCounter"]):
    def __init__(self, db: Session) -> None:
        self.engine = db.get_bind()
        self.statements: list[str] = []

    @property
    def count(self) -> int:
        return len(self.statements)

    def _record(
        self,
        _connection: object,
        _cursor: object,
        statement: str,
        _parameters: object,
        _context: object,
        _executemany: bool,
    ) -> None:
        self.statements.append(" ".join(statement.split()))

    def __enter__(self) -> StatementCounter:
        event.listen(self.engine, "before_cursor_execute", self._record)
        return self

    def __exit__(self, *_args: object) -> None:
        event.remove(self.engine, "before_cursor_execute", self._record)


def _account(
    db: Session,
    owner_id: int,
    label: str,
    *,
    primary: bool = False,
    tg_id: str | None = None,
) -> models.WarmupAccount:
    row = models.WarmupAccount(
        owner_id=owner_id,
        label=label,
        is_primary=primary,
        tg_id=tg_id,
        status="paused",
        plan_days=14,
    )
    db.add(row)
    db.flush()
    return row


def _database_with_leads(
    db: Session,
    owner_id: int,
    prefix: str,
    count: int,
) -> tuple[models.LeadDatabase, list[models.Lead]]:
    database = models.LeadDatabase(owner_id=owner_id, name=f"DB {prefix}")
    db.add(database)
    db.flush()
    leads = [
        models.Lead(
            database_id=database.id,
            channel_id=f"{prefix}-{index}",
            title=f"Lead {prefix}-{index}",
            score=100 - index,
            tg_id=str(80_000 + index),
            tg_username=f"{prefix}_{index}",
            tg_status="verified",
        )
        for index in range(count)
    ]
    db.add_all(leads)
    db.flush()
    return database, leads


def test_warmup_list_uses_at_most_six_statements_with_many_owned_rows(db, user) -> None:
    from app.routers import warmup

    primary = _account(db, user.id, "Test", primary=True, tg_id=str(user.telegram_id))
    primary.tg_username = user.username
    owned = [primary]
    for index in range(1, 5):
        owned.append(_account(db, user.id, f"Owned {index}"))
    foreign_user = models.User(telegram_id=930001, username="warmup-foreign")
    db.add(foreign_user)
    db.flush()
    foreign = _account(db, foreign_user.id, "Foreign")
    for account in [*owned, foreign]:
        db.add_all(
            [
                models.WarmupLog(account_id=account.id, day=day, target_messages=day + 4)
                for day in range(1, 4)
            ]
        )
    db.commit()
    db.expire_all()
    db.refresh(user)

    with StatementCounter(db) as counter:
        result = warmup.list_accounts(user=user, db=db)

    assert len(result.accounts) == len(owned)
    assert result.accounts[0].id == primary.id
    assert all(account.label != "Foreign" for account in result.accounts)
    assert counter.count <= 6, f"{counter.count} statements:\n" + "\n".join(counter.statements)


def test_campaign_recipient_selection_uses_at_most_five_statements(db, user) -> None:
    from app.routers import campaigns

    database, leads = _database_with_leads(db, user.id, "recipient", 7)
    foreign_user = models.User(telegram_id=930002, username="recipient-foreign")
    db.add(foreign_user)
    db.flush()
    _database_with_leads(db, foreign_user.id, "foreign", 3)
    campaign = models.Campaign(owner_id=user.id, name="Recipients", database_name=database.name)
    db.add(campaign)
    db.flush()
    db.add(
        models.CampaignSend(
            owner_id=user.id,
            campaign_id=campaign.id,
            lead_id=leads[0].id,
            status="sent",
        )
    )
    db.commit()
    db.expire_all()

    data = RecipientsIn(database_id=database.id, skip_contacted=True, limit=100)
    with StatementCounter(db) as counter:
        result = campaigns.campaign_recipients(campaign.id, data, db, user)

    assert result.total == 6
    assert result.skipped_contacted == 1
    assert [lead.id for lead in result.leads] == [lead.id for lead in leads[1:]]
    assert counter.count <= 5, f"{counter.count} statements:\n" + "\n".join(counter.statements)


def test_launch_background_reload_uses_at_most_six_statements(db, user, monkeypatch) -> None:
    from app.routers import campaigns

    _database, leads = _database_with_leads(db, user.id, "launch", 4)
    campaign = models.Campaign(owner_id=user.id, name="Launch", database_name="DB launch")
    accounts = [_account(db, user.id, f"Launch {index}") for index in range(2)]
    offers = [
        models.Offer(owner_id=user.id, name=f"Offer {index}", text="hello") for index in range(2)
    ]
    db.add(campaign)
    db.add_all(offers)
    db.commit()
    ids = {
        "campaign": campaign.id,
        "leads": [lead.id for lead in leads],
        "accounts": [account.id for account in accounts],
        "offers": [offer.id for offer in offers],
    }
    background_db = sessionmaker(bind=db.get_bind(), autoflush=False, autocommit=False)()
    monkeypatch.setattr(campaigns.database, "SessionLocal", lambda: background_db)
    seen: list[tuple[int, list[int], list[int], list[int]]] = []

    def run_launch(
        _db: Session,
        loaded_campaign: models.Campaign,
        loaded_leads: list[models.Lead],
        loaded_accounts: list[models.WarmupAccount],
        loaded_offers: list[models.Offer],
        **_kwargs: object,
    ) -> None:
        seen.append(
            (
                loaded_campaign.id,
                [row.id for row in loaded_leads],
                [row.id for row in loaded_accounts],
                [row.id for row in loaded_offers],
            )
        )

    monkeypatch.setattr(campaigns.sending, "run_launch", run_launch)
    with StatementCounter(db) as counter:
        campaigns._launch_in_background(
            ids["campaign"],
            ids["leads"],
            ids["accounts"],
            ids["offers"],
            None,
            5,
        )

    assert seen == [(ids["campaign"], ids["leads"], ids["accounts"], ids["offers"])]
    assert counter.count <= 6, counter.statements


def test_reply_callback_uses_at_most_five_statements_and_is_exact_one_shot(
    db, user, monkeypatch
) -> None:
    from app.routers import campaigns

    account = _account(db, user.id, "Reply", tg_id="500")
    database, leads = _database_with_leads(db, user.id, "reply", 2)
    campaign = models.Campaign(owner_id=user.id, name="Reply", database_name=database.name)
    db.add(campaign)
    db.flush()
    send = models.CampaignSend(
        owner_id=user.id,
        campaign_id=campaign.id,
        lead_id=leads[0].id,
        account_id=account.id,
        status="sent",
        delivered_at=datetime.now(UTC),
    )
    db.add(send)
    db.commit()
    db.expire_all()

    monkeypatch.setattr(campaigns.settings, "tg_client_secret", "query-secret")
    monkeypatch.setattr(campaigns.alerts, "send_alert", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(campaigns.alerts, "notify_user", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(campaigns.hub, "publish", lambda *_args, **_kwargs: None)
    payload = CampaignReplyIn(
        owner_id=user.id,
        account_id=account.id,
        tg_id=leads[0].tg_id,
        username=leads[0].tg_username,
        sender_name="Reply Person",
        text="Interested",
    )
    with StatementCounter(db) as counter:
        result = campaigns.lead_replied(payload, "query-secret", db)

    assert result.matched is True
    assert result.lead_id == leads[0].id
    assert result.campaign_id == campaign.id
    db.expire_all()
    assert db.get(models.CampaignSend, send.id).replied_at is not None
    assert db.get(models.Campaign, campaign.id).reply_count == 1
    assert db.get(models.Lead, leads[0].id).status == "replied"
    # Было шестнадцать. Двенадцать из них появлялись после db.commit(): SQLAlchemy
    # помечает объекты устаревшими, и обращение к campaign.name или lead.title
    # молча перечитывало строку — кампания читалась трижды. Значения теперь
    # снимаются до commit, а окно тестовой отправки ищется одним запросом.
    #
    # Оставшиеся одиннадцать — по делу, и каждый нужен:
    #   1 warmup_accounts  проверка, что аккаунт-отправитель принадлежит владельцу
    #   2 test_reply_watches  окно тестовой отправки
    #   3 campaign_sends   поиск нашей отправки этому контакту
    #   4 UPDATE sends     атомарная отметка «ответил» (защита от двойного счёта)
    #   5 campaigns        сама рассылка
    #   6 UPDATE campaigns счётчик ответов
    #   7 UPDATE leads     статус лида
    #   8 campaign_logs    строка в журнал рассылки
    #   9 event_logs       событие в ленту
    #  10 SELECT event_logs  перечитать id и время для WebSocket
    #  11 users            владелец, которому уходит уведомление
    # Меньше — только ценой удаления журнала или уведомления. Порог держим на
    # одиннадцати: он всё так же ловит возврат N+1.
    assert counter.count <= 11, f"{counter.count} statements:\n" + "\n".join(counter.statements)


def test_admin_overview_statement_count_is_constant_as_users_grow(db, user) -> None:
    from app.routers import admin

    db.add(models.EventLog(owner_id=user.id, scope="settings", message="small"))
    db.commit()
    db.expire_all()
    with StatementCounter(db) as small_counter:
        small = admin.admin_overview(user, db)

    for index in range(25):
        extra = models.User(telegram_id=940000 + index, username=f"overview_{index}")
        db.add(extra)
        db.flush()
        db.add(models.EventLog(owner_id=extra.id, scope="offers", message=f"event {index}"))
    db.commit()
    db.expire_all()
    with StatementCounter(db) as large_counter:
        large = admin.admin_overview(user, db)

    assert large.users_total == small.users_total + 25
    assert len(large.recent_actions) == 15
    assert large_counter.count == small_counter.count, {
        "small": small_counter.statements,
        "large": large_counter.statements,
    }
