#!/bin/bash
# Деплой прода. Ключевой порядок: ДАМП БАЗЫ -> сборка/миграции -> проверка.
#
# Почему дамп обязателен и именно первым: alembic-даунгрейды в этом проекте
# осознанно теряют данные (см. AI_HANDOFF.md), поэтому «откатить миграцию» —
# это не rollback, а восстановление из дампа. Нет дампа — нет отката.
#
# Формат дампа — pg_dump -Fc (custom), а не plain SQL через пайп: у пайпа
# `pg_dump | gzip` код возврата берёт gzip, и упавший на середине pg_dump
# давал бы «валидный» архив нужного размера. Custom-формат проверяется
# насквозь оглавлением (pg_restore -l читает и валидирует весь архив),
# он уже сжат, и восстанавливается тем же pg_restore.
#
# Запуск: ./deploy/deploy-production.sh   (из корня приложения на сервере)
# Переопределения: ZAPP_ROOT, HEALTH_URL.
set -euo pipefail

ROOT="${ZAPP_ROOT:-/home/aidar/zapp}"
HEALTH_URL="${HEALTH_URL:-https://talaria.mooo.com/api/health}"
COMPOSE="docker compose -f docker-compose.yml -f docker-compose.prod.yml"
STAMP="$(date +%Y%m%d-%H%M%S)"
BACKUP_DIR="$ROOT/backups/deploy"
LOCK="/tmp/zapp-deploy.lock"

cd "$ROOT"

# Один деплой за раз: параллельный запуск (человек + CI, двойной Enter)
# рвал бы контейнеры друг у друга посреди сборки.
exec 9>"$LOCK"
flock -n 9 || { echo "Деплой уже выполняется (лок $LOCK занят)" >&2; exit 1; }

mkdir -p "$BACKUP_DIR"

# Значения БД читаем из .env — те же, что видит docker compose.
POSTGRES_USER="$(sed -n 's/^POSTGRES_USER=//p' .env | tail -1)"
POSTGRES_DB="$(sed -n 's/^POSTGRES_DB=//p' .env | tail -1)"
: "${POSTGRES_USER:=outly}"
: "${POSTGRES_DB:=outly}"

DUMP="$BACKUP_DIR/pre-deploy-$STAMP.dump"

echo "==> Дамп БД перед миграциями ($DUMP)"
$COMPOSE exec -T db pg_dump -Fc -U "$POSTGRES_USER" -d "$POSTGRES_DB" > "$DUMP"
# Валидация дампа: pg_restore -l разбирает оглавление всего архива — обрыв
# на середине здесь падает, в отличие от gzip -t, который проверял только
# целостность СЖАТИЯ, а не полноту содержимого.
$COMPOSE exec -T db pg_restore -l < "$DUMP" >/dev/null
test "$(stat -c%s "$DUMP")" -gt 1024

# Ревизия миграций на момент дампа: rollback-production.sh сверяется с этим
# файлом и отказывается восстанавливать данные под более новый код.
$COMPOSE exec -T backend alembic current \
  > "$BACKUP_DIR/migration-before-$STAMP.txt" 2>/dev/null || true

echo "==> Сборка и перезапуск (backend прогонит alembic upgrade сам)"
# --wait: compose вернётся только когда healthcheck'и зелёные, поэтому
# отдельный цикл опроса состояния не нужен.
$COMPOSE up -d --build --wait backend web-tg frontend

echo "==> Проверка здоровья снаружи"
curl -fsS --max-time 15 "$HEALTH_URL" >/dev/null

$COMPOSE exec -T backend alembic current > "$BACKUP_DIR/migration-after-$STAMP.txt"
echo "==> Готово. Дамп: $DUMP"
echo "    Миграция: $(tr '\n' ' ' < "$BACKUP_DIR/migration-after-$STAMP.txt")"

# Держим последние 10 деплой-дампов, старые чистим — иначе диск кончится.
# *.sql.gz — формат до перехода на -Fc, дочищаем и его.
ls -1t "$BACKUP_DIR"/pre-deploy-*.dump 2>/dev/null | tail -n +11 | xargs -r rm -f
ls -1t "$BACKUP_DIR"/pre-deploy-*.sql.gz 2>/dev/null | tail -n +11 | xargs -r rm -f
