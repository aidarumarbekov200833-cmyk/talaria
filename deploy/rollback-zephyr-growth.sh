#!/bin/sh
set -eu

ROOT=/home/aidar/zapp
RELEASE=/home/aidar/releases/zephyr-growth-20260811-final
COMPOSE="docker compose -f docker-compose.yml -f docker-compose.prod.yml"
MIGRATION="$ROOT/backend/alembic/versions/20260811_0019_offer_delivery_and_account_safety.py"

test -s "$RELEASE/source-before.tar.gz"
test -s "$RELEASE/database-before.dump"
sha256sum -c "$RELEASE/backups.sha256"

if [ "${1:-}" != "--apply" ]; then
  echo "rollback_verified=true"
  echo "run: $0 --apply"
  exit 0
fi

cd "$ROOT"
$COMPOSE exec -T backend alembic downgrade 0018
tar -xzf "$RELEASE/source-before.tar.gz" -C "$ROOT"
if [ -f "$MIGRATION" ]; then
  rm -f "$MIGRATION"
fi
$COMPOSE up -d --build backend web-tg frontend
$COMPOSE exec -T backend alembic current
echo "rollback_applied=true"
