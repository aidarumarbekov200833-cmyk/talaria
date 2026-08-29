#!/bin/sh
set -eu

ROOT=/home/aidar/zapp
RELEASE=/home/aidar/releases/zephyr-growth-20260811-final
COMPOSE="docker compose -f docker-compose.yml -f docker-compose.prod.yml"

if [ -e "$RELEASE" ]; then
  echo "release_exists=$RELEASE" >&2
  exit 2
fi

mkdir -p "$RELEASE"
cd "$ROOT"

sha256sum .env > "$RELEASE/env.sha256.before"
$COMPOSE ps > "$RELEASE/compose.before.txt"
$COMPOSE exec -T backend alembic current > "$RELEASE/migration.before.txt"
$COMPOSE exec -T backend python -c "from app.database import SessionLocal; from app import models; db=SessionLocal(); pairs=[('users',models.User),('databases',models.LeadDatabase),('leads',models.Lead),('accounts',models.WarmupAccount),('subscriptions',models.Subscription),('campaign_sends',models.CampaignSend)]; print(' '.join(f'{name}={db.query(model).count()}' for name,model in pairs)); db.close()" > "$RELEASE/data-counts.before.txt"
$COMPOSE exec -T web-tg sh -lc "find /app/sessions -maxdepth 1 -type f -print0 | sort -z | xargs -0 -r sha256sum" > "$RELEASE/sessions.sha256.before"

tar \
  --exclude='.env' \
  --exclude='.git' \
  --exclude='.outly-change' \
  --exclude='.outly-rollback/python-libs' \
  --exclude='backups' \
  --exclude='release' \
  --exclude='*/__pycache__' \
  --exclude='*.pyc' \
  --exclude='node_modules' \
  -czf "$RELEASE/source-before.tar.gz" -C "$ROOT" .

$COMPOSE exec -T db sh -lc 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc' > "$RELEASE/database-before.dump"
$COMPOSE exec -T db sh -lc 'pg_restore --list' < "$RELEASE/database-before.dump" > "$RELEASE/database-before.list"
sha256sum "$RELEASE/source-before.tar.gz" "$RELEASE/database-before.dump" > "$RELEASE/backups.sha256"

echo "release=$RELEASE"
cat "$RELEASE/data-counts.before.txt"
cat "$RELEASE/migration.before.txt"
cat "$RELEASE/backups.sha256"
