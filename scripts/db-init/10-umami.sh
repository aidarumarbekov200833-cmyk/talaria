#!/bin/sh
# Роль и база для Umami (аналитика). Отдельная роль намеренно: у аналитики нет
# доступа к данным приложения, даже если её однажды взломают.
#
# Запускается автоматически образом postgres при ПЕРВОЙ инициализации тома
# (/docker-entrypoint-initdb.d), то есть на новой машине после восстановления.
# Раньше роль создавали руками, и чек-лист переезда об этом молчал — Umami на
# свежем сервере падал с «role umami does not exist». Идемпотентен: его же
# можно выполнить на живой базе:
#   docker compose exec -T db sh /docker-entrypoint-initdb.d/10-umami.sh
set -eu

if [ -z "${UMAMI_DB_PASSWORD:-}" ]; then
  echo "10-umami: UMAMI_DB_PASSWORD не задан — роль и база Umami не созданы" >&2
  exit 0
fi

U="${UMAMI_DB_USER:-umami}"
D="${UMAMI_DB_NAME:-umami}"
# Одинарная кавычка в пароле удваивается — иначе SQL-литерал разорвётся.
P="$(printf '%s' "$UMAMI_DB_PASSWORD" | sed "s/'/''/g")"

psql -v ON_ERROR_STOP=1 -U "${POSTGRES_USER:-outly}" -d postgres -q <<SQL
DO \$\$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '$U') THEN
    EXECUTE format('CREATE ROLE %I LOGIN PASSWORD %L', '$U', '$P');
  ELSE
    EXECUTE format('ALTER ROLE %I WITH LOGIN PASSWORD %L', '$U', '$P');
  END IF;
END
\$\$;
SQL

if ! psql -U "${POSTGRES_USER:-outly}" -d postgres -Atc "SELECT 1 FROM pg_database WHERE datname = '$D'" | grep -q 1; then
  psql -v ON_ERROR_STOP=1 -U "${POSTGRES_USER:-outly}" -d postgres -q -c "CREATE DATABASE \"$D\" OWNER \"$U\""
  echo "10-umami: создана база $D для роли $U"
else
  echo "10-umami: база $D уже есть, роль $U синхронизирована"
fi
