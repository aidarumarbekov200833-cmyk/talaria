# Zephyr — ревью безопасности и качества

Дата: 10 августа 2026 года. Scope: frontend, FastAPI backend, Telegram gateway,
контейнерная конфигурация и изменённый AI-контур. Это проверка текущего снимка,
а не обещание абсолютной защищённости.

## Исправлено

- Обновлены уязвимые зависимости: `FastAPI 0.115.* → 0.139.*`,
  `PyJWT 2.9.* → 2.13.*`, `Express 4.19.2 → 4.22.2`,
  `ws 8.17.0 → 8.21.3`. Для Node добавлен lock-файл и сборка через
  `npm ci --omit=dev --ignore-scripts`.
- AI получает полную карту продукта, но данные инструментов всегда фильтруются
  по текущему `owner_id`. Добавлены отдельные тесты, подтверждающие отсутствие
  данных чужого workspace.
- AI-команды интерфейса выполняются только из проверенного результата сервера.
  Разрешены обратимые действия: навигация, заполнение фильтров, несохранённый
  черновик и оформление. Поиск, сохранение, запуск, остановка, удаление и
  подключение аккаунта не исполняются автоматически.
- AI-ввод ограничен: 8 000 символов на сообщение, 24 сообщения и 48 000
  символов суммарно. Это уменьшает риск переполнения контекста и дорогих запросов.
- Ссылка ручной проверки SpamBot разрешена только как точный
  `https://t.me/SpamBot`; открытие идёт с `noopener,noreferrer`.
- Медиа оффера принимает только PNG/JPEG/WebP/GIF data URI, проверяет реальную
  сигнатуру файла и лимит размера; SVG/HTML не принимаются.

## XSS, инъекции и изоляция данных

- Прямых `eval`, `new Function`, `document.write`, `shell=True`, небезопасной
  десериализации и строково собранных SQL-запросов в scope не найдено.
- Ответ AI выводится через `textContent`; данные в HTML-шаблонах проходят через
  общий `esc()`, числовые значения нормализуются, а события помещают сообщение
  в отдельный текстовый узел.
- CSV-экспорт нейтрализует формулы (`=`, `+`, `-`, `@`), что закрывает spreadsheet
  injection при открытии выгрузки.
- CRUD-хелперы проверяют владельца объекта; защищённые API без токена отвечают
  `401`. Отдельный regression test проверяет owner-scoping AI-сводки.
- Включены CSP, `frame-ancestors`, `object-src 'none'`, `base-uri 'self'`,
  `form-action 'self'`, `X-Frame-Options`, `nosniff`, Referrer-Policy и
  Permissions-Policy. Inline JavaScript не используется.
- Подход сверялся с
  [OWASP XSS Prevention](https://cheatsheetseries.owasp.org/cheatsheets/Cross_Site_Scripting_Prevention_Cheat_Sheet.html),
  [OWASP DOM XSS Prevention](https://cheatsheetseries.owasp.org/cheatsheets/DOM_based_XSS_Prevention_Cheat_Sheet.html),
  [OWASP CSP](https://cheatsheetseries.owasp.org/cheatsheets/Content_Security_Policy_Cheat_Sheet.html)
  и [MDN CSP](https://developer.mozilla.org/en-US/docs/Web/HTTP/CSP).

## Проверено

- Backend: 90 тестов, все прошли.
- Ruff: `All checks passed!`.
- Frontend: 22 JS-файла прошли `node --check`; 348 HTML ID, дубликатов нет;
  баланс CSS-скобок `2349/2349`.
- `pip-audit`: `No known vulnerabilities found`.
- `npm audit --omit=dev`: `found 0 vulnerabilities`.
- HTTP: `/` отвечает `200`, защищённый `/api/offers` без токена — `401`, security
  headers присутствуют.
- Docker: backend, database, frontend и Telegram gateway запущены; ключевые
  сервисы healthy.
- Browser smoke: лендинг и вход, Главная, поиск, офферы, рассылка, настройки и
  админ-панель. Проверены переключение темы, точный цвет, Pixel-шрифт и их
  сохранение после перезагрузки.

## Остаточные риски и следующие шаги

- JWT сейчас хранится в `localStorage`. CSP и экранирование существенно снижают
  XSS-риск, но при успешном XSS токен доступен JavaScript. Миграцию на короткую
  access-сессию в `HttpOnly; Secure; SameSite` cookie лучше делать отдельной
  задачей с CSRF-защитой и тестами авторизации.
- CSP пока содержит `style-src 'unsafe-inline'`, потому что существующий UI
  использует inline styles. Убрать директиву можно после переноса динамических
  стилей в классы/nonce-механику.
- Пакет `telegram@2.22.2` архивирован; npm рекомендует `teleproto`. Сейчас
  известных CVE в установленном дереве нет, но миграцию стоит провести отдельным
  совместимостным этапом с реальными MTProto-сессиями.
- Python-зависимости ограничены диапазонами `*`, а не полным lock-файлом.
  Для воспроизводимых production-сборок стоит добавить hashes/lock после выбора
  процесса обновлений.
