"""Удаление кампании не должно стирать память «кому уже писали».

Ревью 2026-09-01: delete_campaign удалял CampaignSend вместе с кампанией, и
skip_contacted (который считает только status=sent) переставал видеть этих
лидов — им уходило повторное первое сообщение в следующей рассылке.
"""

from __future__ import annotations

from app import models


def _lead_fixture(db, owner_id: int) -> models.Lead:
    database = models.LeadDatabase(owner_id=owner_id, name="База")
    db.add(database)
    db.commit()
    lead = models.Lead(
        database_id=database.id,
        channel_id="UC-lead-1",
        title="Канал",
        tg_username="lead1",
    )
    db.add(lead)
    db.commit()
    db.refresh(lead)
    return lead


def test_delete_campaign_keeps_send_history(client, db, paid_user, auth_headers):
    campaign = models.Campaign(
        owner_id=paid_user.id, name="C", database_name="База", status="completed"
    )
    db.add(campaign)
    db.commit()
    lead = _lead_fixture(db, paid_user.id)
    db.add(
        models.CampaignSend(
            owner_id=paid_user.id,
            campaign_id=campaign.id,
            lead_id=lead.id,
            status="sent",
        )
    )
    db.commit()

    campaign_id = campaign.id
    lead_id = lead.id
    owner_id = paid_user.id

    response = client.delete(f"/api/campaigns/{campaign_id}", headers=auth_headers)
    assert response.status_code == 200

    db.expire_all()
    rows = db.query(models.CampaignSend).filter(models.CampaignSend.owner_id == owner_id).all()
    assert len(rows) == 1, "история отправки должна пережить удаление кампании"
    assert rows[0].campaign_id is None
    assert rows[0].lead_id == lead_id
    assert rows[0].status == "sent"
    assert (
        db.query(models.Campaign).filter(models.Campaign.id == campaign_id).one_or_none() is None
    )


def test_uncertain_send_counts_as_contacted(client, db, paid_user, auth_headers):
    """SEND_UNCERTAIN-исход исключает лида из следующей рассылки.

    Ревью PR #3: отказ от ретрая при неопределённом исходе (сообщение могло
    уйти) не имел смысла, пока skip_contacted видел только status=sent —
    дубль просто переезжал в следующую кампанию. Теперь строка со статусом
    uncertain тоже считается «уже писали», а честный failed — по-прежнему нет.
    """
    lead = _lead_fixture(db, paid_user.id)
    lead.tg_status = "verified"
    # Предпросмотру нужен хотя бы один оффер — иначе честный 400 до отбора.
    db.add(models.Offer(owner_id=paid_user.id, name="Оффер", text="Привет, {name}!"))
    db.add(
        models.CampaignSend(
            owner_id=paid_user.id,
            campaign_id=None,
            lead_id=lead.id,
            status="uncertain",
        )
    )
    db.commit()
    database_id = lead.database_id

    response = client.post(
        "/api/campaigns/launch-preview",
        json={"database_id": database_id, "skip_contacted": True, "limit": 10},
        headers=auth_headers,
    )
    assert response.status_code == 200
    body = response.json()
    assert body["skipped_contacted"] == 1, "uncertain должен считаться «уже писали»"

    # Честная ошибка (failed) лида не исключает: он ничего не получал.
    db.query(models.CampaignSend).update({models.CampaignSend.status: "failed"})
    db.commit()
    response = client.post(
        "/api/campaigns/launch-preview",
        json={"database_id": database_id, "skip_contacted": True, "limit": 10},
        headers=auth_headers,
    )
    assert response.status_code == 200
    assert response.json()["skipped_contacted"] == 0


def test_uncertain_excluded_from_recipients(client, db, paid_user, auth_headers):
    """Дедупликация /recipients видит uncertain так же, как launch-preview.

    Ревью PR #4: uncertain был раскатан на предпросмотр, но /recipients — это
    отдельный запрос с собственным skip_contacted; без теста регресс в одном
    месте не тянул бы за собой второе.
    """
    lead = _lead_fixture(db, paid_user.id)
    campaign = models.Campaign(
        owner_id=paid_user.id, name="C", database_name="База", status="draft"
    )
    db.add(campaign)
    db.add(
        models.CampaignSend(
            owner_id=paid_user.id,
            campaign_id=None,
            lead_id=lead.id,
            status="uncertain",
        )
    )
    db.commit()

    response = client.post(
        f"/api/campaigns/{campaign.id}/recipients",
        json={"database_id": lead.database_id, "skip_contacted": True},
        headers=auth_headers,
    )
    assert response.status_code == 200
    body = response.json()
    assert body["skipped_contacted"] == 1
    assert body["total"] == 0

    db.query(models.CampaignSend).update({models.CampaignSend.status: "failed"})
    db.commit()
    response = client.post(
        f"/api/campaigns/{campaign.id}/recipients",
        json={"database_id": lead.database_id, "skip_contacted": True},
        headers=auth_headers,
    )
    assert response.status_code == 200
    assert response.json()["skipped_contacted"] == 0


def test_uncertain_excluded_from_verify_candidates(db, paid_user, monkeypatch):
    """_verify_launch_candidates не доверифицирует лида с uncertain-историей.

    Прямой вызов вместо /launch: полный запуск требует живого шлюза, а здесь
    проверяется только фильтр кандидатов. verify_lead подменён счётчиком —
    если фильтр сломается, кандидат дойдёт до «верификации» и тест упадёт.
    """
    from app.routers import campaigns as campaigns_router
    from app.schemas import CampaignLaunchIn

    lead = _lead_fixture(db, paid_user.id)
    lead.tg_status = "pending"
    account = models.WarmupAccount(
        owner_id=paid_user.id,
        label="acc",
        tg_id="10001",
        verification_enabled=True,
    )
    db.add(account)
    db.add(
        models.CampaignSend(
            owner_id=paid_user.id,
            campaign_id=None,
            lead_id=lead.id,
            status="uncertain",
        )
    )
    db.commit()
    database = db.query(models.LeadDatabase).filter_by(id=lead.database_id).one()

    calls: list[int] = []

    class _Profile:
        sendable = True

    def fake_verify(lead_obj, owner_id, account_ids, start_index=0) -> _Profile:  # noqa: ANN001
        calls.append(lead_obj.id)
        return _Profile()

    monkeypatch.setattr(campaigns_router.contacts, "verify_lead", fake_verify)
    data = CampaignLaunchIn(database_id=database.id, skip_contacted=True, limit=10)

    checked, _verified = campaigns_router._verify_launch_candidates(
        db, paid_user, data, database, [account]
    )
    assert checked == 0 and not calls, "uncertain-лид не должен идти в доверификацию"

    # Честный failed кандидата не прячет.
    db.query(models.CampaignSend).update({models.CampaignSend.status: "failed"})
    db.commit()
    checked, _verified = campaigns_router._verify_launch_candidates(
        db, paid_user, data, database, [account]
    )
    assert checked == 1 and calls == [lead.id]
