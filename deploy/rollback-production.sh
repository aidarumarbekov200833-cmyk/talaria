#!/bin/sh
# Откат прода к дампу, снятому deploy-production.sh.
#
# Это восстановление ДАННЫХ, а не «alembic downgrade»: даунгрейды в проекте
# теряют данные, поэтому откатываемся заменой БД на состояние до деплоя.
# Код при этом надо вернуть тем же способом, каким деплоили (git checkout
# предыдущего коммита / прошлый архив) — скрипт напоминает об этом и
# останавливается, если backend не поднялся.
#
# Запуск: ./deploy/rollback-production.sh backups/deploy/pre-deploy-XXXX.sql.gz
set -eu

ROOT="${ZAPP_ROOT:-/home/aidar/zapp}"
COMPOSE="docker compose -f docker-compose.yml -f docker-compose.prod.yml"
DUMP="${1:?укажите путь к дампу: rollback-production.sh backups/deploy/pre-deploy-XXXX.sql.gz}"

cd "$ROOT"
test -s "$DUMP"
gzip -t "$DUMP"

POSTGRES_USER="$(sed -n 's/^POSTGRES_USER=//p' .env | tail -1)"
POSTGRES_DB="$(sed -n 's/^POSTGRES_DB=//p' .env | tail -1)"
: "${POSTGRES_USER:=outly}"
: "${POSTGRES_DB:=outly}"

echo "ВНИМАНИЕ: база $POSTGRES_DB будет заменена содержимым $DUMP."
echo "Убедитесь, что код уже возвращён к версии, соответствующей дампу."
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
gunzip -c "$DUMP" | $COMPOSE exec -T db psql -q -U "$POSTGRES_USER" -d "${POSTGRES_DB}_rollback_tmp"

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
