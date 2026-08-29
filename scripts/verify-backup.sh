#!/bin/sh
# Реальная проверка восстановления последнего дампа в отдельную временную БД.
# Production-база не останавливается и не изменяется. Успешный gzip сам по себе
# недостаточен: только psql с ON_ERROR_STOP доказывает, что дамп разворачивается.

set -eu
umask 077

BACKUP_DIR="${BACKUP_DIR:-/backups}"
VERIFY_DB="${BACKUP_VERIFY_DB:-outly_restore_verify}"
INTERVAL_DAYS="${BACKUP_VERIFY_INTERVAL_DAYS:-7}"

log() { echo "[backup-verify $(date -u +%Y-%m-%dT%H:%M:%SZ)] $*"; }

case "$VERIFY_DB" in
  *[!A-Za-z0-9_]*|'') log "Некорректное имя временной БД" >&2; exit 1 ;;
esac
if [ "$VERIFY_DB" = "${PGDATABASE:-outly}" ]; then
  log "Временная БД не может совпадать с production" >&2
  exit 1
fi

cleanup() {
  dropdb --if-exists --force "$VERIFY_DB" >/dev/null 2>&1 || true
}
trap cleanup EXIT INT TERM

verify_latest() {
  latest="$(find "$BACKUP_DIR" -maxdepth 1 -type f -name 'outly-*.sql.gz' | sort | tail -1)"
  if [ -z "$latest" ] || [ ! -f "$latest" ]; then
    log "Нет дампа для проверки" >&2
    return 1
  fi

  checksum="$latest.sha256"
  if [ ! -s "$checksum" ]; then
    log "Нет SHA-256 для $(basename "$latest")" >&2
    return 1
  fi
  if ! (cd "$BACKUP_DIR" && sha256sum -c "$(basename "$checksum")" >/dev/null); then
    log "SHA-256 не совпал для $(basename "$latest")" >&2
    return 1
  fi
  if ! gzip -t "$latest" 2>/dev/null; then
    log "gzip повреждён: $(basename "$latest")" >&2
    return 1
  fi

  cleanup
  createdb --template=template0 "$VERIFY_DB"
  if ! gzip -dc "$latest" | psql -X --set ON_ERROR_STOP=on -q -d "$VERIFY_DB"; then
    log "Восстановление завершилось ошибкой" >&2
    cleanup
    return 1
  fi

  missing="$(psql -X -Atq -d "$VERIFY_DB" -c "
    SELECT string_agg(name, ',') FROM (VALUES
      ('users'),('lead_databases'),('leads'),('offers'),('campaigns'),
      ('warmup_accounts'),('campaign_sends')
    ) AS required(name)
    WHERE to_regclass('public.' || name) IS NULL;")"
  if [ -n "$missing" ]; then
    log "В восстановленной БД нет таблиц: $missing" >&2
    cleanup
    return 1
  fi

  summary="$(psql -X -Atq -d "$VERIFY_DB" -c "
    SELECT 'users=' || (SELECT count(*) FROM users) ||
           ' lead_databases=' || (SELECT count(*) FROM lead_databases) ||
           ' leads=' || (SELECT count(*) FROM leads) ||
           ' offers=' || (SELECT count(*) FROM offers) ||
           ' campaigns=' || (SELECT count(*) FROM campaigns);")"
  cleanup

  printf '%s\n' "$summary" > "$BACKUP_DIR/.last-restore-summary.part"
  mv "$BACKUP_DIR/.last-restore-summary.part" "$BACKUP_DIR/.last-restore-summary"
  date -u +%Y-%m-%dT%H:%M:%SZ > "$BACKUP_DIR/.last-restore-ok.part"
  mv "$BACKUP_DIR/.last-restore-ok.part" "$BACKUP_DIR/.last-restore-ok"
  date -u +%s > "$BACKUP_DIR/.last-restore-ok.epoch.part"
  mv "$BACKUP_DIR/.last-restore-ok.epoch.part" "$BACKUP_DIR/.last-restore-ok.epoch"
  chmod 0644 "$BACKUP_DIR/.last-restore-summary" "$BACKUP_DIR/.last-restore-ok" "$BACKUP_DIR/.last-restore-ok.epoch"
  log "Успешно восстановлен $(basename "$latest"): $summary"
}

mkdir -p "$BACKUP_DIR"
log "Старт. Проверка восстановления каждые $INTERVAL_DAYS дней."
while true; do
  now="$(date -u +%s)"
  last=0
  if [ -s "$BACKUP_DIR/.last-restore-ok.epoch" ]; then
    last="$(cat "$BACKUP_DIR/.last-restore-ok.epoch" 2>/dev/null || echo 0)"
  fi
  case "$last" in *[!0-9]*|'') last=0 ;; esac
  if [ "$((now - last))" -ge "$((INTERVAL_DAYS * 86400))" ]; then
    verify_latest || log "Проверка не прошла; повтор через час" >&2
  fi
  sleep 3600
done
