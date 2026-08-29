"""Волна 15: настройки AI-оффера у пользователя

Зачем колонки, а не отдельная таблица: настройка одна на человека и читается
на каждом запуске рассылки. Отдельная таблица на одну строку — это лишний JOIN
в самом горячем месте и лишний код на «а если строки ещё нет».

* users.ai_offer_template  — скелет оффера («примерный вид»), по которому ИИ
  пишет индивидуальный текст каждому лиду.
* users.ai_offer_materials — сколько последних материалов канала смотреть.

revision: 0012
"""

from alembic import op
import sqlalchemy as sa

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def _columns(table: str) -> set[str]:
    bind = op.get_bind()
    return {c["name"] for c in sa.inspect(bind).get_columns(table)}


def upgrade() -> None:
    cols = _columns("users")
    if "ai_offer_template" not in cols:
        op.add_column("users", sa.Column("ai_offer_template", sa.Text(), server_default=""))
    if "ai_offer_materials" not in cols:
        op.add_column(
            "users",
            sa.Column(
                "ai_offer_materials",
                sa.Integer(),
                nullable=False,
                server_default="5",
            ),
        )


def downgrade() -> None:
    cols = _columns("users")
    if "ai_offer_materials" in cols:
        op.drop_column("users", "ai_offer_materials")
    if "ai_offer_template" in cols:
        op.drop_column("users", "ai_offer_template")
