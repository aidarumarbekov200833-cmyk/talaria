#!/bin/bash
# Откат прода к дампу, снятому deploy-production.sh.
#
# Это восстановление ДАННЫХ, а не «alembic downgrade»: даунгрейды в проекте
# теряют данные, поэтому откатываемся заменой БД на состояние до деплоя.
# Код надо вернуть тем же способом, каким деплоили (git checkout предыдущего
# коммита / прошлый архив) — и скрипт это ПРОВЕРЯЕТ: если голова миграций в
# текущем образе backend новее ревизии на момент дампа, восстановление
# отклоняется. Иначе backend при старте сам прогнал бы alembic upgrade head
# и немедленно вернул ту миграцию, от которой мы только что откатились.
# Осознанно перекрыть проверку: FORCE_ROLLBACK=1 (например, миграция
# безобидная и нужен только откат данных).
#
# Запуск: ./deploy/rollback-production.sh backups/deploy/pre-deploy-XXXX.dump
set -euo pipefail

ROOT="${ZAPP_ROOT:-/home/aidar/zapp}"
COMPOSE="docker compose -f docker-compose.yml -f docker-compose.prod.yml"
DUMP="${1:?укажите путь к дампу: rollback-production.sh backups/deploy/pre-deploy-XXXX.dump}"

cd "$ROOT"
test -s "$DUMP"

case "$DUMP" in
  *.sql.gz)
    echo "Дамп старого формата (plain SQL). Восстановите его руками через psql" >&2
    echo "с -v ON_ERROR_STOP=1 --single-transaction; скрипт работает с -Fc." >&2
    exit 1
    ;;
esac

POSTGRES_USER="$(sed -n 's/^POSTGRES_USER=//p' .env | tail -1)"
POSTGRES_DB="$(sed -n 's/^POSTGRES_DB=//p' .env | tail -1)"
: "${POSTGRES_USER:=outly}"
: "${POSTGRES_DB:=outly}"

# Валидация архива до любых разрушающих действий.
$COMPOSE exec -T db pg_restore -l < "$DUMP" >/dev/null

# --- Гард от «восстановили данные под новый код» ---------------------------
# deploy-production.sh кладёт рядом с дампом migration-before-<STAMP>.txt.
# Сравниваем её с головой миграций в ТЕКУЩЕМ образе backend: alembic heads
# читает файлы миграций, не БД, поэтому честно показывает, что накатит
# entrypoint при старте.
STAMP="$(basename "$DUMP" | sed -n 's/^pre-deploy-\(.*\)\.dump$/\1/p')"
BEFORE_FILE="$(dirname "$DUMP")/migration-before-$STAMP.txt"
if [ "${FORCE_ROLLBACK:-0}" != "1" ]; then
  DUMP_REV="$(grep -oE '^[0-9a-f]+' "$BEFORE_FILE" 2>/dev/null | head -1 || true)"
  CODE_HEAD="$($COMPOSE run --rm --no-deps --entrypoint alembic backend heads 2>/dev/null \
    | grep -oE '^[0-9a-f]+' | head -1 || true)"
  if [ -z "$DUMP_REV" ] || [ -z "$CODE_HEAD" ]; then
    echo "Не удалось сверить ревизии миграций (дамп: '${DUMP_REV:-?}', код: '${CODE_HEAD:-?}')." >&2
    echo "Файл $BEFORE_FILE отсутствует или образ backend не отвечает." >&2
    echo "Проверьте руками и перезапустите с FORCE_ROLLBACK=1." >&2
    exit 1
  fi
  if [ "$DUMP_REV" != "$CODE_HEAD" ]; then
    echo "ОТКАЗ: дамп снят на ревизии $DUMP_REV, а код в образе несёт $CODE_HEAD." >&2
    echo "Backend при старте прогонит alembic upgrade head и снова применит" >&2
    echo "миграцию поверх восстановленных данных. Сначала верните код" >&2
    echo "(git checkout / прошлый архив + пересборка), потом откатывайте базу." >&2
    echo "Перекрыть осознанно: FORCE_ROLLBACK=1." >&2
    exit 1
  fi
fi

echo "ВНИМАНИЕ: база $POSTGRES_DB будет заменена содержимым $DUMP."
printf "Продолжить? [yes/N] "
read -r answer
[ "$answer" = "yes" ] || { echo "Отменено"; exit 1; }

# Бэкенд останавливаем, чтобы никто не писал в базу посреди восстановления.
$COMPOSE stop backend web-tg

# Пересоздаём схему целиком: restore поверх живых таблиц оставил бы мусор.
$COMPOSE exec -T db psql -U "$POSTGRES_USER" -d postgres -c \
  "DROP DATABASE IF EXISTS \"${POSTGRES_DB}_rollback_tmp\";"
$COMPOSE exec -T db psql -U "$POSTGRES_USER" -d postgres -c \
  "CREATE DATABASE \"${POSTGRES_DB}_rollback_tmp\";"
# --exit-on-error --single-transaction: любая ошибка восстановления валит весь
# restore целиком и НЕ доходит до подмены БД. Без этого недовосстановленная
# база тихо переименовалась бы поверх продовой.
$COMPOSE exec -T db pg_restore -U "$POSTGRES_USER" -d "${POSTGRES_DB}_rollback_tmp" \
  --exit-on-error --single-transaction --no-owner < "$DUMP"

# Подмена атомарна на уровне имён БД: старую убираем в _broken на разбор.
$COMPOSE exec -T db psql -U "$POSTGRES_USER" -d postgres -c \
  "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = '${POSTGRES_DB}' AND pid <> pg_backend_pid();"
$COMPOSE exec -T db psql -U "$POSTGRES_USER" -d postgres -c \
  "DROP DATABASE IF EXISTS \"${POSTGRES_DB}_broken\";"
$COMPOSE exec -T db psql -U "$POSTGRES_USER" -d postgres -c \
  "ALTER DATABASE \"${POSTGRES_DB}\" RENAME TO \"${POSTGRES_DB}_broken\";"
$COMPOSE exec -T db psql -U "$POSTGRES_USER" -d postgres -c \
  "ALTER DATABASE \"${POSTGRES_DB}_rollback_tmp\" RENAME TO \"${POSTGRES_DB}\";"

$COMPOSE up -d --wait backend web-tg frontend
echo "Готово. Старая база сохранена как ${POSTGRES_DB}_broken (удалить руками после проверки)."
