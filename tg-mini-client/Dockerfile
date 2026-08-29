# Telegram-gateway: Node + Express + WebSocket авторизации + GramJS (MTProto).
# Отдельный сервис: держит настоящую пользовательскую сессию Telegram.
FROM node:20-alpine
WORKDIR /app

# Манифест и lock-файл делают сборку воспроизводимой: production получает
# ровно те версии, которые прошли npm audit, без плавающих transitive-пакетов.
COPY package.json package-lock.json ./
RUN npm ci --omit=dev --ignore-scripts

COPY server.js ./server.js
COPY reply-filter.js ./reply-filter.js
COPY session-crypto.js ./session-crypto.js
COPY chat.js ./chat.js
COPY spambot.js ./spambot.js
COPY public ./public

# Каталог сессий монтируется томом (см. docker-compose). Файл session.txt —
# это полный доступ к аккаунту, поэтому он живёт в volume, а не в образе.
#
# Каталог создаём заранее и отдаём пользователю node: Docker копирует права
# с этого пути на именованный том при его первом создании. Иначе том
# окажется root:root и сервис не сможет сохранить сессию.
#
# ВНИМАНИЕ при обновлении старой установки: уже существующий том
# web-tg-sessions останется с прежними правами root. Если сервис после
# обновления не может писать в /app/sessions, один раз выполните:
#   docker compose run --rm --user root web-tg chown -R node:node /app/sessions
RUN mkdir -p /app/sessions && chown -R node:node /app
USER node

EXPOSE 3000
CMD ["node", "server.js"]
