# Нейрокомментинг — сделано

- [x] models: NeuroTarget, NeuroRun, NeuroComment
- [x] services/neuro_budget.py — общий дневной котёл, тиры прогрева, взаимоисключение с рассылкой
- [x] neuro_comment.py — генерация человечного комментария + постфильтр + антиповтор
- [x] routers/neuro.py — площадки, поиск, вступление, запуск, журнал, предпросмотр
- [x] neuro_worker.py — фоновый прогон, проверка допуска перед каждым комментарием
- [x] mini-client: /neuro/search, /neuro/info, /neuro/join, /neuro/posts, /neuro/comment
- [x] alembic 0032 (прогнана на чистой базе, колонки сверены с моделями)
- [x] plans: capability neuro_comment
- [x] frontend: раздел «Нейрокомментинг» (index.html, js/neuro.js, css/app.css, main.js)
- [x] ai_prompt.py (карта интерфейса + «чего нет»)
- [x] тесты: 529 backend / 57 mini-client, контракты dom/actions/openapi/alembic

## Не сделано намеренно
- Режим «поиск по теме → автокомментинг без подтверждения»: поиск отдаёт
  кандидатов, вступает человек. Автовступление = самый быстрый путь в бан.
- Планировщик по расписанию: запуск ручной, как и у рассылки.
