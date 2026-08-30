"""Альбом офферов, мультирегион трендов и снос таблиц нейрокомментинга.

Revision ID: 0034
Revises: 0033

Три правки одного релиза v38:

1. `offers.images` — JSON-список картинок. Владелец выбрал вариант «альбом»:
   2–10 фото уходят лиду одним сообщением. Старое поле `image` остаётся первой
   картинкой альбома, поэтому предпросмотр и записи прошлых релизов не ломаются.
2. `topic_runs.region` расширен до 64 символов: регион трендов стал списком
   («поиск популярного — чтобы несколько регионов можно было выбрать»), а в
   восемь символов помещался только один код.
3. Таблицы `neuro_comments`, `neuro_runs`, `neuro_targets` удаляются. Код
   нейрокомментинга вырезан в v37, таблицы тогда намеренно оставили с данными;
   владелец подтвердил «снеси таблицы». Порядок сноса — от зависимых к
   родительским, иначе внешние ключи не дадут удалить.

Понижение возвращает пустые таблицы (данные не восстановить) и сужает region
обратно — на этом шаге записи со списком регионов обрезаются, поэтому
понижаться стоит только сразу после ошибочного повышения.
"""

from __future__ import annotations

import json

import sqlalchemy as sa

from alembic import op

revision = "0034"
down_revision = "0033"
branch_labels = None
depends_on = None

NEURO_TABLES = ("neuro_comments", "neuro_runs", "neuro_targets")


def _inspector() -> sa.Inspector:
    return sa.inspect(op.get_bind())


def _tables() -> set[str]:
    return set(_inspector().get_table_names())


def _columns(table: str) -> set[str]:
    if table not in _tables():
        return set()
    return {column["name"] for column in _inspector().get_columns(table)}


def upgrade() -> None:
    if "images" not in _columns("offers"):
        op.add_column(
            "offers",
            sa.Column("images", sa.Text(), nullable=False, server_default="[]"),
        )
        # Уже сохранённые одиночные картинки становятся альбомом из одного кадра,
        # чтобы отправка читала одно поле, а не два разных случая. JSON собираем
        # в Python: json_build_array есть только в PostgreSQL, а тесты и локальная
        # разработка идут на SQLite.
        bind = op.get_bind()
        rows = bind.execute(
            sa.text(
                "SELECT id, image FROM offers "
                "WHERE image IS NOT NULL AND image <> '' "
                "AND (images IS NULL OR images = '[]')"
            )
        ).fetchall()
        for row in rows:
            bind.execute(
                sa.text("UPDATE offers SET images = :images WHERE id = :id"),
                {"images": json.dumps([row[1]], ensure_ascii=False), "id": row[0]},
            )

    if "topic_runs" in _tables():
        # batch_alter_table: SQLite не умеет ALTER COLUMN, alembic пересоздаёт
        # таблицу. На PostgreSQL это обычный ALTER.
        with op.batch_alter_table("topic_runs") as batch:
            batch.alter_column(
                "region",
                existing_type=sa.String(length=8),
                type_=sa.String(length=64),
                existing_nullable=True,
            )

    tables = _tables()
    for name in NEURO_TABLES:
        if name in tables:
            op.drop_table(name)


def downgrade() -> None:
    tables = _tables()
    if "neuro_targets" not in tables:
        op.create_table(
            "neuro_targets",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column(
                "owner_id",
                sa.Integer(),
                sa.ForeignKey("users.id"),
                nullable=False,
                index=True,
            ),
            sa.Column("channel", sa.String(length=200), nullable=False),
            sa.Column("status", sa.String(length=30), server_default="active"),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        )
    if "neuro_runs" not in tables:
        op.create_table(
            "neuro_runs",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column(
                "owner_id",
                sa.Integer(),
                sa.ForeignKey("users.id"),
                nullable=False,
                index=True,
            ),
            sa.Column("target_id", sa.Integer(), sa.ForeignKey("neuro_targets.id")),
            sa.Column("status", sa.String(length=30), server_default="queued"),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        )
    if "neuro_comments" not in tables:
        op.create_table(
            "neuro_comments",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column(
                "owner_id",
                sa.Integer(),
                sa.ForeignKey("users.id"),
                nullable=False,
                index=True,
            ),
            sa.Column("run_id", sa.Integer(), sa.ForeignKey("neuro_runs.id")),
            sa.Column("text", sa.Text(), server_default=""),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        )

    if "topic_runs" in _tables():
        # substr вместо left: работает и в SQLite, и в PostgreSQL.
        op.execute("UPDATE topic_runs SET region = substr(region, 1, 8)")
        with op.batch_alter_table("topic_runs") as batch:
            batch.alter_column(
                "region",
                existing_type=sa.String(length=64),
                type_=sa.String(length=8),
                existing_nullable=True,
            )

    if "images" in _columns("offers"):
        op.drop_column("offers", "images")
