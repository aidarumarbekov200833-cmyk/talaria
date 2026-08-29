#!/bin/sh
# Восстановление базы из дампа.
#
# Бэкап, который ни разу не восстанавливали, — не бэкап, а надежда. Этот
# скрипт нужен, чтобы проверка была одной командой, а не квестом.
#
# Проверка без риска (рекомендуется раз в месяц) — развернуть в отдельную БД:
#   ./scripts/restore.sh backups/outly-20260803-051142.sql.gz outly_restore_test
#
# Боевое восстановление (УДАЛЯЕТ текущую базу, спросит подтверждение):
#   ./scripts/restore.sh backups/outly-20260803-051142.sql.gz outly

set -eu

DUMP="${1:-}"
TARGET_DB="${2:-outly_restore_test}"
COMPOSE="${COMPOSE:-docker compose}"
PGUSER_="${POSTGRES_USER:-outly}"

if [ -z "$DUMP" ]; then
  echo "Использование: $0 <файл.sql.gz> [имя_базы]" >&2
  echo "Доступные дампы:" >&2
ls -1t backups/outly-*.sql.gz backups/madiflow-*.sql.gz 2>/dev/null | head -10 >&2 || echo "  (нет)" >&2
  exit 1
fi

if [ ! -f "$DUMP" ]; then
  echo "Файл не найден: $DUMP" >&2
  exit 1
fi

if ! gzip -t "$DUMP"; then
  echo "Архив битый: $DUMP" >&2
  exit 1
fi

if [ "$TARGET_DB" = "${POSTGRES_DB:-outly}" ]; then
  printf '\033[31mВНИМАНИЕ\033[0m: база "%s" будет УДАЛЕНА и создана заново.\n' "$TARGET_DB"
  printf 'Напечатайте имя базы для подтверждения: '
  read -r confirm
  [ "$confirm" = "$TARGET_DB" ] || { echo "Отменено."; exit 1; }

  # Перед боевым восстановлением гасим бэкенд: иначе он держит соединения
  # и DROP DATABASE не пройдёт.
  $COMPOSE stop backend
fi

echo "Восстановление $DUMP -> база $TARGET_DB"

$COMPOSE exec -T db psql -U "$PGUSER_" -d postgres \
  -c "DROP DATABASE IF EXISTS \"$TARGET_DB\" WITH (FORCE);" \
  -c "CREATE DATABASE \"$TARGET_DB\" OWNER \"$PGUSER_\";"

gunzip -c "$DUMP" | $COMPOSE exec -T db psql -U "$PGUSER_" -d "$TARGET_DB" -v ON_ERROR_STOP=1 -q

echo "Готово. Проверка содержимого:"
$COMPOSE exec -T db psql -U "$PGUSER_" -d "$TARGET_DB" -c "
  SELECT 'users' AS таблица, count(*) AS строк FROM users
  UNION ALL SELECT 'leads', count(*) FROM leads
  UNION ALL SELECT 'campaigns', count(*) FROM campaigns
  UNION ALL SELECT 'offers', count(*) FROM offers;"

if [ "$TARGET_DB" = "${POSTGRES_DB:-outly}" ]; then
  $COMPOSE start backend
  echo "Бэкенд запущен обратно."
else
  echo "Это была тестовая база. Удалить: $COMPOSE exec -T db psql -U $PGUSER_ -d postgres -c 'DROP DATABASE \"$TARGET_DB\";'"
fi
