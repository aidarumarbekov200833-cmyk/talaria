# tg-mini-client — Telegram-gateway

Внутренний Node/GramJS-сервис с MTProto-сессиями подключённых аккаунтов.
Он не показывает диалоги: браузеру доступна только короткая форма
`телефон → код → 2FA`, а рабочие операции вызывает backend.

## Роли

- `POST /resolve` — проверка username и нормализация профиля;
- `POST /channel-posts` — публичные посты для персонализации AI-оффера;
- `POST /send` — отправка текста с явно выбранного аккаунта;
- обработчик входящих сообщений сообщает backend об ответах лидов;
- `GET /health` — healthcheck.

Все POST-ручки требуют `X-Send-Secret`. Порт 3000 наружу не публикуется.
Nginx проксирует только форму подключения на `/tg-connect/`.

## Сессии и аккаунты

Каждый слот `warmup_accounts` получает отдельный файл:

```text
sessions/user-<ownerId>-acc-<accountId>.txt
sessions/user-<ownerId>.txt   # legacy, подхватывается первым слотом
```

После входа gateway вызывает `POST /api/warmup/identity`, поэтому карточка
аккаунта получает id, username и телефон без ручного ввода.

Backend выдаёт форме пятиминутный билет:

```text
GET /api/telegram-accounts/ticket?account_id=<id>
<ownerId>.<accountId>.<flags>.<exp>.<HMAC-SHA256>
```

## Переменные

| Переменная | Назначение |
|---|---|
| `API_ID`, `API_HASH` | приложение с my.telegram.org |
| `TG_CLIENT_SECRET` | подпись билетов и `X-Send-Secret` |
| `SITE_API_URL` | backend для identity/reply callbacks |
| `TG_LEGACY_OWNER_ID` | владелец самой старой общей сессии |
| `PORT` | внутренний порт, по умолчанию 3000 |

## Локальный запуск

```bash
cp .env.example .env
npm install
npm start
```

Для входа откройте форму через основной сайт: без подписанного билета
WebSocket подключения закрывается.
