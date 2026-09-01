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
