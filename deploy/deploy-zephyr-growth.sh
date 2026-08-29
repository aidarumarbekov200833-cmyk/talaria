#!/bin/sh
set -eu

ROOT=/home/aidar/zapp
RELEASE=/home/aidar/releases/zephyr-growth-20260811-final
PACKAGE=/home/aidar/zephyr-growth-20260811.tar.gz
COMPOSE="docker compose -f docker-compose.yml -f docker-compose.prod.yml"

test -s "$RELEASE/source-before.tar.gz"
test -s "$RELEASE/database-before.dump"
test -s "$PACKAGE"

cd "$ROOT"
cp "$PACKAGE" "$RELEASE/source-modified.tar.gz"
sha256sum "$RELEASE/source-modified.tar.gz" > "$RELEASE/source-modified.sha256"
tar -xzf "$PACKAGE" -C "$ROOT"

sha256sum .env > "$RELEASE/env.sha256.after-extract"
cmp "$RELEASE/env.sha256.before" "$RELEASE/env.sha256.after-extract"

$COMPOSE up -d --build backend web-tg frontend

attempt=0
all_healthy=false
while [ "$attempt" -lt 45 ]; do
  all_healthy=true
  for service in backend web-tg frontend; do
    container_id=$($COMPOSE ps -q "$service")
    health=$(docker inspect --format '{{.State.Health.Status}}' "$container_id")
    [ "$health" = "healthy" ] || all_healthy=false
  done
  [ "$all_healthy" = "true" ] && break
  attempt=$((attempt + 1))
  sleep 2
done
[ "$all_healthy" = "true" ]

$COMPOSE exec -T backend alembic current > "$RELEASE/migration.after.txt"
$COMPOSE exec -T backend python -c "from app.database import SessionLocal; from app import models; db=SessionLocal(); pairs=[('users',models.User),('databases',models.LeadDatabase),('leads',models.Lead),('accounts',models.WarmupAccount),('subscriptions',models.Subscription),('campaign_sends',models.CampaignSend)]; print(' '.join(f'{name}={db.query(model).count()}' for name,model in pairs)); db.close()" > "$RELEASE/data-counts.after.txt"
$COMPOSE exec -T web-tg sh -lc "find /app/sessions -maxdepth 1 -type f -print0 | sort -z | xargs -0 -r sha256sum" > "$RELEASE/sessions.sha256.after"
sha256sum .env > "$RELEASE/env.sha256.after"

cmp "$RELEASE/env.sha256.before" "$RELEASE/env.sha256.after"
cmp "$RELEASE/data-counts.before.txt" "$RELEASE/data-counts.after.txt"
cmp "$RELEASE/sessions.sha256.before" "$RELEASE/sessions.sha256.after"

curl -fsS https://zapp.westus2.cloudapp.azure.com/api/health > "$RELEASE/health.after.json"
curl -fsS https://zapp.westus2.cloudapp.azure.com/api/plans > "$RELEASE/plans.after.json"
$COMPOSE ps > "$RELEASE/compose.after.txt"
$COMPOSE logs --since=10m backend web-tg frontend > "$RELEASE/logs.after.txt" 2>&1

echo "migration=$(tr '\n' ' ' < "$RELEASE/migration.after.txt")"
echo "counts=$(cat "$RELEASE/data-counts.after.txt")"
echo "health=$(cat "$RELEASE/health.after.json")"
echo "data_preserved=true"
