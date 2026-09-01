"""Application settings, loaded from the environment (or a local .env file)."""

import ipaddress

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Значения JWT_SECRET, которые НЕЛЬЗЯ пускать в прод: пустое и «шаблонные» из
# .env.example. Даже длинный, но стандартный секрет небезопасен — его знают все.
_INSECURE_JWT_SECRETS = {
    "",
    "change-me",
    "change-me-to-a-long-random-string",
}
# Минимальная длина случайного секрета. token_urlsafe(48) даёт ~64 символа —
# так что порог с запасом ниже рекомендуемого значения.
_MIN_JWT_SECRET_LEN = 32


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    @model_validator(mode="before")
    @classmethod
    def _blank_env_values_use_default(cls, data: object) -> object:
        """Пустая строка в .env не должна валить старт контейнера.

        Строка вида `ADMIN_USERNAME_FALLBACK=` (переменная объявлена, значение
        пустое) приходит в pydantic как `""`. Для `bool`/`int`/`float` это не
        разбирается, и приложение падает на импорте настроек ещё до логов —
        снаружи это выглядит как «контейнер с бэкендом не запустился».

        Считаем пустую строку равносильной «не задано»: убираем ключ, и
        подставляется значение по умолчанию из объявления поля. Защищает сразу
        все такие настройки (log_json, rate_limit_enabled,
        telegram_alerts_enabled, admin_username_fallback, все *_seconds и
        лимиты), а не одну конкретную.
        """
        if not isinstance(data, dict):
            return data

        scalar_fields = {
            name for name, info in cls.model_fields.items() if info.annotation in (bool, int, float)
        }
        for name in list(data.keys()):
            # Ключи окружения приходят в нижнем регистре, но подстрахуемся.
            if (
                name.lower() in scalar_fields
                and isinstance(data[name], str)
                and not data[name].strip()
            ):
                data.pop(name)
        return data

    # Telegram bot credentials. Create the bot with @BotFather (/newbot), then
    # bind your domain with /setdomain — the login widget refuses to render
    # on domains that are not registered for the bot.
    bot_token: str = ""
    bot_username: str = ""

    jwt_secret: str = "change-me"
    database_url: str = "postgresql+psycopg2://outly:outly@db:5432/outly"

    # Username (Telegram, без @) владельца проекта. Используется ТОЛЬКО как
    # публичный контакт для оплаты — подписки активируются вручную после оплаты
    # в личку. Правами админа он больше не наделяет: username в Telegram может
    # сменить кто угодно, поэтому раздавать по нему доступ небезопасно.
    admin_username: str = "Cxentrall"

    # Настоящий признак админа — неизменяемый Telegram ID. Список через запятую:
    #   ADMIN_TELEGRAM_IDS=123456789,987654321
    # Пусто в проде = админов нет; при первом старте бэкенд напишет в лог, чей
    # ID вписать (см. routers/auth.py). ID узнать: @userinfobot.
    admin_telegram_ids: str = ""

    # Разрешить вход админа по username (небезопасно, только для локальной
    # разработки, когда ID ещё неизвестен). В проде всегда False.
    admin_username_fallback: bool = False

    # Открытая регистрация: любой честно подписанный вход из Telegram создаёт
    # аккаунт с пробным тарифом. Белый список при этом никуда не девается — он
    # остаётся способом выдать доступ вручную. False возвращает старое
    # поведение «пускаем только своих».
    open_registration: bool = True
    # Сколько дней живёт пробный доступ.
    trial_days: int = 14
    # Сколько пробных аккаунтов можно завести с одного адреса за сутки. Против
    # регистрации пачками; 0 — не ограничивать.
    trial_max_per_ip: int = 3

    # Tunables that used to be scattered as module-level constants.
    jwt_ttl_days: int = 7
    telegram_auth_max_age_seconds: int = 86_400
    max_offer_image_bytes: int = 800_000

    # Telegram-gateway: отдельный процесс с настоящими MTProto-сессиями.
    # Бэкенд ходит сюда, чтобы отправить тестовое сообщение на один username.
    # Само сообщение шлёт аккаунт пользователя из tg-mini-client, не бэкенд.
    tg_client_url: str = "http://localhost:3000"
    # Общий секрет для /send у tg-mini-client. Пусто — заголовок не отправляем
    # (клиент тогда тоже не требует его). Совпадает с TG_CLIENT_SECRET клиента.
    tg_client_secret: str = ""

    # Необязательный мониторинг ошибок. Пусто — Sentry не инициализируется.
    sentry_dsn: str = ""

    # ИИ настраивается только через окружение. В админке этих полей нет: ключ не
    # попадает ни в БД, ни в браузер, ни в резервную копию пользовательских данных.
    ai_base_url: str = "https://freeinference.org/v1"
    ai_model: str = "deepseek-v4-flash"
    # Пусто — ИИ-функции покажут, что провайдер ещё не подключён владельцем.
    ai_api_key: str = ""

    # Сколько СОБСТВЕННЫХ прокси стоит перед бэкендом и дописывает X-Forwarded-For.
    #   1 — только nginx (запуск без TLS);
    #   2 — Caddy + nginx (docker compose --profile tls);
    #   3 — Cloudflare/внешний LB + Caddy + nginx.
    # От этого числа зависит, какой элемент цепочки считать адресом клиента
    # (см. access.client_ip). Завышенное значение безопаснее заниженного.
    # Дефолт 2 = документированный прод-профиль (Caddy + nginx): при 1 адресом
    # клиента считался бы контейнер Caddy — ОДИН на всех, и первые же 30
    # запросов любого пользователя закрывали бы вход/ИИ/поиск для всего мира,
    # а антимультиаккаунт видел бы всех как один IP.
    trusted_proxy_hops: int = 2

    # Максимальный размер тела запроса (байты). Защита от «залей гигабайт JSON
    # и съешь память». Импорт лидов (CSV) идёт мимо — у него свой лимит.
    max_request_body_bytes: int = 2_000_000

    # Глобальный переключатель пауз. При max > 0 диапазон берётся из настроек
    # конкретного аккаунта; max = 0 отключает ожидание для тестов и отладки.
    send_delay_min_seconds: float = 4.0
    send_delay_max_seconds: float = 9.0

    # Пул соединений SQLAlchemy. Дефолт SQLAlchemy (5+10) меньше, чем пул потоков
    # uvicorn (40), поэтому под нагрузкой запросы вставали в очередь за соединением.
    db_pool_size: int = 10
    db_max_overflow: int = 20
    db_pool_recycle_seconds: int = 1800

    # Логи в JSON (удобно для Loki/CloudWatch) или человекочитаемые строки.
    log_json: bool = True
    log_level: str = "INFO"

    # Простой in-memory rate limit (окно/лимиты). Значения — «запросов за window_seconds».
    rate_limit_enabled: bool = True
    rate_limit_window_seconds: int = 60
    rate_limit_auth: int = 20  # /api/auth/telegram
    rate_limit_ai: int = 20  # /api/ai/chat
    rate_limit_key_check: int = 15  # /api/keys/{id}/check
    rate_limit_search: int = 30  # /api/search, /api/topics
    rate_limit_test_send: int = 10  # /api/campaigns/test-send
    # Переписка: человек листает диалоги и открывает чаты чаще, чем ищет
    # лидов, но каждый запрос уходит в Telegram, где свои флуд-лимиты.
    rate_limit_chat: int = 90  # /api/chat/*
    # Обращения в поддержку. Ограничение защищает не сервер, а владельца: без него
    # один раздражённый человек засыпает его Telegram десятками сообщений.
    rate_limit_support: int = 6  # POST /api/support/messages
    # Публичный расчёт цены с промокодом: без лимита код подбирается перебором
    # на скорости сети (ручка не требует входа).
    rate_limit_quote: int = 30  # POST /api/plans/quote

    # --- Волна 6: наблюдаемость и админ-безопасность ---

    # Алерты в Telegram админам при 5xx. Используют тот же бот и тех же
    # админов, что и вход — отдельного бота заводить не нужно. Молчат, если
    # бот не настроен или список админов пуст.
    telegram_alerts_enabled: bool = True
    # Не чаще одного одинакового алерта за этот интервал — защита от шторма
    # уведомлений при повторяющейся ошибке.
    telegram_alert_min_interval_seconds: int = 300

    # Токен для /metrics: если задан, эндпоинт требует заголовок
    # X-Metrics-Token с этим значением. Пусто — эндпоинт открыт (тогда его
    # стоит закрыть на уровне Caddy/nginx, как /api/docs).
    metrics_token: str = ""

    # IP-allowlist для админ-эндпоинтов (/api/admin/*, а также любых других
    # ручек за require_admin). Список через запятую, поддерживает CIDR:
    #   ADMIN_IP_ALLOWLIST=203.0.113.10,198.51.100.0/24
    # Пусто = ограничения нет (как раньше — только проверка username/ID).
    admin_ip_allowlist: str = ""

    @property
    def admin_ip_networks(self) -> list:
        """Разобранные сети/адреса из ADMIN_IP_ALLOWLIST. Некорректные записи молча пропускаются."""
        nets = []
        for chunk in (self.admin_ip_allowlist or "").split(","):
            chunk = chunk.strip()
            if not chunk:
                continue
            try:
                nets.append(ipaddress.ip_network(chunk, strict=False))
            except ValueError:
                continue
        return nets

    def is_ip_allowed_for_admin(self, ip: str) -> bool:
        """True, если IP допущен к админ-эндпоинтам.

        Пустой ADMIN_IP_ALLOWLIST означает «ограничения нет» — так апгрейд не
        ломает существующие проды, где эта переменная ещё не задана.
        """
        nets = self.admin_ip_networks
        if not nets:
            return True
        if not ip:
            return False
        try:
            addr = ipaddress.ip_address(ip)
        except ValueError:
            return False
        return any(addr in net for net in nets)

    @property
    def demo_mode(self) -> bool:
        """True when Telegram credentials are missing: the UI offers a demo login."""
        return not (self.bot_token and self.public_bot_username)

    @property
    def public_bot_id(self) -> int | None:
        """Числовой id бота — часть BOT_TOKEN до двоеточия.

        Не секрет: Telegram сам публикует его в ссылке виджета входа. Фронтенду
        он нужен, чтобы собрать прямую ссылку на oauth.telegram.org, когда
        официальный виджет не загрузился (блокировщики, фильтрация провайдера).
        """
        head = self.bot_token.split(":", 1)[0].strip()
        return int(head) if head.isdigit() else None

    @property
    def public_bot_username(self) -> str:
        """Bot username as the login widget expects it — without a leading '@'."""
        return self.bot_username.strip().lstrip("@")

    @property
    def admin_contact(self) -> str:
        """Публичный @username для оплаты — его показываем в тарифах."""
        return self.admin_username.strip().lstrip("@")

    @property
    def admin_ids(self) -> frozenset[int]:
        """Множество Telegram ID администраторов из ADMIN_TELEGRAM_IDS."""
        out: set[int] = set()
        for chunk in (self.admin_telegram_ids or "").replace(";", ",").split(","):
            chunk = chunk.strip()
            if not chunk:
                continue
            try:
                out.add(int(chunk))
            except ValueError:
                continue
        return frozenset(out)

    def is_admin(self, username: str | None, telegram_id: int | None = None) -> bool:
        """True, если пользователь — администратор.

        Основной критерий — telegram_id из ADMIN_TELEGRAM_IDS: его нельзя
        подменить. Сверка по username оставлена запасным путём и работает,
        только когда явно включён ADMIN_USERNAME_FALLBACK=1 (локальная
        разработка) или список ID пуст И включён демо-режим.
        """
        ids = self.admin_ids
        if telegram_id is not None and telegram_id in ids:
            return True

        allow_username = self.admin_username_fallback or (not ids and self.demo_mode)
        if not allow_username:
            return False
        target = self.admin_username.strip().lstrip("@").lower()
        return bool(target) and (username or "").strip().lstrip("@").lower() == target

    @property
    def jwt_secret_ok(self) -> bool:
        """True, если JWT_SECRET достаточно силён для прода.

        Логика (без утечки значения): секрет не должен быть пустым, короче
        _MIN_JWT_SECRET_LEN или одним из известных шаблонных значений.
        """
        secret = (self.jwt_secret or "").strip()
        if len(secret) < _MIN_JWT_SECRET_LEN:
            return False
        if secret in _INSECURE_JWT_SECRETS:
            return False
        return True

    def validate_jwt_secret(self) -> None:
        """Упасть при старте, если секрет подписи JWT слабый (только в проде).

        Fail-fast в lifespan: не даём поднять сервис с секретом, который знают
        все. Демо-режим (нет бота) пропускаем — локальный запуск без реальной
        авторизации секрет не требует. Тесты задают короткий JWT_SECRET и
        поднимают приложение через TestClient БЕЗ lifespan — их это не касается.
        Старый аварийный клапан для слабого JWT удалён намеренно: он делал
        fail-fast бессмысленным — достаточно было одной переменной окружения,
        чтобы поднять прод со слабым секретом.
        """
        if self.demo_mode:
            return
        if self.jwt_secret_ok:
            return
        raise RuntimeError(
            "JWT_SECRET слишком слабый для прода. Сгенерируйте случайный:\n"
            '  python -c "import secrets; print(secrets.token_urlsafe(48))"\n'
            "и впишите его в .env (переменная JWT_SECRET). Текущее значение не "
            "показываем и не логируем. ВАЖНО: если бот не настроен (демо-режим), "
            "проверка пропускается."
        )


settings = Settings()
