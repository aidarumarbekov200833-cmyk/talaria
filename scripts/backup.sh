#!/bin/sh
# Регулярный атомарный бэкап PostgreSQL и Telegram-сессий.
# Сервис Docker запускает этот файл напрямую; готовым считается только архив,
# для которого pg_dump завершился без ошибки, gzip прошёл проверку и записан
# SHA-256. Маркеры свежести используются healthcheck и restore-проверкой.

set -eu
umask 077

BACKUP_DIR="${BACKUP_DIR:-/backups}"
SESSION_DIR="${SESSION_BACKUP_SOURCE:-/state/telegram-sessions}"
KEEP_DAYS="${BACKUP_KEEP_DAYS:-30}"
MIN_BYTES="${BACKUP_MIN_BYTES:-256}"
# Как часто снимать копию. Раньше дамп делался раз в календарные сутки, и при
# падении диска терялся весь день работы: собранные базы, отправки, ответы.
# Сжатый дамп весит сотни килобайт, поэтому шесть часов ничего не стоят по
# месту (30 дней хранения — десятки мегабайт), а потери сокращают вчетверо.
INTERVAL_SECONDS="${BACKUP_INTERVAL_SECONDS:-21600}"
# Пауза цикла. Меньше интервала, чтобы сдвиг расписания не накапливался:
# проверяем чаще, а снимаем копию только когда пришло время.
CHECK_SECONDS="${BACKUP_CHECK_SECONDS:-600}"

log() { echo "[backup $(date -u +%Y-%m-%dT%H:%M:%SZ)] $*"; }

write_marker() {
  name="$1"
  date -u +%Y-%m-%dT%H:%M:%SZ > "$BACKUP_DIR/$name.part"
  mv "$BACKUP_DIR/$name.part" "$BACKUP_DIR/$name"
  date -u +%s > "$BACKUP_DIR/$name.epoch.part"
  mv "$BACKUP_DIR/$name.epoch.part" "$BACKUP_DIR/$name.epoch"
  chmod 0644 "$BACKUP_DIR/$name" "$BACKUP_DIR/$name.epoch"
}

write_checksum() {
  file="$1"
  base="$(basename "$file")"
  (cd "$BACKUP_DIR" && sha256sum "$base" > "$base.sha256.part")
  mv "$file.sha256.part" "$file.sha256"
}

make_dump() {
  ts="$(date -u +%Y%m%d-%H%M%S)"
  out="$BACKUP_DIR/outly-$ts.sql.gz"
  sql_tmp="$BACKUP_DIR/.outly-$ts.sql.part"
  gz_tmp="$out.part"

  # Не используем `pg_dump | gzip`: POSIX sh без pipefail может скрыть ошибку
  # pg_dump, если gzip успешно закрыл пустой/обрезанный поток.
  if ! pg_dump --no-owner --no-privileges > "$sql_tmp"; then
    log "ОШИБКА: pg_dump не отработал" >&2
    rm -f "$sql_tmp" "$gz_tmp"
    return 1
  fi
  if ! gzip -9 < "$sql_tmp" > "$gz_tmp"; then
    log "ОШИБКА: gzip не отработал" >&2
    rm -f "$sql_tmp" "$gz_tmp"
    return 1
  fi
  rm -f "$sql_tmp"

  if ! gzip -t "$gz_tmp" 2>/dev/null; then
    log "ОШИБКА: архив БД повреждён" >&2
    rm -f "$gz_tmp"
    return 1
  fi
  size="$(wc -c < "$gz_tmp")"
  if [ "$size" -lt "$MIN_BYTES" ]; then
    log "ОШИБКА: дамп подозрительно маленький ($size байт)" >&2
    rm -f "$gz_tmp"
    return 1
  fi

  mv "$gz_tmp" "$out"
  write_checksum "$out"
  write_marker .last-ok
  log "База сохранена: $out ($size байт)"
}

make_session_backup() {
  [ -d "$SESSION_DIR" ] || return 0
  ts="$(date -u +%Y%m%d-%H%M%S)"
  out="$BACKUP_DIR/outly-telegram-sessions-$ts.tar.gz"
  tmp="$out.part"
  if ! tar -C "$SESSION_DIR" -czf "$tmp" .; then
    log "ОШИБКА: Telegram-сессии не упакованы" >&2
    rm -f "$tmp"
    return 1
  fi
  if ! gzip -t "$tmp" 2>/dev/null; then
    log "ОШИБКА: архив Telegram-сессий повреждён" >&2
    rm -f "$tmp"
    return 1
  fi
  mv "$tmp" "$out"
  write_checksum "$out"
  write_marker .last-sessions-ok
  log "Telegram-сессии сохранены: $out"
}

rotate() {
  find "$BACKUP_DIR" -type f \( \
    -name 'outly-*.sql.gz' -o -name 'outly-*.sql.gz.sha256' -o \
    -name 'madiflow-*.sql.gz' -o -name 'madiflow-*.sql.gz.sha256' -o \
    -name 'outly-telegram-sessions-*.tar.gz' -o \
    -name 'outly-telegram-sessions-*.tar.gz.sha256' \
  \) -mtime "+$KEEP_DAYS" -delete 2>/dev/null || true
  find "$BACKUP_DIR" -type f -name '*.part' -mmin +120 -delete 2>/dev/null || true
}

mkdir -p "$BACKUP_DIR"
log "Старт. Каталог $BACKUP_DIR, хранение $KEEP_DAYS дней."

# Пора ли снимать копию: маркера нет вовсе или он старше интервала.
# Решение по времени последнего УСПЕХА, а не по имени файла с датой: файл
# может лежать от прошлой версии сервиса, а проверенным считается только тот,
# для которого записан маркер.
due() {
  marker="$BACKUP_DIR/$1"
  [ -s "$marker" ] || return 0
  last="$(cat "$marker" 2>/dev/null || echo 0)"
  case "$last" in
    ''|*[!0-9]*) return 0 ;;
  esac
  now="$(date -u +%s)"
  [ "$((now - last))" -ge "$INTERVAL_SECONDS" ]
}

log "Интервал копий: $INTERVAL_SECONDS c, проверка каждые $CHECK_SECONDS c."

while true; do
  if due ".last-ok.epoch"; then
    make_dump || log "Бэкап БД не удался; повтор на следующей проверке" >&2
  fi
  if [ -d "$SESSION_DIR" ] && due ".last-sessions-ok.epoch"; then
    make_session_backup || log "Бэкап Telegram-сессий не удался; повтор позже" >&2
  fi
  rotate
  sleep "$CHECK_SECONDS"
done
