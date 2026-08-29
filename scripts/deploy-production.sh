#!/bin/sh
set -eu

ROOT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
cd "$ROOT_DIR"

sh scripts/preflight.sh .env production

compose() {
  docker compose --env-file .env -f docker-compose.yml -f docker-compose.prod.yml --profile tls "$@"
}

compose config --quiet
compose up -d --build --remove-orphans
compose exec -T backend alembic current
compose ps

domain="$(awk 'index($0,"DOMAIN=")==1{sub("^[^=]*=",""); value=$0} END{print value}' .env | tr -d '\r')"
printf '\nOutly развёрнут: https://%s\n' "$domain"
printf 'Проверка здоровья: curl -fsS https://%s/api/health\n' "$domain"
