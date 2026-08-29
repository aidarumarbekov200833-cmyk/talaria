# Talaria — полный чек-лист проверки проекта

Дата актуализации: 23.08.2026  
Корень проекта: `C:\Users\aidar\OneDrive\Desktop\Zephyr_latest_2026-08-21`

## Обозначения

- ✅ — уже подтверждено автоматической проверкой после Talaria V5.
- ⬜ — обязательно проверить перед релизом.
- ⚠️ — известный пробел или блокер, который нельзя считать проверенным.
- 🧪 — выполнять только на тестовых данных/аккаунтах, не на production.

## 0. Текущий статус

- ✅ Публичный frontend называется Talaria; видимое имя Zephyr удалено.
- ✅ Frontend: Prettier прошёл.
- ✅ Frontend: 56/56 unit-тестов прошли.
- ✅ Frontend: 10/10 Playwright-сценариев прошли.
- ✅ Проверены desktop/tablet/mobile, light/dark, reduced motion, отсутствие горизонтального overflow.
- ✅ Проверены лендинг, тариф, FAQ, Telegram login dialog и восемь разделов кабинета.
- ⚠️ Полный backend + PostgreSQL + GramJS + Docker Compose после редизайна ещё не подтверждён: локальный `.env` не заполнен, отсутствует `POSTGRES_PASSWORD`.
- ⚠️ Общий frontend contract gate ранее останавливался на Alembic snapshot: появилась миграция `0022`, а сохранённый контракт необходимо осознанно сверить и обновить.
- ⚠️ В `tg-mini-client/package.json` сейчас есть только `start`; автоматической команды `npm test` и Node `node:test` suite нет.
- ⚠️ В дереве есть временные файлы `.orig`, `.patch`, `.rej` и `backend/c_*`. Перед релизным архивом решить, нужны ли они, и проверить, что они не попадают в production-образ.
- ⚠️ README/deploy-файлы ещё используют имя Zephyr и внутренние `outly-*`. Внутренние идентификаторы можно сохранять ради совместимости, но публичную документацию нужно привести к Talaria.

## 1. Релизный стоп-лист

Релиз нельзя считать готовым, пока не выполнены все пункты:

- ⬜ Все Compose-сервисы имеют статус `healthy`: `frontend`, `backend`, `web-tg`, `db`.
- ⬜ Backend quality gate и весь pytest проходят с нулевым exit code.
- ⬜ DOM, actions, OpenAPI и Alembic contract gates проходят без необъяснённого изменения snapshot.
- ⬜ Alembic показывает ровно один head и успешно обновляет как пустую, так и копию существующей БД.
- ⬜ GramJS проверен автоматическими Node-тестами, а не только ручным сообщением.
- ⬜ Выполнен полный пользовательский путь: вход → настройки → поиск → база → оффер → preview → тестовая отправка → ответ.
- ⬜ Проверено, что посторонние Telegram-сообщения не превращаются в уведомления бота.
- ⬜ Создан свежий backup и реально выполнено тестовое восстановление.
- ⬜ Секреты отсутствуют в frontend, логах, API, образах и релизном архиве.
- ⬜ Production smoke по HTTPS проходит после чистой пересборки.

## 2. Подготовка окружения

- ⬜ Создать `.env` из `.env.example`; сам `.env` не коммитить и не отправлять в чат.
- ⬜ Заполнить минимум: `BOT_TOKEN`, `BOT_USERNAME`, `JWT_SECRET`, `POSTGRES_PASSWORD`, `DATABASE_URL`, `API_ID`, `API_HASH`, `TG_CLIENT_SECRET`.
- ⬜ Убедиться, что пароль в `DATABASE_URL` совпадает с `POSTGRES_PASSWORD` и URL-encoded.
- ⬜ `JWT_SECRET` и `TG_CLIENT_SECRET` — длинные уникальные случайные строки, не шаблоны.
- ⬜ В production `ADMIN_USERNAME_FALLBACK=false`.
- ⬜ `ADMIN_TELEGRAM_IDS` содержит числовые Telegram ID, а не username.
- ⬜ `TRUSTED_PROXY_HOPS` соответствует цепочке прокси: nginx = 1; Caddy + nginx = 2; Cloudflare + Caddy + nginx = 3.
- ⬜ Для production паузы рассылки не отключены: `SEND_DELAY_MAX_SECONDS > 0`.
- ⬜ Если `/metrics` доступен извне, задан `METRICS_TOKEN`.
- ⬜ Проверить конфигурацию без вывода секретов:

```powershell
cd C:\Users\aidar\OneDrive\Desktop\Zephyr_latest_2026-08-21
docker compose config --quiet
```

## 3. Запуск полного приложения

```powershell
cd C:\Users\aidar\OneDrive\Desktop\Zephyr_latest_2026-08-21
docker compose up -d --build --force-recreate
docker compose ps
docker compose logs --tail=150 backend
docker compose logs --tail=150 web-tg
docker compose logs --tail=100 frontend
```

- ⬜ Все четыре основных сервиса запустились без restart loop.
- ⬜ `GET /api/health` отвечает HTTP 200.
- ⬜ Frontend открывается через Compose, а не только через dev-сервер `:4173`.
- ⬜ `/api/*` проксируется в backend.
- ⬜ `/tg-connect/` проксируется в gateway, но без подписанного билета не даёт доступ к сессии.
- ⬜ Порт PostgreSQL и внутренний порт GramJS не опубликованы наружу.
- ⬜ После перезапуска Compose данные БД и Telegram-сессии сохраняются в именованных volumes.

## 4. Frontend: автоматические проверки

```powershell
cd C:\Users\aidar\OneDrive\Desktop\Zephyr_latest_2026-08-21\frontend
pnpm.cmd install --frozen-lockfile
pnpm.cmd run format:check
pnpm.cmd run test:unit
pnpm.cmd run test:smoke
pnpm.cmd run test:contracts
```

- ✅ `format:check`, `test:unit` и ключевые Playwright-сценарии зелёные.
- ⬜ Повторить весь набор после последней правки и уже на Compose URL.
- ⚠️ `test:contracts` не считать зелёным, пока не разобрана миграция `0022` и Alembic snapshot.
- ⬜ В консоли браузера нет необработанных ошибок и rejected promises.
- ⬜ Network не содержит случайных 404/500, кроме специально смоделированных тестом.

## 5. Лендинг Talaria

- ⬜ Title, favicon, header, hero и копирайт везде говорят Talaria.
- ⬜ Hero соответствует `talaria_v5_design.html`: тёмная розовая система, крупный заголовок, оригинальная иллюстрация.
- ⬜ Навигация ведёт к правильным секциям, sticky header не перекрывает заголовки.
- ⬜ Карточки преимуществ и Hermes-иллюстрации загружаются без размытия и layout shift.
- ⬜ Метрики при ошибке API остаются `—` и показывают понятное сообщение.
- ⬜ FAQ работает мышью и клавиатурой, корректно меняет `aria-expanded`.
- ⬜ Login dialog сам не открывается; открывается только по кнопке.
- ⬜ Escape закрывает dialog, focus остаётся внутри modal и возвращается на кнопку-источник.
- ⬜ Переключатель темы не ломает размеры и сохраняет выбор в `mf_theme`.
- ⬜ На 1440×1000, 1024×768 и 390×844 нет горизонтальной прокрутки.
- ⬜ В системном reduced motion контент сразу виден и не зависит от анимации.

### Финальная сцена/footer

- ✅ Сцена реализована как длинный sticky scroll-stage.
- ⬜ Сначала крупное слово `TALARIA` стоит по центру.
- ⬜ При дальнейшей прокрутке слово уходит вниз плавно, без рывка.
- ⬜ После него появляется рука с письмом.
- ⬜ Кнопка `Войти →` появляется над письмом и остаётся кликабельной.
- ⬜ Кнопка открывает именно Telegram login dialog.
- ⬜ На мобильном рука, письмо и свечение не выходят за viewport.
- ⬜ После завершения сцены нет пустого старого футера, дубля бренда или внезапного скачка страницы.

## 6. Кабинет

Проверить desktop и mobile для каждого раздела:

- ⬜ Главная: метрики, последние активности, пустое/loading/error состояния.
- ⬜ Поиск: фильтры, оценка квоты, запуск, прогресс, отмена и ошибка YouTube API.
- ⬜ Базы: список, пагинация, выбор, import/export CSV, bulk status/delete.
- ⬜ Офферы: создание, редактирование, удаление, обычный/AI режим, изображение.
- ⬜ Рассылка: preview, запуск, журнал, пауза/остановка, прогресс и ответы.
- ⬜ Тариф: валюты, 1/3 месяца, промокод, итоговая цена и ручная оплата.
- ⬜ Настройки: YouTube keys, Telegram-аккаунты, лимиты/паузы, ошибки сохранения.
- ⬜ Админ: доступ только администратору, overview не растёт по числу SQL-запросов.

Для каждого действия отдельно проверить:

- ⬜ Видно состояние загрузки; повторный клик не создаёт дубликат запроса.
- ⬜ Успех показан рядом с действием или изменением данных.
- ⬜ Ошибка объясняет, что произошло и что делать дальше.
- ⬜ Пустое состояние предлагает следующий шаг.
- ⬜ Длинные имена, Telegram username, URL и логи не ломают сетку.
- ⬜ Sidebar, mobile drawer и bottom navigation правильно отражают активный раздел.
- ⬜ `zephyr_active_section` сохраняет текущий раздел как внутренний compatibility key.

## 7. Авторизация и права

- ⬜ Telegram Login работает только на hostname, который задан через BotFather `/setdomain`.
- ⬜ Валидная подпись Telegram создаёт/находит правильного пользователя.
- ⬜ Просроченная/поддельная подпись отклоняется существующим error envelope.
- ⬜ JWT хранится под compatibility key `mf_token`, logout реально инвалидирует локальную сессию.
- ⬜ Обычный пользователь не видит и не вызывает `/api/admin/*`.
- ⬜ Нельзя прочитать/изменить базы, офферы, кампании, аккаунты и настройки другого `owner_id`.
- ⬜ IDOR-проверки выполнены не только через UI, но и прямыми HTTP-запросами.
- ⬜ Rate limit срабатывает на auth, AI, key-check, search и test-send.
- ⬜ При корректных proxy-настройках клиент не может подделать IP через `X-Forwarded-For`.

## 8. Backend: quality gate

```powershell
cd C:\Users\aidar\OneDrive\Desktop\Zephyr_latest_2026-08-21\backend
..\.venv\Scripts\python.exe -m ruff check .
..\.venv\Scripts\python.exe -m black --check .
..\.venv\Scripts\python.exe -m compileall -q app tests
..\.venv\Scripts\python.exe -m mypy app
..\.venv\Scripts\python.exe -m pytest
```

- ⬜ Все команды завершаются кодом 0.
- ⬜ Число собранных pytest-тестов зафиксировано в релизном отчёте; skipped/xfailed объяснены.
- ⬜ Тесты не зависят от порядка запуска и проходят повторно.
- ⬜ Ошибка внутри service делает rollback; repository сам не вызывает `commit`.
- ⬜ Router выполняет HTTP-валидацию/сериализацию, доменная логика остаётся в service.
- ⬜ Background-задачи корректно получают `interrupted`/восстанавливаются после рестарта согласно контракту.

## 9. API и контрактные проверки

- ⬜ OpenAPI paths, методы, status codes и response schemas совпадают с утверждённым snapshot.
- ⬜ Успешные ответы не обёрнуты в новый `{data: ...}`.
- ⬜ Все ошибки имеют существующий envelope `detail/request_id/errors`.
- ⬜ `request_id` присутствует в HTTP-ответе и позволяет найти связанную запись в логах.
- ⬜ DOM contract сохраняет исходные `id`, class-токены, `name`, `data-act`, `data-args`, `data-target`, `data-telegram-login-trigger`.
- ⬜ Все `data-act` разрешаются в опубликованные frontend actions; нет кнопок без действия.
- ⬜ Проверить `/api/health`, `/api/stats/public`, `/api/plans`, `/api/plans/quote`, auth и основные CRUD-маршруты.
- ⬜ 401, 403, 404, 409, 422, 429 и 500 не раскрывают stack trace, SQL или секреты.

## 10. База данных и Alembic

```powershell
cd C:\Users\aidar\OneDrive\Desktop\Zephyr_latest_2026-08-21
docker compose exec backend alembic heads
docker compose exec backend alembic current
docker compose exec backend alembic history
```

- ⬜ `alembic heads` выводит ровно один head.
- ⬜ На текущем дереве ожидается последняя миграция `0022`; сверить revision/down_revision, а не просто переписать snapshot.
- ⬜ `alembic upgrade head` проходит на новой пустой PostgreSQL.
- 🧪 Upgrade проходит на копии существующей production-схемы с данными.
- 🧪 После upgrade проверить основные counts, foreign keys, уникальности и отсутствие потерянных Telegram account/session связей.
- ⬜ Query-count gates проходят: warmup list ≤6, campaign recipients ≤5, launch helper ≤6, reply callback ≤5.
- ⬜ Admin overview остаётся с константным числом запросов при росте пользователей.
- ⬜ Миграция `0022` действительно нужна по `EXPLAIN`, не создаёт дублирующие индексы и не блокирует production надолго.

## 11. Поиск и базы лидов

- ⬜ Валидный YouTube API key сохраняется; неверный показывает локальную понятную ошибку.
- ⬜ API key никогда не возвращается целиком в API и UI после сохранения.
- ⬜ Estimate и фактический поиск одинаково учитывают фильтры и квоту.
- ⬜ Пустая выдача, quota exceeded, timeout и частичный сбой имеют отдельные состояния.
- ⬜ CSV import принимает заявленный формат `;` + UTF-8 BOM и безопасно обрабатывает плохие строки.
- ⬜ CSV export нейтрализует ячейки, начинающиеся с `=`, `+`, `-`, `@`, CR/LF и full-width вариантов.
- ⬜ XSS-строки в title/description/username отображаются текстом, не HTML.
- ⬜ Telegram verification сохраняет прогресс после каждого лида, возобновляется после рестарта и корректно отменяется.
- ⬜ В рассылку проходят только подтверждённые личные профили.

## 12. Офферы, кампании и отправка

- ⬜ Regular offer использует существующие офферы владельца и корректный spintax.
- ⬜ AI offer не получает чужие данные и не выполняет внешнее действие без подтверждения человека.
- ⬜ Launch preview показывает реальные примеры, но не отправляет сообщения и не тратит send quota.
- 🧪 Test-send отправляет ровно одно сообщение выбранным account + recipient.
- 🧪 Launch пишет каждому получателю максимум один раз при `skip_contacted=true`.
- 🧪 Cancel/stop-all прекращает новые отправки, не портя уже сохранённые outcomes.
- 🧪 Перезапуск backend во время кампании отмечает её как `interrupted`; уже отправленные не дублируются.
- ⬜ FloodWait, revoked session, quarantine, unread spike и дневной лимит дают ожидаемый статус и reason journal.
- ⬜ `sending.py`/`account_health.py` compatibility exports не меняют существующие вызовы.

## 13. Тариф и ручная оплата

- ⬜ Публично отображается один тариф Talaria; legacy-планы не показываются новым клиентам.
- ⬜ Цена всегда приходит из `/api/plans/quote`, а не считается браузером.
- ⬜ Проверить RUB и KZT, 1 и 3 месяца.
- ⬜ База: 5 000 ₽ / 25 000 ₸.
- ⬜ 3 месяца: 12 750 ₽ / 63 750 ₸.
- ⬜ Promo первого месяца: скидка 30%.
- ⬜ 3 месяца + promo: 11 250 ₽ / 56 250 ₸.
- ⬜ Два параллельных quote-запроса: UI принимает только последний ответ.
- ⬜ Неверный promo не раскрывает существование приватных кодов.
- ⬜ Оплата остаётся ручной: кнопка готовит `t.me`-сообщение; нет endpoint карты, автосписания или нового payment API.
- ⬜ Сообщение в Telegram содержит Talaria, период, валюту и финальную цену, но не раскрывает введённый promo-код.

## 14. Telegram gateway / GramJS

### Автоматический gate, который ещё нужно добавить

- ⚠️ Добавить `npm test` на базе встроенного `node:test` без новой runtime-зависимости.
- ⬜ FloodWait parsing и retry/backoff.
- ⬜ Registry dedup и единственная регистрация event handlers.
- ⬜ Watch expiry ровно через 300 секунд.
- ⬜ Один watcher создаёт максимум один callback и затем удаляется.
- ⬜ Exact recipient ID используется раньше username fallback.
- ⬜ Игнорируются исходящие сообщения, группы, каналы, боты и чужие private user ID.
- ⬜ Revoked session и повторное подключение имеют ожидаемый lifecycle.

Минимальная syntax-проверка до появления suite:

```powershell
cd C:\Users\aidar\OneDrive\Desktop\Zephyr_latest_2026-08-21\tg-mini-client
npm.cmd ci
node --check server.js
node --check spambot.js
```

### Ручная Telegram-проверка

- 🧪 Подключить отдельный тестовый Telegram-аккаунт через подписанный билет.
- 🧪 Проверить телефон → код → 2FA, отмену и просроченный билет.
- 🧪 Отправить test-send точному тестовому recipient ID.
- 🧪 Ответить из того же private-чата: backend получает ровно один reply callback.
- 🧪 Ответить повторно после one-shot: второго callback нет.
- 🧪 Написать из другого private-чата: callback/уведомление отсутствует.
- 🧪 Написать в группе, от бота и исходящим сообщением: side effects отсутствуют.
- 🧪 Подождать больше 300 секунд: старый watcher больше ничего не пересылает.
- 🧪 Проверить уведомление владельцу через flowbot: правильные клиент, account, campaign и текст.
- ⬜ Сообщения вроде ранее замеченных «неизвестный контакт / тестовое сообщение» не должны приходить вне активного test watcher. Если приходят — сохранить timestamp, account ID, recipient ID и request_id, остановить релиз и проверить фильтры watcher.
- ⬜ Обычный порт 3000 не доступен извне; все POST server API требуют `X-Send-Secret`.

## 15. Безопасность и приватность

- ⬜ Поиск по репозиторию и собранным образам не находит реальные bot tokens, API keys, JWT, session strings и пароли.
- ⬜ `.env`, `sessions/`, `backups/`, `node_modules/`, `.venv/` отсутствуют в Docker build context и публичном архиве.
- ⬜ YouTube key, API_HASH, AI key и Telegram session не появляются в JSON-ответах.
- ⬜ Redaction-тесты подтверждают отсутствие секретов в INFO/ERROR логах и exception trace.
- ⬜ CSP не требует `unsafe-eval`; Telegram login работает при строгой политике.
- ⬜ Все пользовательские строки выводятся через textContent/escaping.
- ⬜ CSV injection закрыта.
- ⬜ Upload изображения проверяет тип и размер, не позволяет path traversal.
- ⬜ Admin IP allowlist, rate limits и request body limits реально срабатывают.
- ⬜ Dependency-аудит выполнен для Python, frontend tooling и Node gateway; найденные high/critical либо устранены, либо документированы.

## 16. Логи, метрики и алерты

- ⬜ `LOG_JSON=true` даёт валидный JSON по одной записи на строку.
- ⬜ У каждого запроса есть `request_id`; один и тот же ID виден в response и связанных логах.
- ⬜ `/metrics` отдаёт нужные счётчики и защищён токеном, если опубликован.
- ⬜ HTTP 5xx/необработанная ошибка отправляет алерт админам, но не чаще `TELEGRAM_ALERT_MIN_INTERVAL_SECONDS` для одинакового ключа.
- ⬜ Recovery после проблемы тоже видна оператору.
- ⬜ Логи Docker ротируются и не заполняют диск.
- ⬜ Логи не содержат полный текст секретов, session strings и приватные payload сверх необходимого.

## 17. Бэкапы и восстановление

```powershell
docker compose up -d backup backup-verify backup-watchdog
docker compose ps backup backup-verify backup-watchdog
docker compose logs --tail=100 backup backup-verify backup-watchdog
```

- ⬜ Создан свежий SQL dump и SHA-256.
- ⬜ Архив Telegram sessions создаётся без публикации наружу.
- 🧪 `backup-verify` реально восстановил dump в отдельную БД и проверил ключевые таблицы.
- ⬜ Watchdog видит свежие marker-файлы и свободное место.
- 🧪 Смоделирован просроченный backup: админ получает один дедуплицированный алерт.
- ⬜ Включён off-site restic либо письменно принято решение о риске потери VPS.
- 🧪 Ручное восстановление проверено на отдельном окружении командой `scripts/restore.sh`, не на production-БД.

## 18. Production / HTTPS

На Linux/VPS:

```bash
sh scripts/preflight.sh .env production
sh scripts/deploy-production.sh
docker compose -f docker-compose.yml -f docker-compose.prod.yml --profile tls ps
curl -fsS https://ВАШ_ДОМЕН/api/health
```

- ⬜ DNS A/AAAA ведёт на нужный VPS; открыты только необходимые 80/443.
- ⬜ Caddy получил сертификат и автоматически его продлевает.
- ⬜ HTTP корректно перенаправляет на HTTPS.
- ⬜ Frontend/backend/web-tg используют read-only root filesystem, tmpfs и `no-new-privileges` из production override.
- ⬜ PostgreSQL и GramJS остаются во внутренней Docker-сети.
- ⬜ Перезапуск контейнеров не теряет volumes.
- ⬜ После выкладки повторены health, login, quote и test-send smoke.
- ⬜ Rollback-процедура и предыдущий backup доступны до начала обновления.

## 19. Производительность и доступность

- ⬜ Chrome Performance trace не содержит long tasks >50 ms во время основных анимаций.
- ⬜ На локальном 60 Hz gaps >33 ms не превышают согласованный предел.
- ⬜ Анимации используют transform/opacity; изменение height допускается только для FAQ.
- ⬜ Изображения имеют разумный размер и не блокируют first render.
- ⬜ Keyboard-only: навигация, FAQ, dialogs, cabinet controls и формы доступны.
- ⬜ Focus-visible заметен во всех темах.
- ⬜ Контраст текста/controls соответствует WCAG AA; служебный текст не мельче 12 px.
- ⬜ Screen reader получает названия кнопок, ошибки формы и статусы загрузки.
- ⬜ При `prefers-reduced-motion: reduce` нет скрытого до скролла контента.

## 20. Финальный пользовательский сценарий

На отдельном тестовом пользователе и тестовом Telegram-аккаунте:

1. ⬜ Открыть production URL и войти через Telegram.
2. ⬜ Сохранить YouTube key и подключить Telegram account.
3. ⬜ Выполнить маленький поиск и создать базу.
4. ⬜ Запустить полную Telegram-верификацию, проверить progress и restart recovery.
5. ⬜ Создать regular offer и посмотреть launch preview.
6. 🧪 Отправить одно тестовое сообщение точному recipient ID.
7. 🧪 Получить один ответ и одно корректное уведомление владельцу.
8. ⬜ Проверить журнал, статус отправки, доставку/ответ и отсутствие дубля.
9. ⬜ Остановить тестовую кампанию и убедиться, что новые сообщения не уходят.
10. ⬜ Выйти, войти повторно и убедиться, что состояние сохранено.

## 21. Что приложить к релизному отчёту

- ⬜ Дата/время проверки и версия исходников/checkpoint SHA-256.
- ⬜ Точные totals: frontend unit, Playwright, backend pytest, Node tests.
- ⬜ Вывод `docker compose ps` без секретов.
- ⬜ Alembic current/head и результат upgrade на тестовой копии.
- ⬜ Desktop/mobile скриншоты лендинга, финальной сцены и восьми разделов кабинета.
- ⬜ Подтверждение backup restore.
- ⬜ Список известных ограничений и осознанно принятых рисков.
- ⬜ Отдельный список всех красных/жёлтых пунктов, которые остались.

## 22. Где искать код

- Talaria markup и finale: `frontend/index.html`.
- Главный визуальный слой: `frontend/css/talaria-v5.css`.
- Scroll-анимация finale: `frontend/js/landing.js`.
- Frontend API/actions: `frontend/js/api.js`, `frontend/js/actions.js`.
- Backend entrypoint: `backend/app/main.py`.
- Routers: `backend/app/routers/`.
- Services/repositories: `backend/app/services/`, `backend/app/repositories/`.
- Модели и миграции: `backend/app/models.py`, `backend/alembic/versions/`.
- Telegram gateway: `tg-mini-client/server.js`.
- Compose/prod hardening: `docker-compose.yml`, `docker-compose.prod.yml`.
- Backup/deploy: `scripts/`, `deploy/`.
- Contract snapshots: `frontend/tests/contracts/`, `backend/tests/contracts/`.

---

Короткое правило приёмки: если пункт влияет на деньги, авторизацию, чужие данные, Telegram-отправку, миграцию или восстановление — одного визуального клика недостаточно; нужен автоматический тест либо воспроизводимый технический результат.
