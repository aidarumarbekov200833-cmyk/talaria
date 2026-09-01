"""SQLAlchemy ORM models."""

from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import relationship

from .database import Base


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True)
    telegram_id = Column(BigInteger, unique=True, index=True, nullable=False)
    username = Column(String(64))
    first_name = Column(String(128))
    last_name = Column(String(128))
    photo_url = Column(String(512))
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    # --- Настройки AI-оффера ---
    # Скелет оффера: «примерный вид» письма, по которому ИИ пишет каждому лиду
    # свой текст. Настраивается один раз на странице «Офферы» и оттуда же
    # берётся рассылкой — в самой рассылке выбирать больше нечего, там только
    # переключатель «обычный оффер / ИИ-оффер».
    ai_offer_template = Column(Text, default="")
    # Сколько последних материалов канала (видео/постов) ИИ смотрит перед тем,
    # как писать. Колонкой, а не константой: цена запроса растёт с их числом.
    ai_offer_materials = Column(Integer, default=5, nullable=False, server_default="5")

    @property
    def display_name(self) -> str:
        full = f"{self.first_name or ''} {self.last_name or ''}".strip()
        return full or (self.username or "")


class Offer(Base):
    __tablename__ = "offers"

    id = Column(Integer, primary_key=True)
    owner_id = Column(Integer, ForeignKey("users.id"), index=True, nullable=False)
    name = Column(String(200), nullable=False)
    text = Column(Text, default="")
    image = Column(Text, default="")
    # Альбом: JSON-список data-URL. Просьба владельца «медиа оффер несколько
    # картинок → альбом»: 2–10 фото уходят лиду одним сообщением. Поле image
    # хранит первую картинку — на неё смотрят предпросмотр и старые записи.
    images = Column(Text, default="[]", server_default="[]")
    # user | ai — кто написал текст. Помощник умеет сохранять офферы сам, и
    # человек должен видеть, что этот текст не его: отправлять незнакомое
    # письмо сотням людей он согласия не давал.
    created_by = Column(String(20), default="user", nullable=False, server_default="user")
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class Campaign(Base):
    __tablename__ = "campaigns"

    id = Column(Integer, primary_key=True)
    owner_id = Column(Integer, ForeignKey("users.id"), index=True, nullable=False)
    name = Column(String(200), nullable=False)
    database_name = Column(String(200), nullable=False)
    channel = Column(String(50), default="Telegram")
    # running | paused | completed | scheduled
    status = Column(String(50), default="running")
    progress = Column(Float, default=0.0)  # percent, 0-100
    total_recipients = Column(Integer, default=0)
    sent_count = Column(Integer, default=0)
    error_count = Column(Integer, default=0)
    offer_ids = Column(Text, default="[]")  # JSON-encoded list of offer ids

    # Тип оффера: regular | ai. Логика отправки у них разная, и статистику
    # надо сравнивать по типам, поэтому тип хранится колонкой, а не выводится из
    # того, пуст ли ai_template.
    offer_mode = Column(String(20), default="regular", nullable=False, server_default="regular")
    # Шаблон AI-оффера с плейсхолдерами — «примерный вид», по которому ИИ пишет
    # индивидуальный текст каждому лиду. Храним при кампании: по нему потом видно,
    # что именно отправлялось, если пользователь потом перепишет шаблон.
    ai_template = Column(Text, default="")
    # Спинтакс — только для обычных офферов (у AI вариативность и так полная).
    spintax = Column(Boolean, default=False, nullable=False, server_default="0")
    # С каких подключённых аккаунтов шла рассылка (JSON-список id WarmupAccount).
    account_ids = Column(Text, default="[]")
    # Полный список получателей, отобранных при запуске (JSON-список id Lead).
    # Нужен для автовозобновления после рестарта: остаток = этот список минус
    # те, у кого уже есть строка в campaign_sends. Без него после рестарта
    # невозможно честно узнать, кому ещё не писали.
    lead_ids = Column(Text, default="[]", nullable=False, server_default="[]")
    # Сколько материалов канала ИИ читает перед генерацией оффера. Хранится при
    # кампании, чтобы возобновление шло с теми же настройками, что и запуск.
    ai_materials = Column(Integer, default=5, nullable=False, server_default="5")
    # Сколько лидов ответило. Счётчик колонкой, чтобы список кампаний не делал
    # подзапрос на каждую строку.
    reply_count = Column(Integer, default=0, nullable=False, server_default="0")
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    started_at = Column(DateTime(timezone=True))
    completed_at = Column(DateTime(timezone=True))

    logs = relationship(
        "CampaignLog",
        back_populates="campaign",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class CampaignLog(Base):
    __tablename__ = "campaign_logs"
    __table_args__ = (
        # Журнал всегда читается одинаково: фильтр по campaign_id плюс сортировка
        # по времени вниз. Одиночного индекса по campaign_id для этого мало:
        # база отберёт все строки кампании (а их столько же, сколько
        # отправленных сообщений) и отсортирует их в памяти ради одной страницы.
        Index("ix_campaign_logs_campaign_ts", "campaign_id", "timestamp"),
    )

    id = Column(Integer, primary_key=True)
    campaign_id = Column(
        Integer,
        ForeignKey("campaigns.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    timestamp = Column(DateTime(timezone=True), server_default=func.now())
    message = Column(Text, nullable=False)
    level = Column(String(20), default="info")  # info | warning | error

    campaign = relationship("Campaign", back_populates="logs")


class ApiKey(Base):
    """A YouTube Data API key.

    Quota reality check, because the UI must not lie about it:
    Google grants quota per *Cloud project*, not per key. Two keys from one
    project share a single 10 000-unit daily budget, so `project_label` lets us
    sum only distinct projects. There is also no API that reports remaining
    quota — that number exists solely in the Cloud Console. We therefore meter
    spending ourselves in QuotaUsage using Google's documented per-call costs.
    """

    __tablename__ = "api_keys"

    id = Column(Integer, primary_key=True)
    owner_id = Column(Integer, ForeignKey("users.id"), index=True, nullable=False)
    label = Column(String(120), default="")
    key = Column(String(200), nullable=False)
    project_label = Column(String(120), default="")
    daily_quota = Column(Integer, default=10_000)
    enabled = Column(Boolean, default=True)
    # unchecked | ok | invalid | quota_exceeded | error
    status = Column(String(30), default="unchecked")
    status_detail = Column(Text, default="")
    last_checked_at = Column(DateTime(timezone=True))
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    usage = relationship(
        "QuotaUsage",
        back_populates="api_key",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class QuotaUsage(Base):
    """Units spent on one key during one quota day.

    YouTube resets quota at midnight Pacific Time, so `day` holds a Pacific
    date rather than a UTC one — otherwise the counter would reset hours early.
    """

    __tablename__ = "quota_usage"
    __table_args__ = (UniqueConstraint("api_key_id", "day", name="uq_quota_key_day"),)

    id = Column(Integer, primary_key=True)
    api_key_id = Column(
        Integer,
        ForeignKey("api_keys.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    day = Column(Date, nullable=False, index=True)
    units = Column(Integer, default=0)
    calls = Column(Integer, default=0)
    # О каком пороге расхода (в процентах) владельцу уже сообщили сегодня.
    # Живёт вместе со строкой расхода: обе умирают на тихоокеанской полуночи,
    # поэтому отдельная чистка состояния не нужна.
    notified_pct = Column(Integer, nullable=False, default=0, server_default="0")

    api_key = relationship("ApiKey", back_populates="usage")


class LeadDatabase(Base):
    """A set of leads produced by one search run."""

    __tablename__ = "lead_databases"
    __table_args__ = (
        # Индекс создан миграцией 0014, но в модели его не было. Схема для тестов
        # строится из моделей, поэтому расхождение означало, что в рабочей базе
        # индекс есть, а в проверках — нет, и заметить его случайную потерю нечем.
        Index("ix_lead_databases_owner_pipeline", "owner_id", "pipeline_status"),
    )

    id = Column(Integer, primary_key=True)
    owner_id = Column(Integer, ForeignKey("users.id"), index=True, nullable=False)
    name = Column(String(200), nullable=False)
    source = Column(String(50), default="youtube")
    # trends | niches | custom
    mode = Column(String(30), default="custom")
    query = Column(Text, default="")  # keywords actually sent to YouTube
    # Снимок фильтров поиска. Храним JSON рядом с базой, чтобы через месяц было
    # понятно, почему одни каналы попали в выборку, а другие — нет.
    filters_json = Column(Text, default="{}", nullable=False, server_default="{}")
    # youtube_ready | verifying | ready | partial. Это состояние pipeline, а не
    # состояние фоновой задачи: оно пересчитывается после каждой верификации.
    pipeline_status = Column(
        String(30), default="youtube_ready", nullable=False, server_default="youtube_ready"
    )
    units_spent = Column(Integer, default=0)  # what this run cost in quota units
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    leads = relationship(
        "Lead",
        back_populates="database",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    verification_jobs = relationship(
        "LeadVerificationJob",
        back_populates="database",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="LeadVerificationJob.id.desc()",
    )


class Lead(Base):
    """One YouTube channel.

    Only fields YouTube actually returns are stored. There is deliberately no
    first/last name column: the API gives a channel title, not a person's name,
    and inventing one would put fake data in front of the user.
    """

    __tablename__ = "leads"
    __table_args__ = (
        UniqueConstraint("database_id", "channel_id", name="uq_lead_db_channel"),
        # Самый частый фильтр в интерфейсе — «лиды такой-то базы в таком-то
        # статусе», и он же нужен дедупликации рассылки (Волна 2), чтобы не
        # писать повторно тем, кому уже писали.
        Index("ix_leads_database_status", "database_id", "status"),
        Index(
            "ix_leads_tg_verified_account_checked_at",
            "tg_verified_by_account_id",
            "tg_checked_at",
        ),
    )

    id = Column(Integer, primary_key=True)
    database_id = Column(
        Integer,
        ForeignKey("lead_databases.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    channel_id = Column(String(64), nullable=False, index=True)
    title = Column(String(300), default="")
    description = Column(Text, default="")
    custom_url = Column(String(200), default="")
    country = Column(String(8), default="")
    thumbnail = Column(String(512), default="")
    subscribers = Column(Integer)  # NULL when the channel hides the count
    video_count = Column(Integer)
    view_count = Column(BigInteger)
    published_at = Column(DateTime(timezone=True))
    matched_keyword = Column(String(200), default="")

    # Профиль в Telegram, подтянутый через MTProto-клиент (tg-mini-client).
    # YouTube имени человека не знает — там только название канала. А пишем мы
    # в Telegram, и там имя с фамилией есть. Храним их рядом с лидом, чтобы
    # плейсхолдеры {first_name}/{last_name} подставлялись настоящими данными,
    # а не выдумкой, и чтобы не дёргать Telegram на каждый предпросмотр.
    tg_id = Column(String(40), default="", index=True)
    tg_username = Column(String(64), default="", index=True)
    tg_first_name = Column(String(128), default="")
    tg_last_name = Column(String(128), default="")
    # unknown | pending | verified | missing | channel | bot | rejected | error
    # unknown сохраняет совместимость со старыми импортами, где tg_username уже
    # был заполнен до появления отдельного статуса верификации.
    tg_status = Column(
        String(20), default="unknown", nullable=False, server_default="unknown", index=True
    )
    tg_kind = Column(String(20), default="")
    tg_error = Column(String(500), default="")
    tg_verified_by_account_id = Column(Integer)
    tg_checked_at = Column(DateTime(timezone=True))
    # Идентификатор последнего непрерывного прогона, который обработал лид.
    # Нужен, чтобы повторяемая ошибка не зацикливала фоновую задачу на одной
    # строке, а повторный запуск всё равно мог попробовать лид ещё раз.
    tg_verification_job_id = Column(Integer, index=True)

    # Кросс-контакты, достроенные после поиска (см. contacts.py). YouTube даёт канал,
    # а пишем мы в Telegram — без этого шага база к рассылке непригодна.
    # telegram_channel — именно канал автора (t.me/…), а tg_username — личный аккаунт,
    # куда уходит сообщение. Смешивать их нельзя: в канал в личку не напишешь.
    telegram_channel = Column(String(64), default="")
    telegram_url = Column(String(256), default="")
    contact_email = Column(String(200), default="")
    # Когда достраивали контакты. NULL = ещё не пробовали; заполнено с пустым
    # telegram_channel = пробовали и не нашли, второй раз квоту не тратим.
    contacts_checked_at = Column(DateTime(timezone=True))

    # new | contacted | replied | interested | unsubscribed
    status = Column(String(30), default="new", index=True)
    # Заметка владельца: «созвон в четверг», «просил написать в марте». Свободный
    # текст, а не структурированное поле: заранее неизвестно, что человек захочет
    # запомнить, а потеря такой мелочи стоит сделки.
    note = Column(Text, default="", nullable=False, server_default="")
    # Оценка привлекательности канала, 0–100. Считается из того, что реально
    # отдаёт YouTube (см. services.compute_lead_score), и хранится колонкой, а не
    # считается на лету: по ней надо сортировать и фильтровать в SQL с пагинацией,
    # а вычисляемое в Python поле сортировать можно только вытащив всю таблицу.
    score = Column(Integer, default=0, nullable=False, server_default="0", index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    database = relationship("LeadDatabase", back_populates="leads")

    @property
    def score_reasons(self) -> list[str]:
        # Локальный импорт не создаёт цикл models -> services -> models во время
        # загрузки приложения; свойство вызывается только при сериализации лида.
        from .services import explain_lead_score

        return explain_lead_score(
            self.subscribers,
            self.video_count,
            self.view_count,
            self.matched_keyword,
        )


class LeadVerificationJob(Base):
    """Один непрерывный прогон Telegram-верификации всей базы.

    HTTP-запрос только создаёт запись и запускает worker. Прогресс хранится в
    БД, поэтому вкладку можно закрыть, а после рестарта worker продолжит работу
    с первого ещё не обработанного лида.
    """

    __tablename__ = "lead_verification_jobs"
    __table_args__ = (
        Index("ix_lead_verification_owner_status", "owner_id", "status"),
        Index("ix_lead_verification_database_status", "database_id", "status"),
    )

    id = Column(Integer, primary_key=True)
    owner_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    database_id = Column(
        Integer,
        ForeignKey("lead_databases.id", ondelete="CASCADE"),
        nullable=False,
    )
    # queued | running | cancel_requested | cancelled | completed | error
    status = Column(String(30), default="queued", nullable=False, server_default="queued")
    retry_errors = Column(Boolean, default=False, nullable=False, server_default="0")
    total = Column(Integer, default=0, nullable=False, server_default="0")
    processed = Column(Integer, default=0, nullable=False, server_default="0")
    verified = Column(Integer, default=0, nullable=False, server_default="0")
    unresolved = Column(Integer, default=0, nullable=False, server_default="0")
    errors = Column(Integer, default=0, nullable=False, server_default="0")
    current_lead_id = Column(Integer)
    current_title = Column(String(300), default="")
    last_result = Column(String(500), default="")
    last_error = Column(String(1000), default="")
    started_at = Column(DateTime(timezone=True))
    completed_at = Column(DateTime(timezone=True))
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )

    database = relationship("LeadDatabase", back_populates="verification_jobs")


class SearchJob(Base):
    """Один прогон поиска каналов YouTube, живущий в БД, а не в HTTP-запросе.

    Раньше поиск шёл синхронно внутри запроса: рестарт сервера терял всё, что
    уже нашли (и потраченную квоту вместе с этим). Теперь HTTP-запрос только
    создаёт запись и запускает worker; прогресс — найденные каналы и число
    пройденных ключевых слов — коммитится по ходу, поэтому после рестарта
    worker продолжает с первого непройденного слова, не теряя найденное.
    """

    __tablename__ = "search_jobs"
    __table_args__ = (Index("ix_search_jobs_owner_status", "owner_id", "status"),)

    id = Column(Integer, primary_key=True)
    owner_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    # queued | running | completed | error
    status = Column(String(30), default="queued", nullable=False, server_default="queued")
    # Снимок SearchIn целиком: фильтры и имя базы нужны воркеру и после рестарта.
    payload = Column(Text, default="{}", nullable=False, server_default="{}")
    # Сколько ключевых слов уже пройдено полностью. Возобновление стартует со
    # следующего: прерванное слово идёт заново, но найденное не дублируется.
    keywords_done = Column(Integer, default=0, nullable=False, server_default="0")
    # Найденные каналы: channel_id -> ключевое слово. JSON, а не строки в
    # отдельной таблице: список живёт минуты и нужен целиком одним куском.
    found_json = Column(Text, default="{}", nullable=False, server_default="{}")
    current_keyword = Column(String(200), default="")
    stop_reason = Column(String(500), default="")
    error = Column(String(1000), default="")
    database_id = Column(Integer, ForeignKey("lead_databases.id", ondelete="SET NULL"))
    leads_count = Column(Integer, default=0, nullable=False, server_default="0")
    limit_total = Column(Integer, default=0, nullable=False, server_default="0")
    per_keyword = Column(Integer, default=0, nullable=False, server_default="0")
    started_at = Column(DateTime(timezone=True))
    completed_at = Column(DateTime(timezone=True))
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )


class TopicRun(Base):
    """One "generate topics" run, kept so the user can revisit what was found.

    `topics` is a JSON list of {topic, source, evidence} objects. Trend topics
    come from real mostPopular videos; niche topics from the picked niches. The
    source is recorded so the UI can state where each topic came from.
    """

    __tablename__ = "topic_runs"

    id = Column(Integer, primary_key=True)
    owner_id = Column(Integer, ForeignKey("users.id"), index=True, nullable=False)
    mode = Column(String(30), default="trends")  # trends | niches
    # Список регионов через запятую: «KZ,RU,US». Одиночный код тоже валиден.
    region = Column(String(64), default="KZ")
    niches = Column(Text, default="[]")  # JSON list of niche slugs
    topics = Column(Text, default="[]")  # JSON list of topic objects
    requested = Column(Integer, default=10)
    units_spent = Column(Integer, default=0)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class WarmupAccount(Base):
    """A Telegram account being warmed up.

    The application database never stores a session string or a 2FA password.
    The connected gateway uses this slot only for explicitly launched contact
    verification and campaigns; the warm-up journal records manual activity.
    """

    __tablename__ = "warmup_accounts"
    __table_args__ = (
        Index(
            "uq_warmup_primary_owner",
            "owner_id",
            unique=True,
            postgresql_where=text("is_primary = true"),
            sqlite_where=text("is_primary = 1"),
        ),
    )

    id = Column(Integer, primary_key=True)
    owner_id = Column(Integer, ForeignKey("users.id"), index=True, nullable=False)
    label = Column(String(120), nullable=False)
    note = Column(Text, default="")
    started_on = Column(Date)
    plan_days = Column(Integer, default=14)
    daily_send_limit = Column(Integer, default=30, nullable=False, server_default="30")
    delay_min_seconds = Column(Integer, default=20, nullable=False, server_default="20")
    delay_max_seconds = Column(Integer, default=60, nullable=False, server_default="60")
    sending_enabled = Column(Boolean, default=True, nullable=False, server_default="1")
    verification_enabled = Column(Boolean, default=True, nullable=False, server_default="1")
    # Профиль Telegram, которым владелец вошёл в Talaria. Он считается первым
    # аккаунтом, но сможет проверять лидов и отправлять сообщения только после
    # отдельного подключения пользовательской Telegram-сессии.
    is_primary = Column(Boolean, default=False, nullable=False, server_default="0")
    # active | paused | done
    status = Column(String(30), default="active", index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    # Кто именно подключён в Telegram-gateway. Заполняет сам
    # tg-mini-client после входа, поэтому человеку не нужно вбивать ник и подпись
    # руками. Строка сессии здесь НЕ хранится: она лежит только в том сервисе.
    tg_id = Column(String(40), index=True)
    tg_username = Column(String(64))
    tg_phone = Column(String(40))
    connected_at = Column(DateTime(timezone=True))

    # Рабочее состояние Telegram-сессии не смешивается с `status` прогрева.
    # ready | cooldown | limited | banned | reauth
    operational_status = Column(
        String(30), default="ready", nullable=False, server_default="ready", index=True
    )
    cooldown_until = Column(DateTime(timezone=True), index=True)
    last_tg_error = Column(String(500), default="", nullable=False, server_default="")
    last_tg_error_code = Column(String(64), default="", nullable=False, server_default="")
    last_tg_error_at = Column(DateTime(timezone=True))
    last_success_at = Column(DateTime(timezone=True))
    risk_score = Column(Integer, default=0, nullable=False, server_default="0")
    consecutive_errors = Column(Integer, default=0, nullable=False, server_default="0")
    # Снимок непрочитанных диалогов приходит напрямую из Telegram. Он нужен
    # автопаузе: резкий рост после рассылки может означать жалобы/служебные
    # уведомления, поэтому новый запуск лучше остановить до проверки человеком.
    unread_count = Column(Integer, default=0, nullable=False, server_default="0")
    unread_dialogs = Column(Integer, default=0, nullable=False, server_default="0")
    unread_growth = Column(Integer, default=0, nullable=False, server_default="0")
    safety_checked_at = Column(DateTime(timezone=True))

    # Результат явной проверки собственного аккаунта через официальный @SpamBot.
    # unknown | clear | limited | appeal_available | appeal_sent | error
    spambot_status = Column(String(30), default="unknown", nullable=False, server_default="unknown")
    spambot_detail = Column(Text, default="", nullable=False, server_default="")
    spambot_checked_at = Column(DateTime(timezone=True))
    spambot_appealed_at = Column(DateTime(timezone=True))

    logs = relationship(
        "WarmupLog",
        back_populates="account",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="WarmupLog.day",
    )


class WarmupLog(Base):
    """One day of the warm-up plan for one account."""

    __tablename__ = "warmup_logs"
    __table_args__ = (UniqueConstraint("account_id", "day", name="uq_warmup_account_day"),)

    id = Column(Integer, primary_key=True)
    account_id = Column(
        Integer,
        ForeignKey("warmup_accounts.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    day = Column(Integer, nullable=False)  # 1-based day of the plan
    date = Column(Date)
    target_messages = Column(Integer, default=0)
    done_messages = Column(Integer, default=0)
    target_actions = Column(Text, default="[]")  # JSON list of suggested actions
    done = Column(Boolean, default=False)
    note = Column(Text, default="")
    completed_at = Column(DateTime(timezone=True))

    account = relationship("WarmupAccount", back_populates="logs")


class EventLog(Base):
    """Server-side journal shown in the log panels next to the editors.

    The panels used to be filled by the browser with invented lines. They now
    read this table, so a line appears only when something actually happened.
    """

    __tablename__ = "event_logs"
    __table_args__ = (
        # Лента событий всегда строится как «события одного владельца, новые
        # сверху». Два отдельных индекса по owner_id и created_at такой запрос
        # нормально не закрывают — нужен составной. Особенно важно для админки
        # из Волны 3, где эта таблица станет самой большой в базе.
        Index("ix_event_logs_owner_created", "owner_id", "created_at"),
    )

    id = Column(Integer, primary_key=True)
    owner_id = Column(Integer, ForeignKey("users.id"), index=True, nullable=False)
    # search | leads | offers | campaigns | warmup | ai | settings
    scope = Column(String(30), default="app", index=True)
    level = Column(String(20), default="info")  # info | warn | error | success
    message = Column(Text, nullable=False)
    ref_type = Column(String(30), default="")  # campaign | offer | database | ...
    # С какого адреса пришёл запрос, породивший событие. Пусто у фоновых задач:
    # у них нет запроса. Заполняется автоматически из контекста запроса, чтобы
    # не тащить request через все вызовы log_event.
    ip = Column(String(64), default="", nullable=False, server_default="", index=True)
    ref_id = Column(Integer)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), index=True)


class Subscription(Base):
    """Подписка пользователя. Одна строка на пользователя.

    Оплата проходит вне системы (клиент пишет админу в личку), поэтому здесь нет
    ничего про платежи — только какой план активирован, кем и до какого срока.
    Флаг `blocked` живёт тоже здесь: так и «текущий план», и «доступ закрыт»
    хранятся в одной новой таблице, и существующую users не приходится менять.
    """

    __tablename__ = "subscriptions"

    id = Column(Integer, primary_key=True)
    owner_id = Column(Integer, ForeignKey("users.id"), unique=True, index=True, nullable=False)
    # none | pro | max | ultra (см. plans.PLANS). Бесплатного тарифа нет:
    # "none" — тариф ещё не активирован админом. Старые строки со значением
    # "free" читаются как "none" (см. plans.effective_plan_slug).
    plan = Column(String(20), default="none", nullable=False)
    # none | active | expired — справочно; фактический план считает
    # plans.effective_plan_slug
    status = Column(String(20), default="none")
    blocked = Column(Boolean, default=False, nullable=False)
    # До какого момента оплачен платный план. NULL — бессрочно/не задано.
    paid_until = Column(DateTime(timezone=True))
    note = Column(Text, default="")  # заметка админа (за что/когда платил)
    activated_by = Column(Integer)  # id админа, который активировал
    activated_at = Column(DateTime(timezone=True))
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())


class Payment(Base):
    """Полученные деньги. Одна строка — один платёж.

    Почему отдельная таблица, а не сумма в подписке. Подписка одна на человека
    и описывает текущее состояние: «активен до такого-то». Сумма в ней
    отвечала бы только на вопрос «сколько заплатили в последний раз», а
    владельцу сервиса нужен другой — «сколько получено за месяц и от кого».

    Оплата в Talaria ручная: человек переводит деньги, админ активирует тариф.
    Поэтому платёж заводит админ, а не платёжный шлюз, и запись не связана
    жёстко с подпиской: возврат, доплата и оплата за друга — обычные случаи,
    которые в схему «одна подписка = один платёж» не помещаются.
    """

    __tablename__ = "payments"
    __table_args__ = (Index("ix_payments_owner_paid", "owner_id", "paid_at"),)

    id = Column(Integer, primary_key=True)
    owner_id = Column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    # В минорных единицах (копейки, тиын). Целое, а не дробное: суммирование
    # выручки в плавающей точке даёт расхождение в копейки, которое потом
    # невозможно объяснить.
    amount_minor = Column(BigInteger, nullable=False, default=0, server_default="0")
    currency = Column(String(8), nullable=False, default="RUB", server_default="RUB")
    plan = Column(String(20), nullable=False, default="", server_default="")
    months = Column(Integer, nullable=False, default=1, server_default="1")
    promo_code = Column(String(40), nullable=False, default="", server_default="")
    note = Column(Text, nullable=False, default="", server_default="")
    created_by = Column(Integer)
    # Дата получения денег. Отдельно от created_at: платёж заводят и задним
    # числом, а в отчёте важно, когда заплатили, а не когда внесли в систему.
    paid_at = Column(DateTime(timezone=True), nullable=False, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    @property
    def amount(self) -> float:
        """Сумма в обычных единицах — только для показа, не для арифметики."""
        return round(int(self.amount_minor or 0) / 100, 2)


class PromoCode(Base):
    """Промокод со скидкой на покупку.

    Раньше код был константой в plan_catalog: чтобы завести новую акцию,
    требовался деплой. Теперь это строка в базе, которую заводит админ.

    Оплата ручная (клиент платит в личку, админ активирует тариф), поэтому
    «использование» кода фиксируется в момент активации подписки админом, а не
    при расчёте цены на сайте: посчитать показы формы — значит считать не то.
    """

    __tablename__ = "promo_codes"

    id = Column(Integer, primary_key=True)
    # Всегда в верхнем регистре: сравнение при вводе регистронезависимое.
    code = Column(String(40), unique=True, index=True, nullable=False)
    discount_pct = Column(Integer, default=0, nullable=False, server_default="0")
    active = Column(Boolean, default=True, nullable=False, server_default=text("true"))
    valid_until = Column(DateTime(timezone=True))
    # 0 — без ограничения.
    max_uses = Column(Integer, default=0, nullable=False, server_default="0")
    used_count = Column(Integer, default=0, nullable=False, server_default="0")
    note = Column(Text, default="", nullable=False, server_default="")
    created_by = Column(Integer)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    def usable_at(self, moment) -> bool:
        """Годен ли код прямо сейчас: включён, не просрочен, не исчерпан."""
        if not self.active or int(self.discount_pct or 0) <= 0:
            return False
        if self.valid_until is not None and self.valid_until < moment:
            return False
        limit = int(self.max_uses or 0)
        return not (limit and int(self.used_count or 0) >= limit)


class PlatformSettings(Base):
    """Глобальные настройки площадки. Строка ровно одна (id=1).

    Пока строки нет, действует значение из окружения. Так свежая установка
    ведёт себя предсказуемо, а админ может закрыть регистрацию одним кликом,
    не трогая docker compose.
    """

    __tablename__ = "platform_settings"

    id = Column(Integer, primary_key=True)
    open_registration = Column(Boolean, nullable=False, default=True, server_default=text("true"))
    updated_by = Column(Integer)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class LoginAttempt(Base):
    """Журнал попыток входа — и удачных, и отклонённых.

    Нужен для двух вещей: видеть, кто ломится в закрытую дверь, и ловить
    мультиаккаунты (один и тот же IP/устройство у разных telegram_id). Пишем
    сюда каждую попытку, включая те, что не дошли до создания пользователя,
    поэтому owner_id может быть NULL, а telegram_id хранится сырым числом.
    """

    __tablename__ = "login_attempts"

    id = Column(Integer, primary_key=True)
    owner_id = Column(Integer, index=True)  # NULL, если пользователь не создан
    telegram_id = Column(BigInteger, index=True)
    username = Column(String(64), default="")
    first_name = Column(String(128), default="")
    last_name = Column(String(128), default="")
    ok = Column(Boolean, default=False, nullable=False, index=True)
    # ok | not_whitelisted | blocked | hash_mismatch | expired | clock_skew |
    # hash_missing | auth_date_invalid | bot_token_missing | demo_mode
    reason = Column(String(40), default="", index=True)
    ip = Column(String(64), default="", index=True)
    user_agent = Column(Text, default="")
    created_at = Column(DateTime(timezone=True), server_default=func.now(), index=True)


class TokenBlacklist(Base):
    """Отозванные JWT (logout). Проверяется в get_current_user по jti.

    Храним до истечения токена — после `expires_at` запись бесполезна и её можно
    чистить. Это и есть «выход»: сам JWT остаётся валидным по подписи, но jti в
    этом списке — значит доступ закрыт.
    """

    __tablename__ = "token_blacklist"

    id = Column(Integer, primary_key=True)
    jti = Column(String(64), unique=True, index=True, nullable=False)
    owner_id = Column(Integer, index=True)
    expires_at = Column(DateTime(timezone=True))
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class UserSession(Base):
    """Активная сессия входа (одна запись на один выпущенный JWT).

    Заводится в auth_telegram при выдаче токена. Позволяет пользователю
    увидеть список своих устройств/входов и принудительно завершить любой из
    них (кроме текущего можно тоже — тогда он разлогинится при следующем
    запросе). Отзыв здесь дублируется записью в TokenBlacklist — именно
    блэклист реально закрывает доступ по jti, эта таблица только для
    отображения и истории.
    """

    __tablename__ = "user_sessions"

    id = Column(Integer, primary_key=True)
    owner_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    jti = Column(String(64), unique=True, index=True, nullable=False)
    ip = Column(String(64))
    user_agent = Column(String(256))
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    expires_at = Column(DateTime(timezone=True))
    revoked_at = Column(DateTime(timezone=True), nullable=True)
    # Когда этим сеансом пользовались в последний раз. Без него список входов
    # нечитаем: дата входа одна на месяц вперёд и не отвечает на вопрос
    # «какая из этих строк — мой телефон, а какая чужая».
    last_seen_at = Column(DateTime(timezone=True))

    __table_args__ = (Index("ix_user_sessions_owner_created", "owner_id", "created_at"),)


class AiConfig(Base):
    """Историческая таблица из старых миграций.

    Runtime больше её не читает: провайдер настраивается только через окружение.
    Модель оставлена, чтобы старые установки можно было мигрировать без удаления
    данных; админских API и интерфейса для этой таблицы нет.
    """

    __tablename__ = "ai_config"

    id = Column(Integer, primary_key=True)
    base_url = Column(String(300), default="")
    api_key = Column(String(300), default="")
    model = Column(String(120), default="")
    system_prompt = Column(Text, default="")
    tools_enabled = Column(Boolean, default=True)
    updated_by = Column(Integer)  # id админа, который менял последним
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class AppSettings(Base):
    """Per-user settings. One row per user.

    Лимит рассылок и порог уведомления пользователь не редактирует — их ставит
    админ (иначе смысл лимита теряется). ИИ-провайдер задаётся окружением и в
    пользовательские настройки не входит.
    """

    __tablename__ = "app_settings"

    id = Column(Integer, primary_key=True)
    owner_id = Column(Integer, ForeignKey("users.id"), unique=True, index=True, nullable=False)

    project_name = Column(String(120), default="Talaria")

    # Месячный лимит рассылок. Меняет только админ через админку; уведомление о
    # приближении к порогу показывается пользователю.
    monthly_limit = Column(Integer, default=10_000)
    monthly_used = Column(Integer, default=0)
    monthly_period = Column(String(7), default="")  # YYYY-MM, resets the counter
    low_limit_threshold = Column(Integer, default=500)

    # Присылать ли в Telegram сообщение, когда лид ответил. Включено по
    # умолчанию: ответ — главное событие сервиса, и ждать его с открытым сайтом
    # человек не обязан. Выключается в настройках, если поток ответов вырос и
    # сообщения начали мешать.
    reply_notify = Column(Boolean, nullable=False, default=True, server_default=text("true"))

    # Куда именно слать эти уведомления. NULL — аккаунт, которым вошли. Нужен
    # выбор, потому что у человека может быть несколько подключённых аккаунтов,
    # а читать он хочет в одном.
    notify_target_tg_id = Column(BigInteger)
    # Доходит ли туда сообщение бота. NULL — не проверяли. Бот не может писать
    # первым: если человек не нажал Start, Telegram вернёт 403 и уведомления
    # будут молча теряться — это состояние обязано быть видимым.
    notify_target_ok = Column(Boolean)

    # Какое напоминание об окончании тарифа уже ушло: 0 — ни одного, 3 — «через
    # три дня», 1 — «завтра». И к какой дате окончания оно относилось: продлил
    # тариф — дата изменилась, этап автоматически перестал совпадать и следующее
    # окончание снова будет отработано.
    expiry_notice_stage = Column(Integer, nullable=False, default=0, server_default="0")
    expiry_notice_for = Column(DateTime(timezone=True))

    # Окно отправки по местному времени получателя. Включено по умолчанию:
    # безопасное поведение должно быть тем, которое человек получает, ничего
    # не настраивая. Час местного времени выводится из страны канала и потому
    # приблизителен — этого хватает, чтобы отличить полдень от полуночи.
    send_window_enabled = Column(Boolean, nullable=False, default=True, server_default=text("true"))
    send_window_start = Column(Integer, nullable=False, default=10, server_default="10")
    send_window_end = Column(Integer, nullable=False, default=20, server_default="20")

    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class CampaignSend(Base):
    """Кому уже отправляли — основа дедупликации рассылки.

    Почему отдельная таблица, а не Lead.status == "contacted". Статус у лида
    один и его можно руками переключить обратно в "new" — и история того, что
    человеку уже писали, исчезнет. Повторное письмо человеку, который уже
    получал предложение, — быстрый способ получить жалобу и бан аккаунта,
    поэтому факт отправки хранится отдельно и не затирается.

    owner_id дублирует campaign.owner_id намеренно. Главный вопрос к таблице —
    «писали ли этому лиду хоть когда-нибудь, в любой из моих кампаний», и без
    этой колонки на каждый такой вопрос пришлось бы делать JOIN на campaigns.
    """

    __tablename__ = "campaign_sends"
    __table_args__ = (
        # Защита от двойной отправки на уровне БД, а не только проверкой в коде:
        # проверка в коде не спасает от двух одновременных запросов.
        UniqueConstraint("campaign_id", "lead_id", name="uq_campaign_send_lead"),
        # Основной запрос дедупликации: все лиды владельца, которым уже писали.
        Index("ix_campaign_sends_owner_lead", "owner_id", "lead_id"),
        Index(
            "ix_campaign_sends_owner_account_status_sent_at",
            "owner_id",
            "account_id",
            "status",
            "sent_at",
        ),
    )

    id = Column(Integer, primary_key=True)
    owner_id = Column(Integer, ForeignKey("users.id"), index=True, nullable=False)
    campaign_id = Column(
        Integer,
        ForeignKey("campaigns.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    # Удалили базу лидов — история отправок по ней тоже теряет смысл,
    # поэтому CASCADE, а не висячие строки с мёртвым lead_id.
    lead_id = Column(
        Integer,
        ForeignKey("leads.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    # sent | failed | skipped
    status = Column(String(20), default="sent", nullable=False)
    error = Column(Text, default="")
    sent_at = Column(DateTime(timezone=True), server_default=func.now())

    # Какой именно оффер ушёл этому лиду. Без этого нельзя ответить на главный
    # вопрос «Главной»: какой оффер даёт ответы, а какой нет. SET NULL, а не
    # CASCADE: удалённый оффер не должен стирать историю отправок и дедупликацию.
    offer_id = Column(Integer, ForeignKey("offers.id", ondelete="SET NULL"), index=True)
    # regular | ai — дублирует campaign.offer_mode намеренно: сравнение
    # эффективности типов — запрос именно по отправкам, и JOIN на campaigns ради
    # одного слова на каждой агрегации не окупается.
    offer_kind = Column(String(20), default="regular", nullable=False, server_default="regular")
    # Фактический текст, который увидел лид. У AI-оффера он у каждого свой и
    # нигде больше не хранится: без этого поля нельзя ни показать переписку, ни
    # разобраться, почему на одни письма отвечают, а на другие нет.
    rendered_text = Column(Text, default="")
    # ID исходящего сообщения Telegram позволяет сопоставить raw read-update
    # с конкретной отправкой. Без него «прочитано» было бы догадкой.
    telegram_message_id = Column(Integer, index=True)
    # sent_at — запись попытки; delivered_at — Telegram принял сообщение.
    # read_at приходит только из UpdateReadHistoryOutbox.
    delivered_at = Column(DateTime(timezone=True))
    read_at = Column(DateTime(timezone=True))
    # Заинтересованность ставит пользователь статусом лида — не ИИ-эвристика.
    interested_at = Column(DateTime(timezone=True))
    # Когда лид ответил. NULL = ответа пока нет.
    replied_at = Column(DateTime(timezone=True))
    # С какого аккаунта отправляли — для маршрутизации ответов и аналитики.
    account_id = Column(Integer, ForeignKey("warmup_accounts.id", ondelete="SET NULL"), index=True)


class TestReplyWatch(Base):
    """Короткое серверное ожидание ответа на явную тестовую отправку.

    Браузерный таймер показывает пользователю оставшееся время, но не является
    источником доверия: вкладку можно закрыть, а webhook всё равно придёт. Эта
    строка связывает входящее сообщение с конкретными владельцем, отправителем
    и получателем и поглощается первым подходящим ответом.
    """

    __tablename__ = "test_reply_watches"
    __table_args__ = (
        Index(
            "ix_test_reply_watches_owner_account_tg",
            "owner_id",
            "account_id",
            "recipient_tg_id",
        ),
        Index(
            "ix_test_reply_watches_owner_account_username",
            "owner_id",
            "account_id",
            "recipient_username",
        ),
    )

    id = Column(Integer, primary_key=True)
    owner_id = Column(
        Integer,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    account_id = Column(
        Integer,
        ForeignKey("warmup_accounts.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    recipient_tg_id = Column(String(64), default="", nullable=False, server_default="")
    recipient_username = Column(String(64), default="", nullable=False, server_default="")
    expires_at = Column(DateTime(timezone=True), nullable=False, index=True)
    consumed_at = Column(DateTime(timezone=True))
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class ChatDialogPref(Base):
    """Локальное зеркало «без звука» для диалогов переписки.

    Сам mute живёт в Telegram (шлюз ставит его через MTProto), но Talaria шлёт
    и собственное уведомление «лид ответил» — в личку бота. Спрашивать Telegram
    «замьючен ли диалог» на каждый ответ лида нельзя: это лишний MTProto-вызов
    в горячем пути. Поэтому факт mute дублируется сюда, и рассылка проверяет
    его одним запросом к своей базе.

    Храним только замьюченные: снятие mute удаляет строку, и таблица не растёт
    от каждого открытого диалога.
    """

    __tablename__ = "chat_dialog_prefs"
    __table_args__ = (
        # Один диалог — одна строка; поиск при ответе лида идёт ровно по этой
        # тройке, поэтому уникальный индекс закрывает и целостность, и скорость.
        Index(
            "ix_chat_dialog_prefs_owner_account_peer",
            "owner_id",
            "account_id",
            "peer_id",
            unique=True,
        ),
    )

    id = Column(Integer, primary_key=True)
    owner_id = Column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    # id слота WarmupAccount: mute в Telegram — свойство конкретного аккаунта,
    # с другого аккаунта тот же собеседник может быть и не замьючен.
    account_id = Column(Integer, nullable=False)
    # Идентификатор собеседника как его отдаёт шлюз (у людей — положительный
    # telegram id, у групп и каналов — со знаком минус).
    peer_id = Column(String(32), nullable=False)
    muted = Column(Boolean, nullable=False, default=True, server_default=text("true"))
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class SupportMessage(Base):
    """Переписка клиента с владельцем сервиса («поддержка»).

    Отдельная таблица, а не журнал событий: у обращения два участника и два
    признака прочтения, и его надо показывать диалогом в обе стороны. В
    event_logs это была бы лента без адресата, из которой нельзя понять, ответили
    человеку или нет.

    Владелец сервиса читает обращения в админке, а уведомление о новом получает
    в Telegram тем же ботом, что и вход, — отдельного канала для этого нет.
    """

    __tablename__ = "support_messages"
    __table_args__ = (
        # Диалог всегда читается как «сообщения одного клиента, новые снизу»,
        # а список обращений в админке — как «у кого есть непрочитанное».
        Index("ix_support_messages_owner_created", "owner_id", "created_at"),
    )

    id = Column(Integer, primary_key=True)
    owner_id = Column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    # Кто написал: сам клиент («user») или администратор («admin»). Строкой, а не
    # ссылкой на автора: админ может смениться, а диалог должен остаться читаемым.
    author = Column(String(10), nullable=False, default="user")
    # id администратора, который ответил. Нужен для разбора «кто это писал», если
    # админов станет несколько; у сообщений клиента пусто.
    admin_id = Column(Integer)
    text = Column(Text, nullable=False)
    # Момент прочтения второй стороной. NULL = ещё не прочитано, поэтому в
    # админке видно, на что не ответили, а у клиента мигает точка на ответе.
    read_at = Column(DateTime(timezone=True))
    created_at = Column(DateTime(timezone=True), server_default=func.now(), index=True)
