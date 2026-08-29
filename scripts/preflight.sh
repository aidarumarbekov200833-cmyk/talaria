#!/bin/sh
set -eu

ROOT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
ENV_FILE="${1:-$ROOT_DIR/.env}"
MODE="${2:-production}"

failures=0
fail() { printf 'ОШИБКА: %s\n' "$1" >&2; failures=$((failures + 1)); }
ok() { printf 'OK: %s\n' "$1"; }

if [ ! -f "$ENV_FILE" ]; then
  printf 'ОШИБКА: env-файл не найден: %s\n' "$ENV_FILE" >&2
  exit 1
fi

env_value() {
  awk -v wanted="$1" '
    index($0, wanted "=") == 1 { sub("^[^=]*=", ""); found=$0 }
    END { print found }
  ' "$ENV_FILE" | tr -d '\r'
}

required="BOT_TOKEN BOT_USERNAME JWT_SECRET DATABASE_URL POSTGRES_PASSWORD API_ID API_HASH TG_CLIENT_SECRET ADMIN_TELEGRAM_IDS"
if [ "$MODE" = "production" ]; then
  required="$required DOMAIN METRICS_TOKEN"
fi

for key in $required; do
  value="$(env_value "$key")"
  upper="$(printf '%s' "$value" | tr '[:lower:]' '[:upper:]')"
  if [ -z "$value" ] || printf '%s' "$upper" | grep -q 'CHANGE_ME'; then
    fail "$key не заполнен"
  else
    ok "$key задан"
  fi
done

jwt_secret="$(env_value JWT_SECRET)"
tg_secret="$(env_value TG_CLIENT_SECRET)"
db_password="$(env_value POSTGRES_PASSWORD)"
[ ${#jwt_secret} -ge 32 ] || fail "JWT_SECRET должен быть не короче 32 символов"
[ ${#tg_secret} -ge 32 ] || fail "TG_CLIENT_SECRET должен быть не короче 32 символов"
[ "$db_password" != "madiflow" ] && [ "$db_password" != "outly" ] || fail "POSTGRES_PASSWORD не должен совпадать с публичным шаблоном"

if [ "$MODE" = "production" ]; then
  domain="$(env_value DOMAIN)"
  case "$domain" in http://*|https://*|*/*) fail "DOMAIN указывается без протокола и пути" ;; esac
  [ "$(env_value ADMIN_USERNAME_FALLBACK)" = "false" ] || fail "ADMIN_USERNAME_FALLBACK в production должен быть false"
  [ "$(env_value TRUSTED_PROXY_HOPS)" = "2" ] || fail "Для Caddy + nginx задайте TRUSTED_PROXY_HOPS=2"
  case "$(env_value FRONTEND_HTTP_PORT)" in 127.0.0.1:*) ;; *) fail "FRONTEND_HTTP_PORT должен слушать только 127.0.0.1 за Caddy" ;; esac
fi

if [ "${OUTLY_PREFLIGHT_SKIP_DOCKER:-0}" != "1" ]; then
  command -v docker >/dev/null 2>&1 || fail "Docker не установлен"
  if command -v docker >/dev/null 2>&1; then
    docker compose version >/dev/null 2>&1 || fail "Docker Compose v2 недоступен"
  fi
fi

if [ "$failures" -gt 0 ]; then
  printf '\nPreflight: найдено ошибок: %d. Значения секретов не выводились.\n' "$failures" >&2
  exit 1
fi

ok "env прошёл проверку, секретные значения не печатались"
