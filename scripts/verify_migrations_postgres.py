"""Прогнать всю цепочку миграций на настоящем PostgreSQL и сверить её с моделями.

Зачем отдельно от pytest: схема для тестов строится из моделей (`Base.metadata`),
миграции при этом не выполняются. Поэтому расхождение «модель обещает одно, а
рабочая база устроена иначе» тестами не ловится в принципе — оно проявляется
только на сервере. Так были найдены пропавшие внешние ключи campaign_sends.

Что делает:
  1) поднимает временный PostgreSQL (pgserver, без docker);
  2) `alembic upgrade head` с нуля — падение здесь означает, что деплой сломается;
  3) сравнивает получившуюся схему с моделями приложения — расхождений быть не должно;
  4) `alembic downgrade 0024` — проверка, что откат вообще возможен.

Запуск (нужен Python 3.12, у pgserver нет колёс под 3.13):
    uv venv --python 3.12 /tmp/pgtest/.venv
    uv pip install --python /tmp/pgtest/.venv/bin/python pgserver -r backend/requirements.txt
    /tmp/pgtest/.venv/bin/python scripts/verify_migrations_postgres.py

Прогонять перед каждым деплоем, где есть новая миграция.
"""

from __future__ import annotations

import os
import pathlib
import subprocess
import sys
import tempfile

BACKEND = pathlib.Path("/work/temp/tal_site/backend")


def main() -> int:
    import pgserver

    with tempfile.TemporaryDirectory() as tmp:
        db = pgserver.get_server(pathlib.Path(tmp) / "pgdata")
        url = db.get_uri().replace("postgresql://", "postgresql+psycopg2://")
        print(f"postgres: {db.get_uri()}")

        env = dict(os.environ)
        env["DATABASE_URL"] = url
        env.setdefault("TG_CLIENT_SECRET", "x" * 32)
        env.setdefault("JWT_SECRET", "y" * 32)

        up = subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", "head"],
            cwd=BACKEND, env=env, capture_output=True, text=True,
        )
        print(up.stdout[-4000:])
        if up.returncode != 0:
            print("MIGRATION FAILED", file=sys.stderr)
            print(up.stderr[-8000:], file=sys.stderr)
            return 1

        check = subprocess.run(
            [sys.executable, "-c", COMPARE],
            cwd=BACKEND, env=env, capture_output=True, text=True,
        )
        print(check.stdout)
        if check.returncode != 0:
            print(check.stderr[-8000:], file=sys.stderr)
            return 1

        down = subprocess.run(
            [sys.executable, "-m", "alembic", "downgrade", "0024"],
            cwd=BACKEND, env=env, capture_output=True, text=True,
        )
        if down.returncode != 0:
            print("DOWNGRADE FAILED (откат к 0024 невозможен)", file=sys.stderr)
            print(down.stderr[-4000:], file=sys.stderr)
            return 1
        print("downgrade to 0024: ok")
        return 0


COMPARE = """
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import create_engine
from app import models  # noqa: F401
from app.config import settings
from app.database import Base

engine = create_engine(settings.database_url)
with engine.connect() as conn:
    diff = compare_metadata(MigrationContext.configure(conn), Base.metadata)

# 0007 создала на user_sessions.jti и UNIQUE-ограничение, и уникальный индекс;
# в моделях остался только индекс. Дубль обеспечивает то же правило, снимать его
# с рабочей базы незачем.
def benign(item):
    return (
        isinstance(item, tuple)
        and item[0] == "remove_constraint"
        and getattr(item[1], "table", None) is not None
        and item[1].table.name == "user_sessions"
        and {c.name for c in item[1].columns} == {"jti"}
    )


real = [d for d in diff if not benign(d)]
for item in real:
    print("DIFF:", item)
print("total diffs:", len(real))
raise SystemExit(1 if real else 0)
"""


if __name__ == "__main__":
    raise SystemExit(main())
