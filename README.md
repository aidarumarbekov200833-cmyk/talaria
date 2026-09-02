# Zephyr

> Внутренние имена `outly-*` и `zapp` в volume, путях сервера и ключах
> localStorage сохранены для совместимости: их переименование отсоединило бы
> существующую базу, Telegram-сессии и пользовательские настройки.

**YouTube Lead-Gen → Telegram Outreach** — сервис для поиска YouTube-каналов по нише, оценки
лидов и рассылки офферов через Telegram.

---

## Архитектура

| Сервис | Технология | Порт / URL |
|---|---|---|
| `backend` | Python 3.12, FastAPI, SQLAlchemy 2, Alembic, PostgreSQL 16 | `/api/` |
| `frontend` | Vanilla JS (ES-модули), Nginx | `/` |
| `tg-mini-client` | Node 20, Express, GramJS (MTProto gateway) | `/tg-connect/`, internal API |
| `db` | PostgreSQL 16 | внутренний |
| Caddy | Reverse proxy, HTTPS/TLS (profile `tls`) | `:443` |

---

## Быстрый старт (локально)

```bash
# 1. Копируем переменные
cp .env.example .env
# Открываем .env и обязательно заполняем:
#   BOT_TOKEN, BOT_USERNAME, JWT_SECRET, DATA_ENCRYPTION_KEY, POSTGRES_PASSWORD,
#   API_ID, API_HASH, TG_CLIENT_SECRET
# ВАЖНО: тот же POSTGRES_PASSWORD впишите в DATABASE_URL.

# 2. Сборка и запуск
docker compose up -d --build

# 3. Проверка состояния (backend сам выполняет alembic upgrade head при старте)
docker compose ps
docker compose logs --tail=100 backend

# 4. Seed-данные (необязательно, для демо)
docker compose exec backend python scripts/seed.py

# Если .env скопирован без изменений: http://localhost:8080
# Если FRONTEND_HTTP_PORT не задан: http://localhost:8080 (наружу nginx не публикуется)
```

### Вход через ngrok

Запускайте tunnel на внешний HTTP-порт frontend, например `ngrok http 8080`.
В `@BotFather` → `/setdomain` передавайте только hostname без `https://`, пути и
завершающего `/`: `example.ngrok-free.app`. В браузере открывайте ровно
`https://example.ngrok-free.app`; hostname должен совпадать посимвольно. Если
ngrok выдал новый случайный hostname после перезапуска, `/setdomain` нужно
обновить. После изменения `BOT_TOKEN` или `BOT_USERNAME` перезапустите backend:

```bash
docker compose up -d --force-recreate backend
```

Telegram Login использует redirect на ту же страницу (`data-auth-url`), поэтому
работает со строгой CSP без небезопасного `unsafe-eval`.

### Если распаковали обновление поверх старой версии

Простого копирования файлов недостаточно: старый контейнер продолжит выполнять
старый код. Пересоберите все сервисы и проверьте миграцию `0016`:

```bash
docker compose up -d --build --force-recreate
docker compose exec backend alembic current
docker compose logs --tail=150 backend
```

В выводе `alembic current` должно быть `0019 (head)`. Для старых баз эпохи
`create_all` entrypoint теперь ставит baseline `0001`, а затем реально применяет
`0002–0019`; прежнее ошибочное `stamp head` пропускало нужные изменения схемы.

## Рабочий поток

1. В «Настройках» добавьте YouTube Data API key и подключите один или несколько
   Telegram-аккаунтов. Центр безопасности показывает только проверяемые данные:
   статус авторизации, возраст сессии, фактические ошибки, паузы и непрочитанные.
   Отозванная сессия, FloodWait, рост ошибок или резкий скачок непрочитанных
   автоматически исключают аккаунт из отправки.
2. В «Поиске лидов» выберите источник ключевых слов и фильтры каналов: диапазон
   подписчиков, общие/средние просмотры, отношение просмотров к подписчикам,
   число видео, возраст канала, страну, скрытый счётчик и обязательность
   Telegram-ссылки. Частые наборы условий можно сохранить как пресеты.
3. После поиска прокрутите эту же страницу до «Telegram-верификации базы».
   Нажмите «Проверить всю базу» один раз: задача работает в фоне, сохраняет
   прогресс после каждого канала и возобновляется после рестарта. Личная ссылка
   сохраняется сразу; для канала или бота gateway проверяет публичное описание
   и свежие публикации и ищет личный контакт владельца.
4. В рассылку попадают только подтверждённые личные профили. Выберите обычный
   или персональный AI-оффер, посмотрите 2–3 варианта вступления по тематике,
   размеру и последним роликам канала и запустите фоновую отправку. Кампанию
   можно остановить; отправки, доставки, прочтения, ответы и интерес видны в
   реальном времени, а фактические ответы формируют тепловую карту времени.

`tg-mini-client` — изолированный MTProto gateway, а не Web Telegram: наружу
публикуется только узкая форма телефон → код → 2FA. Список диалогов, просмотр
чатов и отправка произвольных сообщений через браузер отсутствуют.

---

## Переменные окружения (`.env`)

### Обязательные

| Переменная | Описание |
|---|---|
| `BOT_TOKEN` | Токен бота в формате `1234567890:AAF…` |
| `BOT_USERNAME` | Узернейм бота без `@` |
| `JWT_SECRET` | Случайная строка ≥ 32 символов |
| `DATA_ENCRYPTION_KEY` | Случайная строка ≥ 32 символов; шифрует ключи YouTube в БД. Не ротировать без перешифровки |
| `POSTGRES_PASSWORD` | Пароль пользователя PostgreSQL |
| `API_ID` | Telegram API ID для GramJS |
| `API_HASH` | Telegram API Hash для GramJS |
| `TG_CLIENT_SECRET` | Общий секрет backend ↔ Telegram-gateway и подпись билетов подключения |

### С умолчанием, но часто меняют

| Переменная | Умолчание | Описание |
|---|---|---|
| `DOMAIN` | `""` | Домен для Caddy (TLS). Пусто — основные порты без TLS |
| `FRONTEND_HTTP_PORT` | `127.0.0.1:8080` | Порт/адрес фронтенда без TLS. Голый `80` наружу — только осознанно |
| `ADMIN_TELEGRAM_IDS` | `""` | Телеграм-id админов через запятую: `123,456` |
| `ADMIN_USERNAME` | `Cxentrall` | публичный контакт владельца на странице тарифов |
| `ADMIN_USERNAME_FALLBACK` | `false` | локальный fallback прав по username; в production всегда `false` |
| `LOG_JSON` | `true` | `true` — JSON-лог (прод), `false` — текст (локаль) |
| `LOG_LEVEL` | `INFO` | `DEBUG`, `INFO`, `WARNING`, `ERROR` |
| `TRUSTED_PROXY_HOPS` | `1` | Количество собственных proxy-хопов перед бэкендом |
| `BACKUP_KEEP_DAYS` | `30` | Сколько дней хранить локальные дампы и архивы Telegram-сессий |
| `BACKUP_VERIFY_INTERVAL_DAYS` | `7` | Как часто реально восстанавливать последний дамп в тестовую БД |
| `OUTLY_PGDATA_VOLUME` | `outly-pgdata` | Стабильное имя volume с production-базой |
| `RESTIC_REPO` | `""` | Удалённое репо `restic` (sftp://, s3://, b2://…) |
| `RESTIC_PASSWORD` | `""` | Пароль для шифрования резервных копий |
| `AI_BASE_URL` | `https://freeinference.org/v1` | Базовый URL OpenAI-совместимого API |
| `AI_MODEL` | `deepseek-v4-flash` | Имя модели провайдера |
| `AI_API_KEY` | `""` | Ключ API |
| `SENTRY_DSN` | `""` | DSN Sentry |

Настройки AI-провайдера намеренно читаются только из окружения сервера: ключи и
системный prompt не доступны в админке и не попадают в браузер или БД.

### AI-помощник и управление интерфейсом

Системная карта проекта находится в `backend/app/ai_prompt.py`, а фактические
данные помощник получает только через типизированные инструменты из
`backend/app/ai_tools.py`. Все выборки ограничены `owner_id` текущего
пользователя: чужие базы, офферы, кампании, аккаунты, ключи и сессии в контекст
не попадают.

Помощник умеет собрать сводку workspace, открыть раздел, заполнить фильтры
поиска, подготовить **несохранённый** черновик оффера и изменить локальное
оформление. Он не нажимает «Поиск», «Сохранить», «Запустить», «Удалить» или
«Подключить аккаунт»: расход квоты, записи в БД и внешние действия всегда
подтверждает человек. Сервер принимает не больше 24 сообщений и 48 000 символов
контекста за запрос; сырой вызов модели не исполняется в браузере.

---

## Развёртывание на VPS

```bash
# 1. Возьмите deploy/.env.production.example за основу корневого .env.
# 2. Проверьте env без вывода значений секретов.
sh scripts/preflight.sh .env production

# 3. Production override + Caddy TLS + миграции + health-состояние.
sh scripts/deploy-production.sh
```

`docker-compose.prod.yml` включает read-only root filesystem для frontend,
backend и Telegram gateway, временные каталоги в `tmpfs`,
`no-new-privileges` и корректные grace periods. Caddy публикует только 80/443;
PostgreSQL и Telegram gateway остаются во внутренней сети Compose.

### Готовый архив для переноса

На Windows из корня проекта:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/package-release.ps1
```

Архив `release/outly-deploy.zip` содержит код, production-конфиги и текущий
корневой `.env`, как единый комплект. Сам архив и `.env` исключены из git, а
все `.dockerignore` исключают env из Docker-образов. Упаковщик проверяет, что в
zip нет Telegram-сессий, backup-данных, кэшей и `node_modules`. Передавайте архив только
по защищённому каналу. На сервере распакуйте его и следуйте `deploy/README.md`.

### Бэкапы

```bash
# Автоматические сервисы: ежедневный атомарный дамп, SHA-256, Telegram-сессии
# и еженедельное пробное восстановление в отдельную БД.
docker compose up -d backup backup-verify backup-watchdog
docker compose ps backup backup-verify backup-watchdog
docker compose logs --tail=80 backup backup-verify backup-watchdog

# Восстановление из дампа
./scripts/restore.sh backups/outly-2026-08-01_03-00.sql.gz

# Удалённые резервные копии (требует RESTIC_REPO + RESTIC_PASSWORD в .env):
docker compose --profile offsite-backup up -d
```

`backup` считается healthy только при свежем проверенном архиве. Сервис
`backup-verify` раз в неделю восстанавливает последний дамп в
`outly_restore_verify`, проверяет ключевые таблицы и удаляет временную БД.
`backup-watchdog` следит за обоими marker-файлами и свободным местом, а при
проблеме пишет администраторам в Telegram. Production при этом не
останавливается. Локальные копии защищают от ошибки
пользователя; от полной потери VPS защищает только включённый off-site restic.

---

## API — основные эндпоинты

### Базы лидов

| Метод | Путь | Описание |
|---|---|---|
| GET | `/api/leads/databases` | Список баз |
| GET | `/api/leads?database_id=&status=&q=&sort=&offset=&limit=` | Лиды с пагинацией |
| GET | `/api/leads/export?database_id=` | Скачать CSV |
| POST | `/api/leads/import` | Загрузить CSV (`multipart/form-data`) |
| POST | `/api/leads/bulk/status` | Массовое изменение статуса |
| POST | `/api/leads/bulk/delete` | Массовое удаление |
| POST | `/api/leads/{id}/telegram` | Проверить Telegram одного лида |
| GET | `/api/leads/databases/{id}/verification` | Текущее задание и прогресс проверки |
| POST | `/api/leads/databases/{id}/verification/start` | Одной командой проверить всю базу в фоне |
| POST | `/api/leads/databases/{id}/verification/cancel` | Остановить после текущего канала |
| POST | `/api/leads/resolve-telegram` | Старый пакетный API, оставлен для совместимости |

Тело запуска полной проверки: `{"retry_errors": false, "wait": false}`.
`wait: true` удобно только для тестов API; интерфейс всегда запускает фоновое задание и
показывает его прогресс. После завершения ошибки можно повторить одной кнопкой.

### Поиск лидов

| Метод | Путь | Описание |
|---|---|---|
| GET | `/api/niches` | Готовые ниши |
| POST | `/api/topics` | Темы из YouTube-трендов или ниш |
| POST | `/api/keywords` | Ключевые слова без расхода YouTube-квоты |
| POST | `/api/search/estimate` | Оценка квоты с учётом всех фильтров |
| POST | `/api/search` | Собрать и сохранить отфильтрованную базу каналов |

**Формат CSV** (разделитель `;`, UTF-8 BOM):
`channel_id;title;custom_url;country;subscribers;video_count;view_count;published_at;matched_keyword;status;description`
Поле `score` в импорте игнорируется; пересчитывается автоматически.

### Рассылки

| Метод | Путь | Описание |
|---|---|---|
| GET | `/api/campaigns` | Кампании с пагинацией |
| POST | `/api/campaigns` | Создать (запуска по расписанию больше нет) |
| POST | `/api/campaigns/launch` | Создать и сразу отправить партию |
| POST | `/api/campaigns/launch-preview` | Примеры текстов до запуска (то же тело, без отправки) |
| POST | `/api/campaigns/stop-all` | Аварийно остановить все активные кампании пользователя |
| POST | `/api/campaigns/{id}/cancel` | Мягко остановить идущую кампанию |
| POST | `/api/campaigns/{id}/recipients` | Список к отправке с отсевом |
| POST | `/api/campaigns/{id}/sends` | Отметить отправленных (идемпотентно) |
| GET | `/api/campaigns/{id}/sends` | История отправок |

**recipients запрос** (тело POST):
```json
{ "database_id": 1, "skip_contacted": true, "min_score": 60, "offset": 0, "limit": 100 }
```
`skip_contacted: true` — включено по умолчанию, исключает лидов, которым уже отправляли в любой рассылке.

**launch запрос** (тело POST):
```json
{ "database_id": 1, "offer_mode": "regular", "spintax": true, "limit": 50, "skip_contacted": true, "wait": false }
```
Запуск по умолчанию **фоновый**: ответ приходит сразу (`background: true`),
отправка идёт в отдельном потоке с индивидуальными паузами и суточным лимитом
каждого аккаунта. `SEND_DELAY_MAX_SECONDS=0` в `.env` глобально отключает паузы
для тестов; в production оставьте значение больше нуля. Ход отправки — в журнале кампании
(`GET /api/campaigns/{id}/logs`) и по WebSocket. `wait: true` возвращает старое
синхронное поведение — только для маленьких партий через API. Рассылки,
прерванные перезапуском сервера, при старте автоматически получают статус
`interrupted` с пояснением в журнале — уже отправленное учтено, повторный запуск
не пишет тем, кому уже писали.

`launch-preview` принимает то же тело и возвращает 2–3 примера текстов на
реальных лидах (тем же кодом, что реальная отправка) — ничего не отправляет и
не требует подключённых аккаунтов. Для `offer_mode: ai` показывается скелет с
подставленными данными лида: генерацию ИИ в предпросмотре не зовём.

Когда лид отвечает (`POST /api/campaigns/reply` от tg-mini-client), помимо
события на сайте владельцу рассылки уходит личное сообщение от бота
(`BOT_TOKEN`) — если владелец хоть раз писал этому боту (ограничение Telegram).

Два типа оффера, ровно те же, что переключаются в интерфейсе:

* `regular` — берутся **все** офферы владельца и чередуются по лидам
  (`offer_ids` можно передать, чтобы сузить список, но интерфейс этого не делает).
  Плейсхолдеры подставляются, спинтакс — по желанию.
* `ai` — ИИ пишет каждому лиду свой текст по **скелету** из настроек
  (`users.ai_offer_template`). Скелет и число сканируемых материалов правятся
  через `/api/offers/ai-settings`, в рассылке они не вводятся. Персонализация
  учитывает тематику, размер и последние материалы канала.

### Офферы и ИИ-оффер

| Метод | Путь | Описание |
|---|---|---|
| GET/PUT | `/api/offers/ai-settings` | Скелет ИИ-оффера, число материалов, доступность по тарифу |
| POST | `/api/offers/ai-generate` | Получить 2–3 варианта вступления по данным свежего лида |
| GET | `/api/offers/placeholders` | Доступные подстановки |
| POST | `/api/offers/preview` | Подставить данные реального лида в текст |

### Статистика офферов и тариф

| Метод | Путь | Описание |
|---|---|---|
| GET | `/api/campaigns/offer-stats` | Воронка отправлено → доставлено → прочитано → ответило → заинтересовалось и тепловая карта ответов |
| POST | `/api/campaigns/read` | Внутренняя отметка прочтения от Telegram-gateway (`X-Send-Secret`) |
| GET | `/api/plans` | Единый тариф Zephyr: 5 000 ₽ / 25 000 ₸ в месяц, без продуктовых квот |
| POST | `/api/plans/quote` | Серверный расчёт периода и промокода до перехода к оплате |

Три месяца оплачиваются одним платежом со скидкой 15%. Промокод `ZEPHYR30`
даёт 30% на первый месяц; итоговую сумму всегда рассчитывает backend, а в
Telegram администратору передаются период, промокод и рассчитанная цена.

### Прогрев аккаунтов

| Метод | Путь | Описание |
|---|---|---|
| GET | `/api/warmup` | Аккаунты + авторизация, возраст сессии, ошибки, пауза и unread-снимок |
| POST/PATCH | `/api/warmup`, `/api/warmup/{id}` | Добавить аккаунт или изменить лимиты/паузы/переключатели |
| PATCH | `/api/warmup/{id}/days/{day}` | Отметить день ручного чек-листа |
| POST | `/api/warmup/{id}/toggle` | Пауза/возобновление прогрева |

Поле `ready: true` означает, что аккаунт прошёл внутренний план прогрева и его
Telegram-сессия сейчас авторизована. Zephyr не придумывает статус SpamBot:
неизвестное состояние так и показывается неизвестным. Перед началом и во время
отправки gateway повторно проверяет сессию и реальные сигналы безопасности.

### Админка

| Метод | Путь | Описание |
|---|---|---|
| GET | `/api/admin/users?q=&offset=&limit=` | Пользователи |
| GET | `/api/admin/login-attempts?only_failed=&reason=&ip=&q=&offset=&limit=` | Отказы входа |
| GET | `/api/admin/activity?user_id=&scope=&level=&q=&offset=&limit=` | Журнал действий |
| GET | `/api/admin/whitelist` | Белый список |
| GET | `/api/admin/overview` | Оперативные показатели и ошибки сайта |
| POST | `/api/admin/notifications` | Уведомление всем, активным за 30 дней или одному пользователю |

Администратор видит пользователей, тарифы, блокировки, базы, кампании, входы,
события и ошибки, может выгружать журналы и отправлять сообщение во внутреннюю
ленту и, при необходимости, через Telegram-бота. Секреты API в ответах не
возвращаются.

---

## Разработка

```bash
# Тесты
cd backend
pip install -r requirements-dev.txt
pytest

# Линтер
ruff check app/ tests/
ruff format --check app/ tests/
mypy app/

# Фронтенд: проверка синтаксиса
cd frontend
for f in js/*.js; do node --check "$f"; done

# Миграция БД (новая)
cd backend
alembic revision --autogenerate -m "describe change"
alembic upgrade head

# Откат миграции (всегда работающий!)
alembic downgrade -1
```

### CI

GitHub Actions (`.github/workflows/ci.yml`) запускается при каждом push/PR в `main`:
- `backend` — ruff, ruff-format, mypy, pytest
- `frontend` — `node --check` всех JS-модулей, проверка отсутствия inline-обработчиков
- `docker-build` — сборка всех трёх Dockerfile
- `secrets-scan` — поиск случайно закоммиченного BOT_TOKEN

### Конвенция коммитов

```
refactor(scope): описание
feat(scope): новая функция
fix(scope): исправление
```

Scope: `backend`, `frontend`, `db`, `infra`, `ci`.

---

## Структура проекта

```
├── backend/              FastAPI-бэкенд
│   ├── app/
│   │   ├── routers/      HTTP-роутеры
│   │   ├── models.py     SQLAlchemy-модели
│   │   ├── schemas.py    Pydantic-схемы
│   │   ├── services.py   Бизнес-логика (скоринг, журнал)
│   │   ├── config.py     Настройки (pydantic-settings)
│   │   ├── dependencies.py JWT, get_db, require_admin
│   │   └── ratelimit.py  Rate-лимитер
│   ├── alembic/versions/ Миграции БД (0001–0016)
│   ├── tests/            тесты на SQLite в памяти
│   ├── scripts/
│   │   ├── backup.sh     Дамп БД + ротация
│   │   ├── restore.sh    Восстановление из дампа
│   │   └── seed.py       Демо-данные
│   ├── requirements.txt
├── frontend/             Vanilla JS + Nginx
│   ├── js/               23 ES-модуля
│   ├── css/
│   ├── index.html
│   └── nginx.conf
├── tg-mini-client/       GramJS MTProto-клиент
├── .github/workflows/
│   └── ci.yml            GitHub Actions CI
├── docker-compose.yml
├── docker-compose.prod.yml production hardening override
├── Caddyfile
├── deploy/
│   ├── .env.production.example
│   └── README.md          инструкция для сервера
├── scripts/
│   ├── backup.sh
│   ├── restore.sh
│   ├── preflight.sh       проверка env без вывода секретов
│   ├── deploy-production.sh
│   └── package-release.ps1
└── .env.example
```

---

## Чек-лист перед деплоем

- [ ] Все обязательные переменные заполнены в `.env`
- [ ] `bash scripts/preflight.sh .env production` завершён с кодом `0`
- [ ] `POSTGRES_PASSWORD` не равен публичным шаблонам `outly` / `madiflow`
- [ ] `JWT_SECRET` длиной ≥ 32 символа и случайный
- [ ] `DATA_ENCRYPTION_KEY` задан (≥ 32 символа) и лежит в off-site бэкапе вместе с `.env`
- [ ] `TG_CLIENT_SECRET` длинный, случайный и одинаковый у backend/gateway
- [ ] `AI_API_KEY`, `AI_BASE_URL`, `AI_MODEL` заданы в окружении, если нужен AI
- [ ] `BOT_TOKEN` живой (не отозван, убедитесь через `/api/config`)
- [ ] `.env` не попал в git (`git status`, `git log --all --name-only | grep .env`)
- [ ] Файл `#U0432` и аналогичные с `#` в начале удалены или не следятся (в `.gitignore`)
- [ ] `alembic upgrade head` запущен на сервере
- [ ] `DOMAIN` указан (если Caddy с TLS)
- [ ] `TRUSTED_PROXY_HOPS=2` и `FRONTEND_HTTP_PORT=127.0.0.1:…` для Caddy + nginx
- [ ] `backup`, `backup-verify` healthy, `backup-watchdog` запущен
- [ ] В `backups/` есть `.last-ok`, `.last-restore-ok` и SHA-256 последнего дампа
- [ ] `RESTIC_REPO` / `RESTIC_PASSWORD` заполнены для защиты от потери всего VPS
- [ ] `ADMIN_TELEGRAM_IDS` содержит ваш Telegram ID
- [ ] CI-зелёный на `main`
