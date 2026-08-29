# Talaria — AI handoff

## Задача

Это исходники сервиса Talaria: лендинг и кабинет, FastAPI/PostgreSQL backend и отдельный GramJS gateway. Утверждённый визуальный референс находится в `docs/reference/talaria_v5_design.html`.

Публичный продукт теперь называется **Talaria**. Внутренние идентификаторы `zephyr`, `outly`, `zapp`, DOM id и localStorage keys местами сохранены намеренно для обратной совместимости. Не переименовывать их механически.

## С чего начать другой ИИ

1. Прочитать `README.md`, `CHANGELOG.md`, `LESSONS.md`.
2. Прочитать `docs/TALARIA_FULL_CHECKLIST.md` — там текущий статус, известные пробелы и команды проверки.
3. Для визуальной работы сравнивать с `docs/reference/talaria_v5_design.html`, а не с прежним Zephyr/Finpay-направлением.
4. Не менять публичные API/DOM/Telegram-контракты без отдельного согласования.
5. Не добавлять платёжный endpoint: оплата остаётся ручной через подготовленную ссылку `t.me`.

## Что уже сделано

- Лендинг и кабинет переведены на визуальную систему Talaria V5.
- Финальная scroll-сцена: слово TALARIA уходит вниз, появляется рука с письмом и кнопка входа.
- Оригинальные изображения извлечены в `frontend/assets/talaria/`.
- Frontend после последней проверки: Prettier — pass; unit — 56/56; Playwright — 10/10.
- Desktop/tablet/mobile, light/dark и reduced motion покрыты тестами.

## Что нельзя считать завершённым

- Полный Compose-контур не проверен без локального `.env` и `POSTGRES_PASSWORD`.
- Alembic contract snapshot нужно сверить с миграцией `0022`, а не обновлять вслепую.
- У `tg-mini-client` ещё нет автоматической команды `npm test` и обязательного `node:test` набора.
- Нужно отдельно проверить reply watcher: exact recipient ID, one-shot callback, 300 секунд, игнорирование outgoing/bot/group/чужих private chats.
- Нужно прогнать полный backend quality gate, миграцию на копии данных и восстановление backup.

## Безопасность архива

Этот AI-архив намеренно не содержит `.env`, `.venv`, Telegram sessions, backups, `node_modules`, кэши, логи и локальные БД. Секреты необходимо передавать отдельно только при реальной необходимости; для анализа кода они не нужны.

## Быстрые команды

Frontend:

```powershell
cd frontend
pnpm.cmd install --frozen-lockfile
pnpm.cmd run format:check
pnpm.cmd run test:unit
pnpm.cmd run test:smoke
pnpm.cmd run test:contracts
```

Backend:

```powershell
cd backend
..\.venv\Scripts\python.exe -m ruff check .
..\.venv\Scripts\python.exe -m black --check .
..\.venv\Scripts\python.exe -m compileall -q app tests
..\.venv\Scripts\python.exe -m mypy app
..\.venv\Scripts\python.exe -m pytest
```

Full stack после создания собственного `.env` из `.env.example`:

```powershell
docker compose up -d --build --force-recreate
docker compose ps
docker compose logs --tail=150 backend web-tg frontend
```
