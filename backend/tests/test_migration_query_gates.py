"""Additive schema migration and real SQL statement-budget gates."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from sqlalchemy import create_engine, inspect, text

from app.models import CampaignSend, Lead

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = REPOSITORY_ROOT / "backend"
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from scripts.contract_gate import capture_alembic  # noqa: E402

CAMPAIGN_INDEX = "ix_campaign_sends_owner_account_status_sent_at"
LEAD_INDEX = "ix_leads_tg_verified_account_checked_at"


def _sqlite_url(path: Path) -> str:
    return f"sqlite:///{path.resolve().as_posix()}"


def _run_alembic(database_path: Path, *args: str) -> None:
    env = os.environ.copy()
    env["DATABASE_URL"] = _sqlite_url(database_path)
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "-c", "alembic.ini", *args],
        cwd=BACKEND_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"


def _schema(database_path: Path) -> tuple[set[str], dict[str, set[str]]]:
    engine = create_engine(_sqlite_url(database_path))
    try:
        inspector = inspect(engine)
        lead_columns = {column["name"] for column in inspector.get_columns("leads")}
        indexes = {
            table: {index["name"] for index in inspector.get_indexes(table)}
            for table in ("leads", "campaign_sends")
        }
        return lead_columns, indexes
    finally:
        engine.dispose()


def test_migrations_have_single_head_with_exact_indexes_and_model_parity() -> None:
    """Голова ровно одна.

    Проверка именно на «одну голову», а не на конкретный номер: иначе тест
    приходится править при каждой миграции, и смысл (нет расходящихся веток)
    теряется за обновлением константы. Актуальная голова — 0041: колонка ключей
    YouTube под шифртекст; до неё 0040 — индекс дедупликации, 0039 — история
    отправок (campaign_sends.campaign_id nullable) переживает удаление кампании.
    """
    captured = capture_alembic(BACKEND_ROOT / "alembic" / "versions")
    assert len(captured["heads"]) == 1, f"расходящиеся ветки миграций: {captured['heads']}"
    assert captured["heads"] == ["0041"]
    assert captured["indexes"].count(CAMPAIGN_INDEX) == 1
    assert captured["indexes"].count(LEAD_INDEX) == 1

    campaign_indexes = {index.name: tuple(column.name for column in index.columns) for index in CampaignSend.__table__.indexes}
    lead_indexes = {index.name: tuple(column.name for column in index.columns) for index in Lead.__table__.indexes}
    assert campaign_indexes[CAMPAIGN_INDEX] == ("owner_id", "account_id", "status", "sent_at")
    assert lead_indexes[LEAD_INDEX] == ("tg_verified_by_account_id", "tg_checked_at")
    assert not ({"score", "reply", "replied_at"} & {part for name in (CAMPAIGN_INDEX, LEAD_INDEX) for part in name.split("_")})


def test_0022_upgrade_and_downgrade_preserve_0021_column_and_data(tmp_path: Path) -> None:
    database_path = tmp_path / "upgrade.sqlite3"
    _run_alembic(database_path, "upgrade", "0021")
    before_columns, before_indexes = _schema(database_path)
    assert "tg_checked_at" in before_columns
    assert CAMPAIGN_INDEX not in before_indexes["campaign_sends"]
    assert LEAD_INDEX not in before_indexes["leads"]

    engine = create_engine(_sqlite_url(database_path))
    with engine.begin() as connection:
        user_id = connection.execute(
            text(
                "INSERT INTO users (telegram_id, username, created_at) "
                "VALUES (910001, 'migration-user', CURRENT_TIMESTAMP)"
            )
        ).lastrowid
        database_id = connection.execute(
            text(
                "INSERT INTO lead_databases (owner_id, name, created_at) "
                "VALUES (:owner_id, 'migration-db', CURRENT_TIMESTAMP)"
            ),
            {"owner_id": user_id},
        ).lastrowid
        lead_id = connection.execute(
            text(
                "INSERT INTO leads (database_id, channel_id, title, created_at) "
                "VALUES (:database_id, 'migration-channel', 'preserved', CURRENT_TIMESTAMP)"
            ),
            {"database_id": database_id},
        ).lastrowid
    engine.dispose()

    _run_alembic(database_path, "upgrade", "0022")
    upgraded_columns, upgraded_indexes = _schema(database_path)
    assert "tg_checked_at" in upgraded_columns
    assert CAMPAIGN_INDEX in upgraded_indexes["campaign_sends"]
    assert LEAD_INDEX in upgraded_indexes["leads"]
    engine = create_engine(_sqlite_url(database_path))
    with engine.connect() as connection:
        assert connection.execute(
            text("SELECT title FROM leads WHERE id = :lead_id"), {"lead_id": lead_id}
        ).scalar_one() == "preserved"
    engine.dispose()

    _run_alembic(database_path, "downgrade", "0021")
    downgraded_columns, downgraded_indexes = _schema(database_path)
    assert "tg_checked_at" in downgraded_columns
    assert CAMPAIGN_INDEX not in downgraded_indexes["campaign_sends"]
    assert LEAD_INDEX not in downgraded_indexes["leads"]


def test_0022_repairs_a_drifted_0021_database_missing_tg_checked_at(tmp_path: Path) -> None:
    database_path = tmp_path / "drifted.sqlite3"
    _run_alembic(database_path, "upgrade", "0021")
    engine = create_engine(_sqlite_url(database_path))
    with engine.begin() as connection:
        connection.execute(text("ALTER TABLE leads DROP COLUMN tg_checked_at"))
    engine.dispose()
    assert "tg_checked_at" not in _schema(database_path)[0]

    _run_alembic(database_path, "upgrade", "0022")
    columns, indexes = _schema(database_path)
    assert "tg_checked_at" in columns
    assert LEAD_INDEX in indexes["leads"]


def test_clean_upgrade_head_has_exact_0022_schema(tmp_path: Path) -> None:
    database_path = tmp_path / "clean.sqlite3"
    _run_alembic(database_path, "upgrade", "head")
    columns, indexes = _schema(database_path)
    assert "tg_checked_at" in columns
    assert CAMPAIGN_INDEX in indexes["campaign_sends"]
    assert LEAD_INDEX in indexes["leads"]
