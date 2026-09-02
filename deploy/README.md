# Production-деплой Zephyr

В корне релизного архива уже лежит `.env`: упаковщик включает локальный файл
целиком, но Dockerfile и `.dockerignore` не дают ему попасть внутрь образов.
Перед передачей архива используйте только защищённый канал — `.env` содержит
серверные ключи.

На чистом Ubuntu/Debian-сервере установите Docker Engine с Compose v2, направьте
DNS A/AAAA домена на сервер и откройте входящие TCP 80/443. Затем:

```bash
unzip outly-deploy.zip -d /opt/outly
cd /opt/outly

# Проверьте и дозаполните .env. Production-шаблон: deploy/.env.production.example
sh scripts/preflight.sh .env production
sh scripts/deploy-production.sh
```

Production override включает read-only root filesystem для приложения,
`no-new-privileges`, tmpfs для runtime-файлов и корректные grace periods.
Caddy получает и продлевает TLS автоматически. PostgreSQL и Telegram gateway
не публикуются наружу; сессии и база лежат в именованных Docker volumes.

После запуска проверьте:

```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml --profile tls ps
curl -fsS https://ВАШ_ДОМЕН/api/health
docker compose -f docker-compose.yml -f docker-compose.prod.yml logs --tail=100 backend
```

Для обновления распакуйте новую версию поверх кода, оставив `.env` и volumes,
сделайте `bash scripts/backup.sh`, затем снова запустите
`sh scripts/deploy-production.sh`. Миграции выполняются entrypoint бэкенда.

## Переезд / восстановление на чистой VM

Порядок проверен по составу бэкапов: дампы БД и архивы Telegram-сессий лежат
в `./backups` (и в off-site restic-репозитории), `.env` и состояние Caddy —
только в restic (`/extra`). Время — около часа, из них половина ждёт DNS.

1. Новая машина: Docker Engine + Compose v2, TCP 80/443 открыты.
2. Код: распаковать релиз (или `git clone`) в `~/zapp`.
3. Достать из restic всё, чего нет в коде:
   ```bash
   export RESTIC_REPOSITORY=... RESTIC_PASSWORD=... B2_ACCOUNT_ID=... B2_ACCOUNT_KEY=...
   restic restore latest --target /tmp/restore
   cp /tmp/restore/extra/env ~/zapp/.env
   mkdir -p ~/zapp/backups && cp /tmp/restore/backups/* ~/zapp/backups/
   ```
   Пароль restic и ключи B2 хранятся отдельно от сервера (у владельца) — без
   них off-site бэкап бесполезен, проверьте, что они есть, ДО отказа машины.
4. `sh scripts/preflight.sh .env production` — зелёный.
5. Поднять только базу: `docker compose up -d db` — на пустом томе сработает
   `scripts/db-init/10-umami.sh` (роль и база аналитики).
6. Вернуть данные: `./scripts/restore.sh backups/outly-<дата>.sql.gz outly`
   (имя базы — как `POSTGRES_DB` в `.env`, на проде `madiflow`).
7. Сессии Telegram: распаковать свежий `outly-telegram-sessions-*.tar.gz` в том
   `outly-telegram-sessions` (`docker run --rm -v outly-telegram-sessions:/s
   -v $PWD/backups:/b alpine tar xzf /b/<архив> -C /s`).
8. Сертификаты (не обязательно, Caddy выпустит новые): содержимое
   `/tmp/restore/extra/caddy-data` в том `outly-caddy-data` тем же приёмом.
9. DNS A-запись домена → новый IP. Дождаться, пока `dig +short домен` его отдаст.
10. `sh scripts/deploy-production.sh`, затем `curl -fsS https://домен/api/health`
    и вход через Telegram. Проверить, что ключи YouTube в настройках читаются
    (значит, `DATA_ENCRYPTION_KEY` из `.env` тот же).

Репетиция раз в квартал на любой одноразовой VM: шаги 1–8 без DNS, вход не
нужен — достаточно `docker compose ps` с зелёными healthcheck и
`select count(*) from leads` в восстановленной базе.
