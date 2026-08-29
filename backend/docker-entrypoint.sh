#!/bin/sh
# Точка входа бэкенда: приводим схему БД к актуальной миграции, затем uvicorn.
#
# База может быть в трёх состояниях, и каждое надо обработать:
#   1) пустая (новый том)                 -> alembic upgrade head создаёт всё;
#   2) legacy: таблицы есть, но нет
#      alembic_version (создавались через
#      create_all в старых версиях)        -> alembic stamp 0001, потом upgrade;
#   3) уже под Alembic (есть версия)        -> просто upgrade head.
#
# Решение принимает один Python-скрипт (печатает fresh|legacy|managed), чтобы
# не дёргать интерпретатор несколько раз и не плодить тонкие ошибки в shell.
set -e
cd /app

state=$(python - <<'PY'
import sys
from sqlalchemy import inspect
from app.database import engine

try:
    with engine.connect() as conn:
        names = set(inspect(conn).get_table_names())
except Exception as exc:  # БД ещё не готова или недоступна
    print(f"db-error: {exc}", file=sys.stderr)
    sys.exit(1)

if not names:
    print("fresh")
elif "alembic_version" in names:
    print("managed")
else:
    print("legacy")
PY
)

if [ "$state" = "legacy" ]; then
    # Legacy create_all соответствует baseline 0001. Помечать такую БД как
    # head было ошибкой: Alembic считал 0002–0016 применёнными и не добавлял
    # новые колонки/таблицы, хотя код уже обращался к ним. Поэтому архив поверх
    # старой БД продолжал падать даже после перезапуска контейнера.
    echo "БД из эпохи create_all: помечаем baseline 0001 и накатываем все обновления."
    alembic stamp 0001
fi

echo "Применяем миграции (alembic upgrade head)…"
alembic upgrade head

exec uvicorn app.main:app --host 0.0.0.0 --port 8000
