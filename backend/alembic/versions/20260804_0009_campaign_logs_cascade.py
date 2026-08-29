"""Campaign logs FK with ON DELETE CASCADE.

В бою удаление рассылки падало:

    update or delete on table "campaigns" violates foreign key constraint
    "campaign_logs_campaign_id_fkey" on table "campaign_logs"

Имя ограничения — автогенерированное (`*_fkey`), такие имена даёт
`Base.metadata.create_all`, а не наша baseline-миграция. Значит, боевая база
была создана до появления `ondelete="CASCADE"` в модели, и ограничение
там до сих пор без каскада. Пересоздаём его явно — именно на каскад
рассчитан passive_deletes=True в Campaign.logs.

То же самое для campaign_sends: там тот же сценарий.

Revision ID: 0009
Revises: 0008
"""

from alembic import op
from sqlalchemy import inspect

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def _recreate(table: str, column: str, target: str) -> None:
    """Пересобрать внешний ключ с ON DELETE CASCADE.

    Имя ограничения берём из базы, а не угадываем: оно разное у тех, кто
    ставил схему create_all, и у тех, кто накатывал baseline-миграцию.
    """
    bind = op.get_bind()
    insp = inspect(bind)
    if table not in insp.get_table_names():
        return
    for fk in insp.get_foreign_keys(table):
        if fk.get("constrained_columns") != [column]:
            continue
        if (fk.get("options") or {}).get("ondelete", "").upper() == "CASCADE":
            return
        name = fk.get("name")
        if not name:
            return
        op.drop_constraint(name, table, type_="foreignkey")
        op.create_foreign_key(name, table, target, [column], ["id"], ondelete="CASCADE")
        return


def upgrade() -> None:
    _recreate("campaign_logs", "campaign_id", "campaigns")
    _recreate("campaign_sends", "campaign_id", "campaigns")
    _recreate("campaign_sends", "lead_id", "leads")


def downgrade() -> None:
    # Обратно не разворачиваем: возврат к ФК без каскада — это возврат бага.
    pass
