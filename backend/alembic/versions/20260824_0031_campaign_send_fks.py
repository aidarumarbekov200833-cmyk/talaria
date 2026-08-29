"""Недостающие внешние ключи campaign_sends → offers и warmup_accounts.

Revision ID: 0031
Revises: 0030

Колонки offer_id и account_id появились в 0011 обычными Integer, без внешнего
ключа, хотя модель объявляет ForeignKey(..., ondelete="SET NULL"). Тесты этого
не видят: схема для них строится из моделей, а не миграциями, поэтому в тестах
связь есть, а в рабочей базе её нет.

Разница проявляется при удалении оффера или аккаунта: вместо NULL в истории
отправок остаётся ссылка на несуществующую строку. Отчёт «какой оффер даёт
ответы» продолжает считать отправки на удалённый оффер, а маршрутизация ответов
ищет аккаунт, которого нет.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0031"
down_revision = "0030"
branch_labels = None
depends_on = None

FKS = (
    ("fk_campaign_sends_offer_id", "offer_id", "offers"),
    ("fk_campaign_sends_account_id", "account_id", "warmup_accounts"),
)


def _inspector() -> sa.Inspector:
    return sa.inspect(op.get_bind())


def _tables() -> set[str]:
    return set(_inspector().get_table_names())


def _fk_names(table: str) -> set[str]:
    return {fk["name"] for fk in _inspector().get_foreign_keys(table) if fk.get("name")}


def upgrade() -> None:
    tables = _tables()
    if not {"campaign_sends", "offers", "warmup_accounts"} <= tables:
        return

    # SQLite не умеет добавлять внешний ключ к существующей таблице, а batch-режим
    # пересобрал бы таблицу целиком. Рабочая база — PostgreSQL; на SQLite (локальные
    # прогоны и старые проверки миграций) шаг пропускается осознанно.
    if op.get_bind().dialect.name == "sqlite":
        return

    existing = _fk_names("campaign_sends")

    # Висячие ссылки нужно обнулить до создания ключа, иначе PostgreSQL откажется
    # его создавать и обновление упадёт на середине. Строки при этом сохраняются:
    # факт отправки важнее, чем то, каким оффером она была сделана.
    for _name, column, target in FKS:
        op.execute(
            sa.text(
                f"UPDATE campaign_sends SET {column} = NULL "
                f"WHERE {column} IS NOT NULL "
                f"AND {column} NOT IN (SELECT id FROM {target})"
            )
        )

    for name, column, target in FKS:
        if name in existing:
            continue
        op.create_foreign_key(
            name,
            "campaign_sends",
            target,
            [column],
            ["id"],
            ondelete="SET NULL",
        )


def downgrade() -> None:
    if "campaign_sends" not in _tables():
        return
    if op.get_bind().dialect.name == "sqlite":
        return
    existing = _fk_names("campaign_sends")
    for name, _column, _target in FKS:
        if name in existing:
            op.drop_constraint(name, "campaign_sends", type_="foreignkey")
