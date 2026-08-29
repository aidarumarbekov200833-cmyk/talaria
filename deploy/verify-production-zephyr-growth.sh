#!/bin/sh
set -eu

ROOT=/home/aidar/zapp
RELEASE=/home/aidar/releases/zephyr-growth-20260811-final
BASE=https://zapp.westus2.cloudapp.azure.com
COMPOSE="docker compose -f docker-compose.yml -f docker-compose.prod.yml"

cd "$ROOT"
$COMPOSE ps
$COMPOSE exec -T backend alembic current

echo "health=$(curl -fsS "$BASE/api/health")"
echo "plans=$(curl -fsS "$BASE/api/plans")"
echo "quote=$(curl -fsS -X POST -H 'Content-Type: application/json' --data '{"plan_slug":"zephyr","period_months":3,"promo_code":"ZEPHYR30","currency":"RUB"}' "$BASE/api/plans/quote")"
echo "unauth_warmup=$(curl -sS -o /dev/null -w '%{http_code}' "$BASE/api/warmup")"
echo "unauth_offer_stats=$(curl -sS -o /dev/null -w '%{http_code}' "$BASE/api/campaigns/offer-stats")"
echo "csp=$(curl -fsSI "$BASE/" | tr -d '\r' | grep -i '^content-security-policy:' | head -n 1)"

cmp "$RELEASE/env.sha256.before" "$RELEASE/env.sha256.after"
cmp "$RELEASE/data-counts.before.txt" "$RELEASE/data-counts.after.txt"
cmp "$RELEASE/sessions.sha256.before" "$RELEASE/sessions.sha256.after"

if grep -E '"level": "(ERROR|CRITICAL)"|Traceback|Unhandled|FATAL' "$RELEASE/logs.after.txt"; then
  echo "runtime_errors_found=true" >&2
  exit 3
fi

echo "preservation_checks=ok"
echo "runtime_errors=none"
