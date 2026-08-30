"""Talaria API application factory."""

import asyncio
import logging
import re
import time
from contextlib import asynccontextmanager
from urllib.parse import unquote, urlsplit

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from . import alerts, metrics
from .access import bind_request_ip
from .config import settings
from .events_hub import hub
from .logging_setup import (
    begin_request_user,
    end_request_user,
    new_request_id,
    request_id_var,
    setup_logging,
)
from .redaction import redact_text, redact_value, register_secret
from .routers import (
    admin,
    ai,
    auth,
    campaigns,
    chat,
    events,
    leads,
    offers,
    search,
    stats,
    subscriptions,
    support,
    system,
    warmup,
    ws,
)
from .routers import (
    metrics as metrics_router,
)
from .routers import (
    settings as settings_router,
)
from .security import decode_claims
from .services.errors import DomainError

logger = logging.getLogger("outly")

# Пути, которые не нужно писать в access-лог: health и metrics дёргают Docker/Prometheus
# каждые 10–30 с, и они одни забивали бы весь журнал.
_QUIET_PATHS = frozenset({"/api/health", "/metrics"})
_REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9._:-]{1,64}$")


def _register_configured_secrets() -> None:
    register_secret(
        settings.bot_token,
        settings.jwt_secret,
        settings.tg_client_secret,
        settings.ai_api_key,
        settings.metrics_token,
        settings.sentry_dsn,
        settings.database_url,
    )
    try:
        parsed = urlsplit(settings.database_url)
        register_secret(
            parsed.username,
            parsed.password,
            unquote(parsed.username or ""),
            unquote(parsed.password or ""),
        )
    except Exception:
        register_secret(settings.database_url)


def _sentry_before_send(event: dict, _hint: object) -> dict:
    safe = redact_value(event)
    return safe if isinstance(safe, dict) else {"message": "redaction_failed"}


def _sentry_before_breadcrumb(breadcrumb: dict, _hint: object) -> dict:
    safe = redact_value(breadcrumb)
    return safe if isinstance(safe, dict) else {"message": "redaction_failed"}


def _init_sentry() -> None:
    """Инициализировать Sentry, если задан DSN. Без DSN — тихо пропускаем."""
    if not settings.sentry_dsn:
        logger.info("Sentry не подключён: SENTRY_DSN пуст")
        return
    try:
        import sentry_sdk

        sentry_sdk.init(
            dsn=settings.sentry_dsn,
            traces_sample_rate=0.0,
            before_send=_sentry_before_send,
            before_breadcrumb=_sentry_before_breadcrumb,
            # Не отправлять в Sentry тела запросов, заголовки и локальные
            # переменные: там токены, ключи и персональные данные лидов.
            send_default_pii=False,
            include_local_variables=False,
            max_request_body_size="never",
        )
        logger.info("Sentry подключён")
    except Exception as exc:  # пакет может быть не установлен — это не критично
        logger.warning("Sentry не инициализирован: %s", exc)


# Как часто проверять, у кого заканчивается тариф. Час — компромисс: чаще
# бессмысленно (этапы измеряются днями), реже — и напоминание «за день»
# рискует уйти уже после окончания.
EXPIRY_CHECK_SECONDS = 3600


async def _expiry_notice_loop() -> None:
    """Фоновый обход подписок с напоминанием об окончании тарифа.

    Живёт в самом приложении, а не в отдельном сервисе или cron: задача одна,
    выполняется секунды и не требует ни очереди, ни воркера. Любая ошибка
    внутри гасится — цикл обязан пережить недоступную базу и продолжить работу
    после её возвращения.
    """
    from .database import SessionLocal
    from .services.notices import send_expiry_notices

    while True:
        try:
            await asyncio.sleep(EXPIRY_CHECK_SECONDS)
            with SessionLocal() as session:
                sent = await asyncio.to_thread(send_expiry_notices, session)
            if sent:
                logger.info("Напоминаний об окончании тарифа отправлено: %d", sent)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 — цикл не должен умирать от одной ошибки
            logger.warning("Обход напоминаний о тарифе не удался: %s", type(exc).__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    _register_configured_secrets()
    setup_logging(json_output=settings.log_json, level=settings.log_level)

    # Fail-fast: не поднимаемся с шаблонным JWT_SECRET в проде (демо-режим
    # пропускается внутри).
    settings.validate_jwt_secret()
    _init_sentry()

    # init_db() убран намеренно: Base.metadata.create_all() создавал таблицы в
    # обход Alembic. Схему приводит к актуальной только `alembic upgrade head`
    # из docker-entrypoint.sh — иначе миграции и реальная схема расходятся.
    if not settings.admin_ids:
        logger.warning(
            "ADMIN_TELEGRAM_IDS не задан — админ-панель недоступна никому. "
            "Узнайте свой Telegram ID у @userinfobot и впишите его в .env."
        )

    # Отдаём шине событий главный event loop: из него она будит WebSocket-очереди
    # при publish() из синхронных обработчиков (те бегут в пуле потоков).
    loop = asyncio.get_running_loop()
    hub.bind_loop(loop)

    # Рассылки, «идущие» с прошлого запуска, уже мертвы: поток отправки не
    # переживает рестарт. Помечаем их прерванными, чтобы интерфейс не показывал
    # вечное «Идёт». Ошибки здесь не должны мешать подъёму сервера: без БД
    # приложение всё равно упадёт дальше более понятным образом.
    try:
        from .database import SessionLocal
        from .routers.campaigns import recover_interrupted
        from .routers.leads import recover_verification_jobs, spawn_verification_job

        db = SessionLocal()
        try:
            stuck = recover_interrupted(db)
            if stuck:
                logger.warning("Помечено прерванными рассылок после рестарта: %d", stuck)
            verification_jobs = recover_verification_jobs(db)
        finally:
            db.close()
        for job_id in verification_jobs:
            spawn_verification_job(job_id)
        if verification_jobs:
            logger.warning(
                "Продолжена Telegram-верификация после рестарта: %d",
                len(verification_jobs),
            )
    except Exception as exc:
        logger.warning("Восстановление фоновых задач не удалось: %s", exc)

    notices_task = asyncio.create_task(_expiry_notice_loop(), name="expiry-notices")

    try:
        yield
    finally:
        notices_task.cancel()
        try:
            await notices_task
        except asyncio.CancelledError:
            pass


app = FastAPI(
    title="Talaria API",
    lifespan=lifespan,
    # Схему API наружу в проде не отдаём: она подробно описывает все ручки и
    # облегчает разведку. Локально (демо-режим) — оставляем, удобно.
    docs_url="/api/docs" if settings.demo_mode else None,
    redoc_url=None,
    openapi_url="/api/openapi.json" if settings.demo_mode else None,
)

# Сжатие ответов покрупнее (списки лидов, тарифы) — заметно легче по сети.
app.add_middleware(GZipMiddleware, minimum_size=1024)


# Методы, которые ничего не меняют. Всё остальное в режиме просмотра запрещено.
_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


@app.middleware("http")
async def readonly_view_guard(request: Request, call_next):
    """Запрет изменений для режима «смотреть глазами пользователя».

    Проверка стоит в middleware, а не в зависимости конкретных ручек, сознательно:
    зависимость надо не забыть повесить на каждый новый маршрут, и первая же
    забытая ручка сделает «только чтение» неправдой. Здесь через эту точку
    проходит весь трафик без исключений.
    """
    if request.method not in _SAFE_METHODS:
        header = request.headers.get("authorization") or ""
        raw = header[7:].strip() if header[:7].lower() == "bearer " else ""
        claims = decode_claims(raw) if raw else None
        if claims and claims.get("ro"):
            return JSONResponse(
                status_code=403,
                content={
                    "detail": "Режим просмотра: изменения запрещены. "
                    "Выйдите из просмотра, чтобы что-то менять."
                },
            )
    return await call_next(request)


@app.middleware("http")
async def request_context(request: Request, call_next):
    """Сквозной request-id, access-лог и лимит на размер тела запроса.

    request-id берём из заголовка X-Request-Id, если его проставил прокси, иначе
    генерируем. Возвращаем клиенту тем же заголовком: если пользователь
    присылает скриншот с ошибкой, по этому идентификатору сразу находится
    полный след запроса в логах.
    """
    incoming = (request.headers.get("x-request-id") or "").strip()[:64]
    # Не переносим в логи произвольные управляющие символы из заголовка.
    request_id = incoming if _REQUEST_ID_RE.fullmatch(incoming) else new_request_id()
    token = request_id_var.set(request_id)
    user_token = begin_request_user()
    request.state.request_id = request_id
    # Адрес запоминаем здесь один раз: журнал действий берёт его из контекста,
    # и ни один вызов log_event не обязан знать про request.
    bind_request_ip(request)

    try:
        # Ранний отказ по Content-Length: не читаем тело целиком, чтобы узнать,
        # что оно слишком большое.
        raw_length = request.headers.get("content-length")
        # Multipart CSV импортируется через UploadFile (временный spool-файл) и
        # сам читает не больше 5 МБ; общий JSON-потолок к нему не применяем.
        is_csv_import = request.url.path == "/api/leads/import"
        if raw_length and raw_length.isdigit() and not is_csv_import:
            if int(raw_length) > settings.max_request_body_bytes:
                return JSONResponse(
                    status_code=413,
                    content={
                        "detail": "Тело запроса слишком большое",
                        "request_id": request_id,
                    },
                    headers={
                        "X-Request-Id": request_id,
                        "X-Content-Type-Options": "nosniff",
                        "X-Frame-Options": "SAMEORIGIN",
                        "Referrer-Policy": "strict-origin-when-cross-origin",
                        "Permissions-Policy": (
                            "camera=(), microphone=(), geolocation=(), payment=()"
                        ),
                    },
                )

        started = time.perf_counter()
        response = await call_next(request)
        elapsed_seconds = time.perf_counter() - started
        elapsed_ms = elapsed_seconds * 1000
        response.headers["X-Request-Id"] = request_id
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        response.headers.setdefault(
            "Permissions-Policy", "camera=(), microphone=(), geolocation=(), payment=()"
        )
        if request.url.path.startswith(("/api/auth", "/api/admin")):
            response.headers.setdefault("Cache-Control", "no-store")

        # Шаблон роута (например "/api/leads/{lead_id}"), а не сырой URL — иначе
        # кардинальность метрик растёт без ограничений. Случайные пути (404 без
        # совпавшего роута) сводятся в одну метку metrics.UNMATCHED_PATH.
        route = request.scope.get("route")
        path_template = getattr(route, "path", None) or metrics.UNMATCHED_PATH
        metrics.observe_request(
            request.method, path_template, response.status_code, elapsed_seconds
        )

        if request.url.path not in _QUIET_PATHS:
            logger.info(
                "%s %s -> %s",
                request.method,
                request.url.path,
                response.status_code,
                extra={
                    "http_method": request.method,
                    "http_path": request.url.path,
                    "http_status": response.status_code,
                    "duration_ms": round(elapsed_ms, 1),
                },
            )
        return response
    finally:
        request_id_var.reset(token)
        end_request_user(user_token)


@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request: Request, exc: StarletteHTTPException):
    """Единый формат ответа об ошибке: detail + request_id.

    Раньше формат отличался у HTTPException, ошибок валидации и падений — на
    фронте это разбиралось тремя разными ветками.
    """
    request_id = request_id_var.get() or "-"
    safe_detail = redact_value(exc.detail)
    if exc.status_code >= 500:
        logger.error(
            "HTTP %s на %s: %s",
            exc.status_code,
            request.url.path,
            redact_text(safe_detail),
        )
        alerts.report_error(
            "API",
            "http",
            f"Запрос завершился ошибкой сервера {exc.status_code}",
            entity=f"{request.method} {request.url.path}",
            details={
                "Ответ клиенту": safe_detail,
                "Код запроса": request_id,
                "Параметры": redact_text(request.url.query or "нет"),
            },
            dedupe_key=f"http:{exc.status_code}:{request.url.path}",
        )
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": safe_detail, "request_id": request_id},
        headers={"X-Request-Id": request_id, **(exc.headers or {})},
    )


@app.exception_handler(DomainError)
async def domain_error_handler(request: Request, exc: DomainError):
    """Adapt service failures to the existing HTTP error contract."""
    return await http_exception_handler(
        request,
        StarletteHTTPException(
            status_code=exc.status_code,
            detail=exc.detail,
            headers=exc.headers,
        ),
    )


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    """422 в том же формате + краткая расшифровка, какое поле не подошло."""
    request_id = request_id_var.get() or "-"
    problems = []
    for err in exc.errors():
        field = ".".join(str(part) for part in err.get("loc", []) if part != "body")
        problems.append(
            {
                "field": redact_text(field or "body"),
                "message": redact_text(err.get("msg", "")),
            }
        )
    return JSONResponse(
        status_code=422,
        content={
            "detail": "Проверьте заполненные поля",
            "errors": problems,
            "request_id": request_id,
        },
        headers={"X-Request-Id": request_id},
    )


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    """Непойманное падение: полный трейсбек — в лог, наружу — только request_id.

    Раньше такие ошибки уходили клиенту текстом исключения (например, строкой
    ответа провайдера ИИ), что раскрывало внутреннее устройство сервиса.
    """
    request_id = getattr(request.state, "request_id", None) or request_id_var.get() or "-"
    token = request_id_var.set(request_id)
    try:
        logger.exception("Необработанная ошибка на %s %s", request.method, request.url.path)
        alerts.report_error(
            "API",
            "http",
            "Необработанное исключение — запрос упал до ответа",
            exc=exc,
            entity=f"{request.method} {request.url.path}",
            details={
                "Код запроса": request_id,
                "Параметры": redact_text(request.url.query or "нет"),
            },
            dedupe_key=f"exc:{type(exc).__name__}:{request.url.path}",
        )
        return JSONResponse(
            status_code=500,
            content={
                "detail": "Внутренняя ошибка сервера. Сообщите код в поддержку.",
                "request_id": request_id,
            },
            headers={"X-Request-Id": request_id},
        )
    finally:
        request_id_var.reset(token)


app.include_router(system.router)
app.include_router(auth.router)
app.include_router(subscriptions.router)
app.include_router(offers.router)
app.include_router(campaigns.router)
app.include_router(chat.router)
app.include_router(settings_router.router)
app.include_router(events.router)
app.include_router(stats.router)
app.include_router(search.router)
app.include_router(leads.router)
app.include_router(warmup.router)
app.include_router(ai.router)
app.include_router(admin.router)
app.include_router(support.router)
app.include_router(metrics_router.router)
app.include_router(ws.router)
