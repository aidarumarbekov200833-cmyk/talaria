#!/bin/sh
# Деплой прода. Ключевой порядок: ДАМП БАЗЫ -> сборка/миграции -> проверка.
#
# Почему дамп обязателен и именно первым: alembic-даунгрейды в этом проекте
# осознанно теряют данные (см. AI_HANDOFF.md), поэтому «откатить миграцию» —
# это не rollback, а восстановление из дампа. Нет дампа — нет отката.
#
# Запуск: ./deploy/deploy-production.sh   (из корня приложения на сервере)
# Переопределения: ZAPP_ROOT, HEALTH_URL.
set -eu

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

echo "==> Дамп БД перед миграциями"
$COMPOSE exec -T db pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" \
  | gzip > "$BACKUP_DIR/pre-deploy-$STAMP.sql.gz"
# Пустой или оборвавшийся дамп = деплой не начинаем.
gzip -t "$BACKUP_DIR/pre-deploy-$STAMP.sql.gz"
test "$(stat -c%s "$BACKUP_DIR/pre-deploy-$STAMP.sql.gz")" -gt 1024

$COMPOSE exec -T backend alembic current \
  > "$BACKUP_DIR/migration-before-$STAMP.txt" 2>/dev/null || true

echo "==> Сборка и перезапуск (backend прогонит alembic upgrade сам)"
# --wait: compose вернётся только когда healthcheck'и зелёные, поэтому
# отдельный цикл опроса состояния не нужен.
$COMPOSE up -d --build --wait backend web-tg frontend

echo "==> Проверка здоровья снаружи"
curl -fsS --max-time 15 "$HEALTH_URL" >/dev/null

$COMPOSE exec -T backend alembic current > "$BACKUP_DIR/migration-after-$STAMP.txt"
echo "==> Готово. Дамп: $BACKUP_DIR/pre-deploy-$STAMP.sql.gz"
echo "    Миграция: $(tr '\n' ' ' < "$BACKUP_DIR/migration-after-$STAMP.txt")"

# Держим последние 10 деплой-дампов, старые чистим — иначе диск кончится.
ls -1t "$BACKUP_DIR"/pre-deploy-*.sql.gz 2>/dev/null | tail -n +11 | xargs -r rm -f
