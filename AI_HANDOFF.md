# Talaria — AI handoff

## Что это

Исходники сервиса Talaria: лендинг + кабинет (vanilla JS, без сборщика), FastAPI/PostgreSQL backend, GramJS-шлюз Telegram (`tg-mini-client`), Caddy с TLS. Прод: https://talaria.mooo.com. Утверждённый визуальный референс — `docs/reference/talaria_v5_design.html`.

Публичный продукт называется **Talaria**. Внутренние идентификаторы `zephyr`, `outly`, `zapp`, DOM id и localStorage keys местами сохранены намеренно для обратной совместимости. Не переименовывать их механически.

## С чего начать

1. `README.md`, `CHANGELOG.md`, `LESSONS.md`, `docs/RELEASES.md` — история и текущее состояние.
2. `ДОСТУПЫ.md` (если передан вместе с архивом) — сервер, SSH, секреты, деплой.
3. Не менять публичные API/DOM/Telegram-контракты без согласования: их стерегут контрактные гейты (`scripts/contract_gate.py`, манифесты в `frontend/tests/contracts/` и `backend/tests/contracts/`).
4. Не добавлять платёжный endpoint: оплата ручная через ссылку `t.me`.

## Состояние на v44 (2026-09-01)

- Голова миграций Alembic — **0038**. Схему приводит `alembic upgrade head` из `docker-entrypoint.sh`, `create_all` не используется.
- Поиск лидов и рассылка возобновляются после рестарта сервера (таблица `search_jobs`, `campaigns.lead_ids`); по завершении поиска, проверки базы и рассылки владельцу уходит личный пуш через бота.
- Тесты зелёные: backend pytest (полный набор) + ruff, frontend 56 unit + 26 Playwright + Prettier, tg-mini-client 57 node:test, контрактные гейты dom/actions/openapi/alembic.

## Секреты

По решению владельца живые ключи прода лежат в `deploy/.env.production` (BOT_TOKEN, JWT_SECRET, POSTGRES_PASSWORD, TG_CLIENT_SECRET, API_ID/API_HASH, AI_API_KEY и др.). Репозиторий обязан оставаться приватным. SSH-ключ сервера в репозиторий не входит — передаётся отдельно.

## Быстрые команды (Linux)

Frontend (из `frontend/`):

```bash
npx prettier --check css index.html js tests
node --test tests/unit/*.test.js
npx playwright test
```

Backend (из `backend/`, нужен venv с зависимостями из `requirements.txt`):

```bash
python -m ruff check app tests alembic
python -m pytest -q
```

Шлюз (из `tg-mini-client/`):

```bash
node --test tests/*.test.js
```

Контрактные гейты (из корня):

```bash
python scripts/contract_gate.py dom check --manifest frontend/tests/contracts/dom-contract.json --html frontend/index.html
python scripts/contract_gate.py actions check --manifest frontend/tests/contracts/actions-contract.json --html frontend/index.html $(for f in frontend/js/*.js; do printf -- "--source %s " "$f"; done)
```

## Правила, которые здесь легко нарушить

- Цвета только токенами из `frontend/css/tokens.css`; `transition` только через `var(--motion-*)`.
- Меняя js-модуль — бампать его query-версию (`./module.js?v=…`) во всех местах импорта.
- Динамические оверлеи/меню аппендить в `#appWrapper`, не в `body`.
- Новые `data-act` в динамических шаблонных строках ломают actions-гейт — использовать `createElement` + `addEventListener`.
- Reply-путь `/api/campaigns/reply` ограничен 11 SQL-запросами (гейт `test_query_count_gates.py`) — новые проверки подшивать outerjoin'ом.
