"""Колонка score у лида + пересчёт уже собранных каналов.

Почему пересчёт здесь, а не отдельной кнопкой. Без бэкфилла все уже собранные
лиды остались бы с нулём, и сортировка по оценке выглядела бы сломанной:
человек включает «по баллу» и видит случайный порядок. Оценка считается той же
функцией, что и в боевом коде (services.compute_lead_score), чтобы не завести вторую
реализацию формулы, которая потом разойдётся с основной.

Пересчёт идёт пачками: база лидов легко бывает на десятки тысяч строк, и
тянуть их в память целиком ради одной миграции не нужно.

Revision ID: 0005
Revises: 0004
"""

import sqlalchemy as sa
from alembic import op

from app.services import compute_lead_score

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None

BATCH = 1000


def upgrade() -> None:
    # server_default="0" обязателен: колонка NOT NULL, а в таблице уже есть
    # строки — без значения по умолчанию Postgres откажется добавить её.
    op.add_column(
        "leads",
        sa.Column("score", sa.Integer(), nullable=False, server_default="0"),
    )
    op.create_index("ix_leads_score", "leads", ["score"])

    conn = op.get_bind()
    leads = sa.table(
        "leads",
        sa.column("id", sa.Integer),
        sa.column("subscribers", sa.Integer),
        sa.column("video_count", sa.Integer),
        sa.column("view_count", sa.BigInteger),
        sa.column("matched_keyword", sa.String),
        sa.column("score", sa.Integer),
    )

    last_id = 0
    while True:
        rows = conn.execute(
            sa.select(
                leads.c.id,
                leads.c.subscribers,
                leads.c.video_count,
                leads.c.view_count,
                leads.c.matched_keyword,
            )
            .where(leads.c.id > last_id)
            .order_by(leads.c.id)
            .limit(BATCH)
        ).fetchall()
        if not rows:
            break

        for row in rows:
            conn.execute(
                leads.update()
                .where(leads.c.id == row.id)
                .values(
                    score=compute_lead_score(
                        row.subscribers,
                        row.video_count,
                        row.view_count,
                        row.matched_keyword,
                    )
                )
            )
        last_id = rows[-1].id


def downgrade() -> None:
    op.drop_index("ix_leads_score", table_name="leads")
    op.drop_column("leads", "score")
