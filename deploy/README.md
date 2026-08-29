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
