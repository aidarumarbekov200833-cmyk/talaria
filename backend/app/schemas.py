"""Pydantic request/response schemas."""

import json
from datetime import date as date_type
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# ---------- system ----------


class HealthOut(BaseModel):
    status: str


class ConfigOut(BaseModel):
    demo_mode: bool
    bot_username: str | None
    bot_id: int | None = None


# ---------- auth ----------


class UserOut(ORMModel):
    name: str
    username: str | None
    photo_url: str | None
    is_admin: bool = False
    # Кабинет открыт администратором в режиме просмотра. Интерфейс обязан
    # сказать об этом прямо: иначе человек за тем же экраном не поймёт, почему
    # ничего не сохраняется.
    view_only: bool = False

    @classmethod
    def from_user(cls, user, is_admin: bool = False, view_only: bool = False) -> "UserOut":
        return cls(
            name=user.display_name,
            username=user.username,
            photo_url=user.photo_url,
            is_admin=is_admin,
            view_only=view_only,
        )


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserOut


# ---------- тарифы и подписка ----------


class PlanOut(BaseModel):
    """Публичное описание тарифа для страницы «Тарифы» и лендинга."""

    slug: str
    name: str
    price_kzt: int
    price_rub: int
    price_kzt_first: int  # цена со скидкой на первую покупку
    price_rub_first: int
    tagline: str
    monthly_leads: int  # -1 = без ограничений
    monthly_messages: int = 0
    monthly_ai_offers: int = 0
    max_per_launch: int = 0
    max_databases: int
    max_offers: int
    max_warmup: int
    ai: bool
    # Доступен ли ИИ-оффер. Поле было в public_plan(), но не в схеме, поэтому
    # до фронта не доезжало — карточки тарифов не могли его показать.
    ai_offer: bool = False
    # Разрешает ли тариф отправку сообщений в Telegram.
    sending: bool = False
    features: list[str] = []


class PlansOut(BaseModel):
    plans: list[PlanOut] = []
    # Цены назначены отдельно в каждой валюте, а не пересчитаны по курсу.
    currencies: list[str] = ["KZT", "RUB"]
    admin_contact: str = ""  # @username, которому писать для оплаты
    first_purchase_discount_pct: int = 0  # процент скидки на первую покупку (0 = выключена)


class PlanQuoteIn(BaseModel):
    period_months: Literal[1, 3] = 1
    currency: Literal["RUB", "KZT"] = "RUB"
    promo_code: str = Field(default="", max_length=40)


class PlanQuoteOut(BaseModel):
    plan_slug: str = "zephyr"
    plan_name: str = "Talaria"
    period_months: Literal[1, 3]
    currency: Literal["RUB", "KZT"] = "RUB"
    base_total: int
    bundle_discount: int = 0
    promo_discount: int = 0
    total_amount: int
    # Старые RUB-поля сохраняем для совместимости с уже открытыми вкладками.
    base_total_rub: int
    bundle_discount_rub: int = 0
    promo_discount_rub: int = 0
    total_rub: int
    promo_code: str = ""
    promo_valid: bool = False
    promo_message: str = ""
    admin_contact: str = ""


class UsageOut(BaseModel):
    """Сколько уже использовано против лимита плана (-1 = безлимит)."""

    leads_used: int = 0
    leads_limit: int = 0
    databases: int = 0
    databases_limit: int = 0
    offers: int = 0
    offers_limit: int = 0
    campaigns: int = 0
    campaigns_limit: int = 0
    warmup: int = 0
    warmup_limit: int = 0
    messages_used: int = 0
    messages_limit: int = 0
    ai_offers_used: int = 0
    ai_offers_limit: int = 0
    max_per_launch: int = 0


class SubscriptionOut(BaseModel):
    """Состояние подписки для раздела «Тарифы».

    Оплата вне системы: если срок вышел, `plan` показывает фактический
    («none» — тарифа нет), а `stored_plan`/`paid_until` — что именно было
    активировано, чтобы человек понимал, что нужно продлить.
    """

    plan: str  # фактический план с учётом срока
    plan_name: str
    stored_plan: str  # что записано в подписке (мог истечь)
    status: str
    blocked: bool = False
    paid_until: datetime | None = None
    expired: bool = False
    ai: bool = False
    ai_offer: bool = False  # доступен ли ИИ-оффер на этом тарифе
    # Пробный доступ. Считает сервер, а не браузер: иначе часовой пояс вкладки
    # начнёт спорить с сервером и в плашке появятся разные числа.
    is_trial: bool = False
    trial_expired: bool = False  # триал был и закончился — показать «продлите оплатой»
    trial_days_left: int = 0
    sending: bool = False  # разрешает ли тариф отправку сообщений
    note: str = ""
    admin_contact: str = ""
    usage: UsageOut
    plans: list[PlanOut] = []
    first_purchase: bool = True  # ещё ни разу не покупал — скидка действует
    first_purchase_discount_pct: int = 0  # процент скидки (0 = акция выключена)


# ---------- offers ----------


class OfferIn(BaseModel):
    name: str
    text: str = ""
    image: str = ""
    # Альбом до 10 картинок. Telegram больше в одно сообщение не принимает.
    images: list[str] = Field(default_factory=list, max_length=10)


class OfferOut(ORMModel):
    id: int
    name: str
    text: str | None
    image: str | None
    images: list[str] = []

    @field_validator("images", mode="before")
    @classmethod
    def parse_images(cls, value: object) -> list[str]:
        """В базе альбом лежит JSON-строкой, наружу отдаём списком."""
        if isinstance(value, list):
            return [str(item) for item in value if item]
        if isinstance(value, str) and value.strip():
            try:
                parsed = json.loads(value)
            except ValueError:
                return []
            if isinstance(parsed, list):
                return [str(item) for item in parsed if item]
        return []
    # "user" или "ai": в списке офферов написанные помощником помечаются, чтобы
    # человек не отправил сотне людей текст, которого он не читал.
    created_by: str = "user"
    created_at: datetime | None


class OfferListOut(BaseModel):
    offers: list[OfferOut]


# ---------- campaigns ----------


class CampaignIn(BaseModel):
    name: str
    database_name: str
    channel: str = "Telegram"
    offer_ids: list[int] = []
    total_recipients: int = 0


class CampaignLaunchIn(BaseModel):
    """Запустить рассылку сразу, без промежуточного создания.

    Один запрос вместо трёх (создать → подобрать получателей → отметить отправки):
    кнопка в интерфейсе одна — «Запустить», и половинчатых состояний быть не должно.

    offer_mode:
      regular — тексты из offer_ids по кругу, плейсхолдеры подставляются,
                спинтакс по желанию;
      ai      — ИИ пишет индивидуальный текст каждому лиду по ai_template.
    """

    database_id: int
    name: str = ""
    offer_mode: Literal["regular", "ai"] = "regular"
    offer_ids: list[int] = []
    ai_template: str = ""
    spintax: bool = False
    account_ids: list[int] = []
    min_score: int | None = None
    skip_contacted: bool = True
    # Сколько лидов взять в этот запуск. Потолок нужен и в фоне: большая
    # партия с одного запуска — прямой путь к бану аккаунтов за спам.
    limit: int = 50
    # true — ответить после отправки всей партии (API-автоматизация, тесты).
    # По умолчанию ответ приходит сразу, отправка идёт в фоне, прогресс — в
    # журнале кампании и по WebSocket.
    wait: bool = False


class CampaignUpdate(BaseModel):
    status: str | None = None
    progress: float | None = None
    sent_count: int | None = None
    error_count: int | None = None


class CampaignOut(ORMModel):
    id: int
    name: str
    database_name: str
    channel: str | None
    status: str | None
    progress: float | None
    total_recipients: int | None
    sent_count: int | None
    error_count: int | None
    offer_ids: list[int] = []
    offer_mode: str = "regular"
    ai_template: str | None = ""
    spintax: bool = False
    reply_count: int = 0
    created_at: datetime | None
    started_at: datetime | None
    completed_at: datetime | None

    @field_validator("offer_ids", mode="before")
    @classmethod
    def _decode_offer_ids(cls, value: object) -> object:
        """offer_ids is stored as a JSON string column; expose it as a list."""
        if isinstance(value, str):
            try:
                decoded = json.loads(value or "[]")
            except json.JSONDecodeError:
                return []
            return decoded if isinstance(decoded, list) else []
        return value or []


class CampaignListOut(BaseModel):
    campaigns: list[CampaignOut]
    # Поля пагинации добавлены с дефолтами намеренно: старый клиент читает
    # только campaigns и продолжает работать, а новый видит, сколько всего
    # записей и нужно ли грузить ещё.
    total: int = 0
    offset: int = 0
    limit: int = 0


class CampaignStopAllOut(BaseModel):
    stopped: int = 0
    campaign_ids: list[int] = Field(default_factory=list)


class CampaignLogIn(BaseModel):
    message: str
    level: str = "info"


class CampaignLogOut(ORMModel):
    id: int
    campaign_id: int
    message: str
    level: str | None
    timestamp: datetime | None


class CampaignLogListOut(BaseModel):
    logs: list[CampaignLogOut]
    total: int = 0
    offset: int = 0
    limit: int = 0


# ---------- тестовая отправка рассылки ----------


class TestSendIn(BaseModel):
    """Отправить один оффер на один Telegram-username для проверки текста.

    Сообщение уходит через Telegram-gateway с аккаунта пользователя.
    """

    username: str  # получатель, с @ или без
    offer_id: int | None = None  # какой оффер отправить
    text: str | None = None  # либо произвольный текст вместо оффера


class TestSendOut(BaseModel):
    ok: bool = True
    sent_to: str = ""
    rendered_text: str = ""
    account_label: str = ""
    sent_at: datetime | None = None
    note: str = ""
    reply_watch_seconds: int = 300


class CampaignPreviewSampleOut(BaseModel):
    """Один пример: кому и какой текст уйдёт."""

    lead_id: int
    title: str = ""
    username: str = ""
    score: int | None = None
    offer_name: str = ""
    text: str = ""


class CampaignPreviewOut(BaseModel):
    """Предпросмотр запуска: те же фильтры, что у /launch, но без отправки.

    Смысл — показать человеку 2–3 готовых текста с данными настоящих лидов
    до того, как он нажмёт «Запустить»: битые плейсхолдеры и кривой спинтакс
    видно глазами, а не по жалобам лидов.
    """

    mode: str = "regular"
    recipients: int = 0
    skipped_no_telegram: int = 0
    skipped_contacted: int = 0
    # Сколько получателей отложено окном отправки: у них сейчас ночь.
    # Отдельным числом, а не внутри «пропущено»: это не отказ, а «позже».
    skipped_window: int = 0
    samples: list[CampaignPreviewSampleOut] = []
    note: str = ""


class CampaignLaunchOut(BaseModel):
    """Итог запуска: что ушло, что нет и почему.

    Ошибки отдаются списком строк, а не числом: «не отправлено 12» ничего не
    говорит, а «у 40 лидов нет Telegram» — говорит, что делать дальше.
    """

    campaign_id: int
    campaign: CampaignOut | None = None
    # true — отправка ушла в фон: sent/failed ещё нулевые, смотреть журнал.
    background: bool = False
    sent: int = 0
    failed: int = 0
    skipped_no_telegram: int = 0
    skipped_contacted: int = 0
    skipped_window: int = 0
    total_candidates: int = 0
    accounts_used: list[str] = []
    problems: list[str] = []
    note: str = ""


class CampaignReplyIn(BaseModel):
    """Вебхук от tg-mini-client: лид ответил в личку.

    Авторизация здесь не через токен пользователя, а по X-Send-Secret: вызывает
    сервис, а не браузер — токена сессии у него нет.
    """

    owner_id: int
    username: str = ""
    tg_id: str = ""
    sender_name: str = ""
    text: str = ""
    account_id: int | None = None


class CampaignReplyOut(BaseModel):
    ok: bool = True
    matched: bool = False
    lead_id: int | None = None
    campaign_id: int | None = None
    note: str = ""


class CampaignReadIn(BaseModel):
    """Raw Telegram read-update from the internal gateway."""

    owner_id: int
    account_id: int
    peer_tg_id: str = ""
    max_message_id: int


class CampaignReadOut(BaseModel):
    ok: bool = True
    marked: int = 0


class OfferStatRow(BaseModel):
    """Один оффер в статистике: сколько отправлено и сколько ответило."""

    offer_id: int | None = None
    name: str = ""
    kind: str = "regular"  # regular | ai
    sent: int = 0
    delivered: int = 0
    read: int = 0
    replied: int = 0
    interested: int = 0
    failed: int = 0
    delivery_rate: float = 0.0
    read_rate: float = 0.0
    reply_rate: float = 0.0  # процент, 0–100
    interest_rate: float = 0.0


class OfferKindStat(BaseModel):
    kind: str = "regular"
    label: str = ""
    sent: int = 0
    delivered: int = 0
    read: int = 0
    replied: int = 0
    interested: int = 0
    failed: int = 0
    delivery_rate: float = 0.0
    read_rate: float = 0.0
    reply_rate: float = 0.0
    interest_rate: float = 0.0


class ReplyHeatmapCell(BaseModel):
    weekday: int = Field(ge=0, le=6)  # 0 = Monday
    hour: int = Field(ge=0, le=23)
    replies: int = 0


class BestSendTime(BaseModel):
    weekday: int = Field(ge=0, le=6)
    hour: int = Field(ge=0, le=23)
    replies: int = 0


class OfferStatsOut(BaseModel):
    """Сравнение эффективности: отдельно по офферам и по типам офферов."""

    offers: list[OfferStatRow] = []
    by_kind: list[OfferKindStat] = []
    total_sent: int = 0
    total_delivered: int = 0
    total_read: int = 0
    total_replied: int = 0
    total_interested: int = 0
    delivery_rate: float = 0.0
    read_rate: float = 0.0
    reply_rate: float = 0.0
    interest_rate: float = 0.0
    reply_heatmap: list[ReplyHeatmapCell] = []
    best_send_times: list[BestSendTime] = []


class OkOut(BaseModel):
    ok: bool = True


# ---------- настройки ----------


class SettingsOut(BaseModel):
    """Что пользователь видит в настройках.

    Лимит и порог — только для чтения: их ставит админ, иначе смысл лимита
    теряется. Уведомление о приближении к порогу считает сервер
    (`low_limit_reached`), поэтому лимит остаётся здесь, но без права правки.
    Про ИИ отдаём только «доступен/подключён» — ни провайдера, ни модели, ни
    ключа: это настройка владельца сервиса, а не пользователя.
    """

    project_name: str
    monthly_limit: int
    monthly_used: int
    monthly_left: int
    low_limit_threshold: int
    low_limit_reached: bool
    ai_available: bool  # входит ли ИИ в тариф пользователя
    ai_ready: bool  # подключён ли провайдер владельцем
    reply_notify: bool  # присылать ли в Telegram сообщение, когда лид ответил
    notify_target_tg_id: int | None  # куда слать; None — аккаунт входа
    notify_target_ok: bool | None  # доходит ли туда; None — не проверяли
    # Окно отправки по местному времени получателя.
    send_window_enabled: bool = True
    send_window_start: int = 10
    send_window_end: int = 20


class SettingsPatchIn(BaseModel):
    """Что пользователю разрешено менять в настройках.

    Только уведомления. Лимит и порог ставит админ, ИИ-провайдер — владелец
    сервиса; если открыть их на запись здесь, смысл ограничений исчезнет.
    Поля необязательные: интерфейс меняет их по одному, и присылать целиком
    весь объект ради одного переключателя незачем.
    """

    reply_notify: bool | None = None
    notify_target_tg_id: int | None = None
    send_window_enabled: bool | None = None
    # Границы окна принимаем только парой: проверять «начало меньше конца»
    # по одному полю нельзя — второе в этот момент ещё старое.
    send_window_start: int | None = Field(default=None, ge=0, le=23)
    send_window_end: int | None = Field(default=None, ge=0, le=23)


class ReplyHourOut(BaseModel):
    """Один час суток в показателе «когда чаще отвечают».

    hour — местное время получателя, а не наше: вопрос, на который отвечает
    показатель, звучит «когда написать человеку», а не «когда мне нажать
    кнопку».
    """

    hour: int
    sent: int
    replied: int
    rate: float


class ReplyHoursOut(BaseModel):
    hours: list[ReplyHourOut] = []
    total_sent: int = 0
    total_replied: int = 0
    # Мало данных — показывать «лучший час» по трём письмам значит выдать шум
    # за закономерность. Интерфейс в этом случае рисует подсказку, а не вывод.
    enough_data: bool = False
    best_hours: list[int] = []
    note: str = ""


class NotifyTargetOut(BaseModel):
    """Аккаунт, который можно назначить главным для уведомлений."""

    tg_id: int
    label: str
    kind: str  # login | sender
    selected: bool


class NotifyTargetTestOut(BaseModel):
    """Результат проверки: дошло сообщение или нет и что делать, если нет."""

    delivered: bool
    detail: str


# ---------- ключи YouTube и квота ----------


class ApiKeyIn(BaseModel):
    key: str
    label: str = ""
    project_label: str = ""
    daily_quota: int = 10_000


class ApiKeyUpdate(BaseModel):
    label: str | None = None
    project_label: str | None = None
    daily_quota: int | None = None
    enabled: bool | None = None


class ApiKeyOut(ORMModel):
    """Ключ наружу отдаётся только в замаскированном виде."""

    id: int
    label: str | None
    key_masked: str
    project_label: str | None
    daily_quota: int | None
    enabled: bool | None
    status: str | None
    status_detail: str | None
    last_checked_at: datetime | None
    used_today: int = 0
    left_today: int = 0


class QuotaOut(BaseModel):
    """Сводка по квоте.

    `measured_locally=True` всегда: YouTube не отдаёт остаток квоты ни одним
    методом, поэтому расход считаем сами по документированной цене вызовов.
    Точное число видно только в Google Cloud Console — так и написано в UI.
    """

    total_daily: int
    used_today: int
    left_today: int
    keys_total: int
    keys_enabled: int
    projects: int
    resets_at: datetime  # ближайшая полночь по тихоокеанскому времени
    measured_locally: bool = True
    source_note: str


class ApiKeyListOut(BaseModel):
    keys: list[ApiKeyOut]
    quota: QuotaOut


# ---------- темы и ниши ----------


class NicheOut(BaseModel):
    slug: str
    title: str
    icon: str


class NicheListOut(BaseModel):
    niches: list[NicheOut]


class TopicItem(BaseModel):
    """Тема с указанием происхождения — чтобы не выдавать догадку за факт."""

    topic: str
    source: str  # youtube_trends | niche
    evidence: str = ""  # заголовок реального видео или название ниши
    channel: str = ""  # канал видео, если тема из трендов
    views: int | None = None


class TopicRunIn(BaseModel):
    mode: str = "trends"  # trends | niches
    count: int = 10
    region: str = "KZ"
    # Мультирегион: владелец просил выбирать несколько регионов трендов сразу.
    # Каждый регион — отдельный вызов videos.list, то есть отдельная единица
    # квоты, поэтому список ограничен восемью.
    regions: list[str] = Field(default_factory=list, max_length=8)
    niches: list[str] = []

    @field_validator("regions")
    @classmethod
    def validate_regions(cls, values: list[str]) -> list[str]:
        cleaned: list[str] = []
        for value in values:
            code = str(value or "").strip().upper()
            if not code:
                continue
            if not code.isalpha() or len(code) > 8:
                raise ValueError("код региона — только буквы, до 8 символов")
            if code not in cleaned:
                cleaned.append(code)
        return cleaned

    @property
    def region_codes(self) -> list[str]:
        """Регионы запроса: список, а если он пуст — одиночное поле region."""
        if self.regions:
            return self.regions
        single = (self.region or "").strip().upper()
        return [single or "KZ"]


class TopicRunOut(BaseModel):
    id: int
    mode: str
    region: str
    niches: list[str]
    topics: list[TopicItem]
    requested: int
    units_spent: int
    created_at: datetime | None


class KeywordsIn(BaseModel):
    """Из выбранных тем/ниш собираем ключевые слова для поиска каналов."""

    topics: list[str] = []
    niches: list[str] = []
    count: int = 20


class KeywordsOut(BaseModel):
    keywords: list[str]
    source_note: str


# ---------- поиск каналов ----------


class SearchIn(BaseModel):
    keywords: list[str] = Field(min_length=1, max_length=50)
    limit: int = Field(default=50, ge=1, le=1000)  # на ключевое слово
    region: str = Field(default="", max_length=8, pattern=r"^[A-Za-z]{0,8}$")
    database_name: str = Field(default="", max_length=200)
    mode: Literal["trends", "niches", "custom"] = "custom"
    min_subscribers: int = Field(default=0, ge=0, le=2_000_000_000)
    max_subscribers: int | None = Field(default=None, ge=0, le=2_000_000_000)
    min_views: int = Field(default=0, ge=0)
    min_average_views: int = Field(default=0, ge=0)
    min_view_subscriber_ratio: float = Field(default=0, ge=0, le=100)
    min_videos: int = Field(default=0, ge=0, le=10_000_000)
    max_channel_age_days: int | None = Field(default=None, ge=1, le=50_000)
    country: str = Field(default="", max_length=8, pattern=r"^[A-Za-z]{0,8}$")
    # Мультивыбор стран: владелец просил список с галочками вместо одного поля.
    # Старое поле country оставлено — им пользуются сохранённые наборы фильтров.
    countries: list[str] = Field(default_factory=list, max_length=40)
    include_hidden_subscribers: bool = True
    require_telegram_link: bool = False
    # Синхронный режим для API-тестов: ответ придёт после всего поиска.
    # Интерфейс всегда шлёт wait=false и следит за прогрессом по job.
    wait: bool = False

    @field_validator("countries")
    @classmethod
    def validate_countries(cls, values: list[str]) -> list[str]:
        cleaned: list[str] = []
        for value in values:
            code = str(value or "").strip().upper()
            if not code:
                continue
            if not code.isalpha() or len(code) > 8:
                raise ValueError("код страны — только буквы, до 8 символов")
            if code not in cleaned:
                cleaned.append(code)
        return cleaned

    @property
    def country_codes(self) -> set[str]:
        """Все страны фильтра одним множеством: список плюс одиночное поле."""
        codes = set(self.countries)
        if self.country:
            codes.add(self.country.upper())
        return codes

    @field_validator("keywords")
    @classmethod
    def validate_keywords(cls, values: list[str]) -> list[str]:
        cleaned = list(dict.fromkeys(value.strip() for value in values if value.strip()))
        if not cleaned:
            raise ValueError("нужно хотя бы одно ключевое слово")
        if any(len(value) > 200 for value in cleaned):
            raise ValueError("ключевое слово длиннее 200 символов")
        return cleaned

    @model_validator(mode="after")
    def validate_ranges(self) -> "SearchIn":
        if self.max_subscribers is not None and self.max_subscribers < self.min_subscribers:
            raise ValueError("максимум подписчиков меньше минимума")
        return self

    @model_validator(mode="after")
    def validate_subscriber_range(self) -> "SearchIn":
        if self.max_subscribers is not None and self.max_subscribers < self.min_subscribers:
            raise ValueError("максимум подписчиков меньше минимума")
        return self


class SearchEstimateOut(BaseModel):
    """Оценка стоимости до запуска — цена вызовов документирована Google."""

    keywords: int
    limit: int  # на одно ключевое слово
    total_channels: int = 0  # ключевые слова × limit
    pages: int
    units_estimate: int
    left_today: int
    enough: bool
    note: str


# ---------- лиды ----------


class LeadOut(ORMModel):
    id: int
    channel_id: str
    title: str | None
    description: str | None
    custom_url: str | None
    country: str | None
    thumbnail: str | None
    subscribers: int | None
    video_count: int | None
    view_count: int | None
    published_at: datetime | None
    matched_keyword: str | None
    tg_id: str | None = ""
    tg_username: str | None = ""
    tg_first_name: str | None = ""
    tg_last_name: str | None = ""
    tg_status: str = "unknown"
    tg_kind: str | None = ""
    tg_error: str | None = ""
    tg_verified_by_account_id: int | None = None
    tg_checked_at: datetime | None = None
    telegram_channel: str | None = ""
    telegram_url: str | None = ""
    contact_email: str | None = ""
    status: str | None
    note: str = ""
    # Оценка 0–100. Значение по умолчанию нужно для старых строк, которые
    # ещё не пересчитаны: без него ответ упал бы на валидации.
    score: int = 0
    score_reasons: list[str] = []
    created_at: datetime | None


class LeadNoteIn(BaseModel):
    """Заметка владельца по лиду. Пустая строка — стереть заметку."""

    # 2000 символов — с запасом под расшифровку разговора и всё равно предел,
    # за которым поле перестаёт быть заметкой и становится документом.
    note: str = Field(default="", max_length=2000)


class LeadListOut(BaseModel):
    leads: list[LeadOut]
    total: int
    offset: int
    limit: int


# --- Получатели рассылки и дедупликация -------------------------------


class RecipientsIn(BaseModel):
    database_id: int
    # По умолчанию ИСКЛЮЧАЕМ тех, кому уже писали. Безопасное поведение
    # должно быть умолчанием: цена ошибки в другую сторону — жалоба на спам
    # и бан Telegram-аккаунта.
    skip_contacted: bool = True
    # Необязательный порог по оценке: писать только тем, кто набрал от N.
    min_score: int | None = None
    offset: int = 0
    limit: int = 100


class RecipientsOut(BaseModel):
    leads: list[LeadOut]
    # Сколько всего получателей осталось после всех фильтров.
    total: int
    # Сколько отсеяно именно дедупликацией. Показывается человеку, чтобы
    # уменьшившийся список не выглядел потерей данных.
    skipped_contacted: int
    offset: int
    limit: int


class CampaignSendIn(BaseModel):
    lead_ids: list[int]
    status: str = "sent"  # sent | failed | skipped | uncertain
    error: str = ""


class CampaignSendOut(ORMModel):
    id: int
    lead_id: int
    status: str
    error: str | None
    sent_at: datetime | None


class CampaignSendResultOut(BaseModel):
    recorded: int
    # Уже были отмечены раньше — не ошибка, повторный вызов безопасен.
    duplicates: int
    # Лиды, которых нет или которые принадлежат чужому аккаунту.
    unknown_leads: int


class CampaignSendListOut(BaseModel):
    sends: list[CampaignSendOut]
    total: int
    offset: int
    limit: int


# --- Импорт лидов -----------------------------------------------------


class LeadImportResultOut(BaseModel):
    database_id: int
    database_name: str
    created: int
    # Строки с channel_id, который уже есть в этой базе — не ошибка,
    # повторная заливка того же файла безопасна.
    duplicates: int
    # Строки без channel_id или с нечитаемыми числами.
    skipped: int
    # Первые несколько проблем с номерами строк — чтобы человек мог починить
    # файл, а не гадать, почему часть лидов не загрузилась.
    errors: list[str] = []


# --- Массовые действия над лидами ---------------------------------


class LeadTelegramIn(BaseModel):
    """Ручное уточнение Telegram-контакта лида."""

    username: str = ""  # пусто — пробуем уже сохранённый или handle с YouTube


class LeadTelegramResolveIn(BaseModel):
    """Массовое определение имён по базе."""

    database_id: int
    limit: int = 30  # сколько лидов за один прогон
    only_missing: bool = True  # не трогать тех, у кого имя уже есть


class LeadTelegramOut(BaseModel):
    lead_id: int
    title: str = ""
    ok: bool = False
    username: str = ""
    tg_id: str = ""
    kind: str = ""
    status: str = ""
    account_id: int | None = None
    first_name: str = ""
    last_name: str = ""
    error: str = ""


class LeadTelegramResolveOut(BaseModel):
    resolved: int = 0
    failed: int = 0
    checked: int = 0
    results: list[LeadTelegramOut] = []


class LeadVerificationStartIn(BaseModel):
    """Запуск полного прогона без ручного выбора размера пачки."""

    retry_errors: bool = False
    # Синхронный режим оставлен для тестов и API-автоматизации на маленьких
    # базах. Интерфейс всегда использует фон и сразу получает job.
    wait: bool = False


class LeadVerificationJobOut(BaseModel):
    id: int
    database_id: int
    status: str = "queued"
    retry_errors: bool = False
    total: int = 0
    processed: int = 0
    verified: int = 0
    unresolved: int = 0
    errors: int = 0
    progress: int = 0
    current_lead_id: int | None = None
    current_title: str = ""
    last_result: str = ""
    last_error: str = ""
    started_at: datetime | None = None
    completed_at: datetime | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class LeadTelegramSummaryOut(BaseModel):
    database_id: int
    total: int = 0
    candidates: int = 0
    pending: int = 0
    verified: int = 0
    channels: int = 0
    bots: int = 0
    missing: int = 0
    errors: int = 0
    progress: int = 0
    pipeline_status: str = "youtube_ready"
    accounts_connected: int = 0
    job: LeadVerificationJobOut | None = None


class LeadBulkStatusIn(BaseModel):
    lead_ids: list[int]
    status: str


class LeadBulkDeleteIn(BaseModel):
    lead_ids: list[int]


class LeadBulkResultOut(BaseModel):
    # Сколько строк действительно изменилось или удалилось.
    affected: int
    # Чужие, несуществующие или обрезанные лимитом id. Отдаём числом,
    # а не ошибкой: частичный успех — нормальный исход массовой операции.
    skipped: int


class LeadDatabaseOut(ORMModel):
    id: int
    name: str
    source: str | None
    mode: str | None
    query: str | None
    filters_json: str = "{}"
    pipeline_status: str = "youtube_ready"
    units_spent: int | None
    created_at: datetime | None
    leads_count: int = 0


class LeadDatabaseListOut(BaseModel):
    databases: list[LeadDatabaseOut]
    total_leads: int


class SearchJobOut(BaseModel):
    """Состояние прогона поиска: интерфейс опрашивает его до завершения.

    `database` заполняется только у завершённого прогона — это тот же объект,
    который раньше возвращал синхронный /api/search, чтобы фронтенду не
    пришлось ходить за базой отдельным запросом.
    """

    id: int
    status: str = "queued"
    mode: str = "custom"
    keywords_total: int = 0
    keywords_done: int = 0
    found_count: int = 0
    limit_total: int = 0
    current_keyword: str = ""
    stop_reason: str = ""
    error: str = ""
    database: LeadDatabaseOut | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    created_at: datetime | None = None


class LeadDatabaseRename(BaseModel):
    name: str


# ---------- Telegram-аккаунт владельца ----------


class TelegramAccountOut(BaseModel):
    """Аккаунт, с которого человек вошёл в сервис.

    Всё, что здесь есть, берётся из данных Telegram-логина и из нашей же
    подписки. Сервер не ходит в Telegram за статусом аккаунта и не знает,
    есть ли на нём спам-блок: `blocked` — это блокировка внутри сервиса,
    которую ставит админ, а не бан в Telegram.
    """

    telegram_id: int
    username: str = ""
    name: str = ""
    photo_url: str = ""
    joined_at: datetime | None = None
    is_admin: bool = False
    plan: str = "none"
    plan_name: str = ""
    plan_status: str = ""
    blocked: bool = False
    paid_until: datetime | None = None
    warmup_total: int = 0
    warmup_active: int = 0


# ---------- прогрев Telegram-аккаунтов ----------


class WarmupAccountIn(BaseModel):
    label: str = Field(default="", max_length=120)
    note: str = Field(default="", max_length=2000)
    plan_days: int = Field(default=14, ge=1, le=60)
    daily_send_limit: int = Field(default=30, ge=1, le=200)
    delay_min_seconds: int = Field(default=20, ge=0, le=3600)
    delay_max_seconds: int = Field(default=60, ge=0, le=3600)
    sending_enabled: bool = True
    verification_enabled: bool = True

    @model_validator(mode="after")
    def validate_delay(self) -> "WarmupAccountIn":
        if self.delay_max_seconds < self.delay_min_seconds:
            raise ValueError("максимальная задержка меньше минимальной")
        return self


class WarmupAccountUpdate(BaseModel):
    label: str | None = Field(default=None, max_length=120)
    note: str | None = Field(default=None, max_length=2000)
    status: str | None = None
    daily_send_limit: int | None = Field(default=None, ge=1, le=200)
    delay_min_seconds: int | None = Field(default=None, ge=0, le=3600)
    delay_max_seconds: int | None = Field(default=None, ge=0, le=3600)
    sending_enabled: bool | None = None
    verification_enabled: bool | None = None


class WarmupLogOut(ORMModel):
    id: int
    day: int
    date: date_type | None
    target_messages: int | None
    done_messages: int | None
    target_actions: list[str] = []
    done: bool | None
    note: str | None
    completed_at: datetime | None

    @field_validator("target_actions", mode="before")
    @classmethod
    def _decode_actions(cls, value: object) -> object:
        if isinstance(value, str):
            try:
                decoded = json.loads(value or "[]")
            except json.JSONDecodeError:
                return []
            return decoded if isinstance(decoded, list) else []
        return value or []


class WarmupDayIn(BaseModel):
    done: bool | None = None
    done_messages: int | None = None
    note: str | None = None


class WarmupToggleIn(BaseModel):
    """Один переключатель вместо формы: включён прогрев или на паузе."""

    on: bool


class WarmupIdentityIn(BaseModel):
    """Кто подключён в Telegram-gateway. Зовёт сервис с общим секретом."""

    owner_id: int
    account_id: int
    tg_id: str | None = None
    name: str | None = None
    username: str | None = None
    phone: str | None = None


class WarmupAccountOut(ORMModel):
    id: int
    label: str
    note: str | None
    started_on: date_type | None
    plan_days: int | None
    status: str | None
    daily_send_limit: int = 30
    delay_min_seconds: int = 20
    delay_max_seconds: int = 60
    sending_enabled: bool = True
    verification_enabled: bool = True
    is_primary: bool = False
    created_at: datetime | None
    current_day: int = 0
    days_done: int = 0
    today_target: int = 0
    # Готов ли аккаунт к рассылке. Важная оговорка: это вывод из того, что
    # человек сам отметил в журнале, а не проверка реальной активности в
    # Telegram — сервер в Telegram ничего не видит и видеть не должен.
    ready: bool = False
    ready_reason: str = ""
    # Сколько дней плана прошло, но осталось неотмеченными.
    overdue_days: int = 0
    # Понятный пользователю индикатор настроенности слота. Он считается только
    # по данным Talaria (подключение, план, лимиты), а не обещает отсутствие
    # ограничений со стороны Telegram.
    health_score: int = 0
    health_label: str = "Требует настройки"
    health_reasons: list[str] = Field(default_factory=list)
    # Какой Telegram привязан к карточке (заполняется автоматически).
    tg_username: str | None = None
    tg_phone: str | None = None
    connected: bool = False
    connected_at: datetime | None = None
    operational_status: str = "ready"
    cooldown_until: datetime | None = None
    cooldown_seconds: int = 0
    last_tg_error: str = ""
    last_tg_error_code: str = ""
    last_tg_error_at: datetime | None = None
    last_success_at: datetime | None = None
    risk_score: int = 0
    consecutive_errors: int = 0
    unread_count: int = 0
    unread_dialogs: int = 0
    unread_growth: int = 0
    safety_checked_at: datetime | None = None
    sent_today: int = 0
    failed_today: int = 0
    verified_today: int = 0
    daily_left: int = 0
    spambot_status: str = "unknown"
    spambot_detail: str = ""
    spambot_checked_at: datetime | None = None
    spambot_appealed_at: datetime | None = None
    logs: list[WarmupLogOut] = []


class WarmupListOut(BaseModel):
    accounts: list[WarmupAccountOut]
    disclaimer: str


class SpamBotOut(BaseModel):
    status: str
    detail: str = ""
    action: str = ""
    manual_url: str = "https://t.me/SpamBot"


# ---------- журнал событий ----------


class EventLogOut(ORMModel):
    id: int
    scope: str | None
    level: str | None
    message: str
    ref_type: str | None
    ref_id: int | None
    created_at: datetime | None


class EventLogListOut(BaseModel):
    events: list[EventLogOut]


# ---------- плейсхолдеры офферов ----------


class PublicStatsOut(BaseModel):
    """Агрегаты для страницы входа. Только числа, никаких персональных данных.

    Лендинг показывает воронку целиком (найдено → отправлено → доставлено →
    ответы), поэтому агрегатов больше, чем было. Всё это по-прежнему только
    счётчики по всей системе: ни одного названия канала, имени или текста
    наружу не уходит — иначе публичный эндпоинт стал бы утечкой чужих баз.
    """

    leads: int = 0
    databases: int = 0
    campaigns: int = 0
    messages_sent: int = 0
    users: int = 0
    # Доставлено и ответы — хвост воронки. Без них на лендинге видно только
    # «сколько отправили», а вопрос у читателя ровно один: «а отвечают ли».
    delivered: int = 0
    replies: int = 0
    # Совокупная аудитория найденных каналов. Единственная честная метрика,
    # которая растёт быстро и на старте не выглядит мёртвой: 600 лидов — это
    # уже десятки миллионов подписчиков.
    audience: int = 0
    countries: int = 0


class PlaceholderOut(BaseModel):
    """Плейсхолдер для вставки в текст оффера.

    `available` = False, если поле есть в шаблоне, но YouTube его не отдаёт
    (например имя и фамилия человека). Такие плейсхолдеры показываем
    отключёнными и объясняем причину, а не подставляем выдумку.
    """

    token: str
    title: str
    example: str
    available: bool = True
    note: str = ""
    source: str = "youtube"  # youtube | telegram — откуда берётся значение


class PlaceholderListOut(BaseModel):
    placeholders: list[PlaceholderOut]


class PreviewIn(BaseModel):
    text: str
    lead_id: int | None = None


class PreviewOut(BaseModel):
    text: str
    lead_title: str = ""
    unresolved: list[str] = []


class AiOfferIn(BaseModel):
    """Проверка ИИ-оффера на реальном лиде.

    Лид не выбирается: берётся самый свежий из баз пользователя. Тон и «что мы
    предлагаем» тоже убраны — и то и другое уже написано человеком в скелете
    оффера, а два источника правды здесь означали противоречащие инструкции
    модели. Из настроек приходит только скелет и число материалов.
    """

    template: str = ""  # скелет; пусто — берём сохранённый
    materials: int | None = None  # сколько последних материалов смотреть


class AiOfferVariantOut(BaseModel):
    label: str = ""
    angle: str = ""
    text: str = ""


class AiOfferOut(BaseModel):
    text: str
    variants: list[AiOfferVariantOut] = []
    lead_title: str = ""
    analyzed: list[str] = []  # что реально смотрели (названия видео/постов)
    note: str = ""


class AiOfferSettingsIn(BaseModel):
    """Настройки ИИ-оффера. Один скелет на пользователя."""

    template: str = ""
    materials: int = 5


class AiOfferSettingsOut(BaseModel):
    template: str = ""
    materials: int = 5
    # Доступен ли ИИ-оффер на текущем тарифе и что ответить, если нет.
    allowed: bool = False
    plan_names: str = ""
    # Настроен ли провайдер ИИ владельцем сервиса.
    provider_ready: bool = False


# ---------- ИИ ----------


class AiMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(default="", max_length=8000)


class AiChatIn(BaseModel):
    messages: list[AiMessage] = Field(min_length=1, max_length=24)

    @model_validator(mode="after")
    def validate_total_context(self) -> "AiChatIn":
        if sum(len(message.content) for message in self.messages) > 48_000:
            raise ValueError("История диалога слишком большая")
        return self


class AiToolCallOut(BaseModel):
    name: str
    arguments: str = ""
    result_summary: str = ""
    result: dict = Field(default_factory=dict)


class AiChatOut(BaseModel):
    reply: str
    model: str = ""
    tool_calls: list[AiToolCallOut] = Field(default_factory=list)


# ---------- статистика «Главной» ----------


class StatsDayOut(BaseModel):
    day: str
    leads: int = 0


class StatsOut(BaseModel):
    databases: int = 0
    leads: int = 0
    offers: int = 0
    campaigns: int = 0
    verified_telegram: int = 0
    pending_telegram: int = 0
    sent_messages: int = 0
    replies: int = 0
    reply_rate: float = 0.0
    # new | contacted | replied | interested | unsubscribed
    leads_by_status: dict[str, int] = {}
    # окно графика с сегодняшним днём включительно, по возрастанию дат
    leads_per_day: list[StatsDayOut] = []
    # Размер окна графика и сумма за предыдущее такое же окно — для строки
    # «+40% к прошлой неделе». Считаем на сервере: у клиента нет данных
    # за прошлый период, и додумывать их из графика было бы враньём.
    chart_days: int = 14
    chart_leads: int = 0
    prev_chart_leads: int = 0


# ---------- админ-панель ----------


class AdminUserOut(BaseModel):
    """Строка сводки по пользователю. Секретов здесь нет намеренно:
    ни токенов, ни значений API-ключей — только счётчики и публичный профиль."""

    id: int
    telegram_id: int
    username: str | None
    name: str
    photo_url: str | None
    created_at: datetime | None
    is_admin: bool = False
    databases: int = 0
    leads: int = 0
    offers: int = 0
    campaigns: int = 0
    warmup_accounts: int = 0
    api_keys: int = 0
    # подписка
    plan: str = "none"
    plan_name: str = "Без тарифа"
    sub_status: str = "none"
    blocked: bool = False
    paid_until: datetime | None = None
    expired: bool = False
    # Кто это и откуда. С открытой регистрацией одного имени мало: нужно
    # видеть, когда человек заходил в последний раз и с какого адреса.
    last_login_at: datetime | None = None
    last_ip: str = ""
    # Подозрительные признаки простым текстом, чтобы админ не гадал по цифрам.
    flags: list[str] = []


class AdminUsersOut(BaseModel):
    users: list[AdminUserOut] = []
    total_users: int = 0
    total_leads: int = 0
    offset: int = 0
    limit: int = 50
    query: str = ""
    scope: str = "all"  # all | trial | paid | expired | blocked | flagged
    counts: dict[str, int] = {}  # сколько человек в каждой группе
    open_registration: bool = True


class AdminRegistrationIn(BaseModel):
    """Рубильник самостоятельной регистрации."""

    open: bool


class AdminDatabaseOut(BaseModel):
    id: int
    name: str
    source: str | None
    mode: str | None
    leads: int = 0
    created_at: datetime | None


class AdminLoginAttemptOut(BaseModel):
    """Одна попытка входа для журнала админки.

    Полей достаточно, чтобы ловить мультиаккаунты: один IP/устройство у разных
    telegram_id — повод присмотреться. Причина отклонения — сырой код из
    security/access (ok, registration_closed, hash_mismatch и т.п.)."""

    id: int
    telegram_id: int | None = None
    username: str = ""
    name: str = ""
    ok: bool = False
    reason: str = ""
    ip: str = ""
    user_agent: str = ""
    created_at: datetime | None = None


class AdminUserDetailOut(BaseModel):
    """Подробности по одному пользователю для админа.

    Настройки ИИ сюда не входят: провайдер задаётся окружением сервера.
    """

    user: AdminUserOut
    databases_list: list[AdminDatabaseOut] = []
    leads_by_status: dict[str, int] = {}
    offers_list: list[str] = []
    campaigns_list: list[str] = []
    monthly_limit: int = 0
    monthly_used: int = 0
    recent_events: list[EventLogOut] = []
    login_attempts: list[AdminLoginAttemptOut] = []
    first_purchase: bool = True  # ещё не покупал — действует скидка на первую покупку
    # подписка
    plan: str = "none"
    stored_plan: str = "none"
    sub_status: str = "none"
    blocked: bool = False
    paid_until: datetime | None = None
    sub_note: str = ""
    leads_this_month: int = 0


# ---------- админ: управление подпиской ----------


class AdminActivateIn(BaseModel):
    """Ручная активация купленного плана после оплаты в личку."""

    plan: str  # none | pro | max | ultra
    months: int = 1  # на сколько месяцев продлить (>=1)
    note: str = ""  # заметка: за что/когда платил
    # Сколько заплатил клиент, в обычных единицах. Ноль — активация без денег
    # (подарок, компенсация): запись в «Деньгах» тогда не создаётся.
    # allow_inf_nan: Infinity/NaN из JSON — это 422 на входе, а не 500 в
    # round() по дороге к минорным единицам. Потолок — миллиард: 1e308 без
    # него переполнял бы минорные единицы; отрицательные — возвраты.
    amount: float = Field(default=0.0, allow_inf_nan=False, ge=-1e9, le=1e9)
    currency: str = "RUB"


class AdminGrantDaysIn(BaseModel):
    """Выдать (или снять) дни доступа.

    Отрицательное число разрешено: админ должен уметь отменить свою же ошибку.
    """

    days: int = Field(ge=-3650, le=3650)
    note: str = ""

    @field_validator("days")
    @classmethod
    def _not_zero(cls, value: int) -> int:
        if value == 0:
            raise ValueError("Число дней не может быть нулём")
        return value


class AdminGrantDaysAllIn(AdminGrantDaysIn):
    """То же, но всем сразу.

    Всегда только действующим клиентам: раздать платный доступ вообще всем,
    кто когда-либо заходил, — это не «акция», а потеря денег. Выбора здесь
    сознательно нет — галочку «всем подряд» однажды нажали бы случайно.
    """


class AdminGrantDaysResultOut(BaseModel):
    affected: int
    days: int
    message: str


class AdminPromoIn(BaseModel):
    code: str = Field(min_length=1, max_length=40)
    discount_pct: int = Field(ge=1, le=100)
    valid_until: datetime | None = None
    max_uses: int = Field(default=0, ge=0)  # 0 — без ограничения
    note: str = ""


class AdminPromoPatchIn(BaseModel):
    discount_pct: int | None = Field(default=None, ge=1, le=100)
    active: bool | None = None
    valid_until: datetime | None = None
    max_uses: int | None = Field(default=None, ge=0)
    note: str | None = None


class AdminPromoOut(BaseModel):
    id: int
    code: str
    discount_pct: int
    active: bool
    valid_until: datetime | None = None
    max_uses: int = 0
    used_count: int = 0
    note: str = ""
    created_at: datetime | None = None
    usable: bool = True  # годен ли прямо сейчас

    model_config = ConfigDict(from_attributes=True)


class AdminPaymentIn(BaseModel):
    """Записать полученный платёж.

    Сумму вводит админ, а не подставляет система: у одного и того же тарифа
    бывает скидка, доплата и частичная оплата, и «цена по прайсу» в отчёте
    была бы выдумкой.
    """

    # В обычных единицах — так её и вводят. В минорные переводим на сервере,
    # чтобы округление было в одном месте, а не в каждом клиенте.
    # allow_inf_nan: Infinity/NaN — 422 на входе, а не 500 в round().
    # Границы — как в AdminActivateIn: без них 1e308 переполнял бы минорные.
    amount: float = Field(allow_inf_nan=False, ge=-1e9, le=1e9)
    currency: str = "RUB"
    plan: str = ""
    months: int = 1
    promo_code: str = Field(default="", max_length=40)
    note: str = Field(default="", max_length=2000)
    # Пусто — «сейчас». Задним числом заводить можно: деньги часто приходят
    # раньше, чем до них доходят руки.
    paid_at: datetime | None = None


class AdminPaymentOut(BaseModel):
    id: int
    owner_id: int
    amount: float
    currency: str
    plan: str = ""
    months: int = 1
    promo_code: str = ""
    note: str = ""
    paid_at: datetime | None = None
    user_label: str = ""

    model_config = ConfigDict(from_attributes=True)


class AdminRevenueOut(BaseModel):
    """Сводка выручки. По валютам раздельно: курса на дату платежа мы не
    храним, а пересчёт по сегодняшнему менял бы прошлые месяцы каждый день."""

    month: dict[str, float] = {}
    last_30_days: dict[str, float] = {}
    all_time: dict[str, float] = {}
    payments_total: int = 0
    paying_users_this_month: int = 0


class AdminPaymentListOut(BaseModel):
    payments: list[AdminPaymentOut] = []
    total: int = 0
    revenue: AdminRevenueOut = AdminRevenueOut()


class AdminBlockIn(BaseModel):
    blocked: bool
    note: str = ""


class AdminNotificationIn(BaseModel):
    target: Literal["all", "active", "user"] = "all"
    user_id: int | None = Field(default=None, ge=1)
    message: str = Field(min_length=1, max_length=2000)
    level: Literal["info", "success", "warn", "error"] = "info"
    telegram: bool = False

    @model_validator(mode="after")
    def validate_target(self) -> "AdminNotificationIn":
        if self.target == "user" and self.user_id is None:
            raise ValueError("для конкретного адресата нужен user_id")
        return self


class AdminNotificationOut(BaseModel):
    recipients: int = 0
    in_app: int = 0
    telegram_queued: int = 0


# ---------- админ: белый список доступа ----------


class AdminAuditOut(BaseModel):
    """Одно событие в общем журнале активности.

    В отличие от EventLogOut здесь есть автор события: в личном журнале
    пользователь и так знает, что это он, а в админке без имени журнал
    бесполезен.
    """

    id: int
    owner_id: int | None = None
    username: str = ""
    user_name: str = ""
    scope: str | None = None
    level: str | None = None
    message: str
    ref_type: str | None = None
    ref_id: int | None = None
    ip: str = ""  # пусто у фоновых задач: у них нет запроса
    created_at: datetime | None = None


class AdminAuditListOut(BaseModel):
    events: list[AdminAuditOut] = []
    total: int = 0
    offset: int = 0
    limit: int = 100


class AdminVisitorOut(BaseModel):
    """Кто заходил на сайт — строка для сводки админки."""

    user_id: int | None = None
    username: str = ""
    name: str = ""
    ip: str = ""
    ok: bool = True
    reason: str = ""
    at: datetime | None = None


class AdminDayPointOut(BaseModel):
    """Один день на графике активности."""

    day: str
    logins: int = 0
    events: int = 0


class AdminSeriesPointOut(BaseModel):
    """Точка графика активности: готовая подпись и два счётчика."""

    label: str
    logins: int = 0
    events: int = 0


class AdminSeriesOut(BaseModel):
    """График активности под выбранный период («сегодня» — по часам,
    недели и месяц — по дням, «год» — по месяцам)."""

    period: str
    points: list[AdminSeriesPointOut] = []


class AdminOverviewOut(BaseModel):
    """Сводка для вкладки «Админка».

    Здесь только агрегаты и публичные поля профиля: ни токенов, ни
    значений API-ключей — сознательно, как и в остальной админке.
    """

    online_now: int = 0  # была активность за последние 15 минут
    logins_today: int = 0
    failed_logins_today: int = 0
    events_today: int = 0
    users_total: int = 0
    users_new_7d: int = 0
    active_subs: int = 0  # платные: пробный тариф сюда не входит
    trial_subs: int = 0
    blocked_users: int = 0
    leads_total: int = 0
    offers_total: int = 0
    campaigns_total: int = 0
    running_campaigns: int = 0
    connected_accounts: int = 0
    pending_verifications: int = 0
    errors_today: int = 0
    recent_visitors: list[AdminVisitorOut] = []
    recent_actions: list[AdminAuditOut] = []
    by_day: list[AdminDayPointOut] = []
    # Деньги на главной админки: без них владелец сервиса не видит главного
    # своего показателя и лезет считать его руками по заметкам.
    revenue: AdminRevenueOut = AdminRevenueOut()


class AdminRecomputeScoreOut(BaseModel):
    """Результат пересчёта балла старых лидов."""

    total: int = 0
    updated: int = 0


class TelegramAccountTicketOut(BaseModel):
    """Короткий билет для формы подключения Telegram-аккаунта.

    tg-mini-client не знает ни про JWT сайта, ни про базу пользователей. Бэкенд
    подписывает «id.срок» общим секретом (TG_CLIENT_SECRET), а клиент по подписи
    понимает, чью сессию Telegram подключать. Живёт минуты: нужен только на
    момент входа, дальше серверные операции используют сохранённую сессию.
    """

    ticket: str
    account_id: int | None = None
    expires_in: int


# ---------- переписка в кабинете ----------


class ChatDialogOut(BaseModel):
    """Строка списка диалогов.

    peer_id — строка, а не int: Telegram давно выдаёт идентификаторы, которые
    не помещаются в 53 бита JavaScript, и в JSON число потеряло бы последние
    цифры. Ошибка в идентификаторе здесь означает открытый не тот чат.
    """

    peer_id: str = ""
    kind: str = "user"
    title: str = ""
    username: str = ""
    can_send: bool = True
    verified: bool = False
    is_self: bool = False
    unread: int = 0
    muted: bool = False
    pinned: bool = False
    last_message: str = ""
    last_out: bool = False
    last_date: int = 0
    has_photo: bool = False
    # Заполняется бэкендом: этот собеседник есть в базе лидов.
    lead_id: int | None = None


class ChatDialogCursorOut(BaseModel):
    """Курсор постраничной выдачи Telegram: дата, сообщение и собеседник."""

    offset_date: int = 0
    offset_id: int = 0
    offset_peer: str = ""


class ChatDialogsOut(BaseModel):
    items: list[ChatDialogOut] = []
    next: ChatDialogCursorOut | None = None
    account_id: int | None = None


class ChatMediaOut(BaseModel):
    kind: str = ""
    loadable: bool = False
    name: str = ""
    size: int = 0
    duration: int = 0

    @field_validator("size", "duration", mode="before")
    @classmethod
    def round_numbers(cls, value: object) -> int:
        """Telegram отдаёт длительность голосового дробной (12.8 с).

        Строгая модель роняла всю историю переписки в 500 — жалоба владельца
        «в чате Внутренняя ошибка сервера». Округляем до целых секунд/байтов.
        """
        if value is None or value == "":
            return 0
        try:
            return int(float(value))
        except (TypeError, ValueError):
            return 0


class ChatMessageOut(BaseModel):
    id: int = 0
    out: bool = False
    date: int = 0
    edited: int = 0
    text: str = ""
    service: bool = False
    media: ChatMediaOut | None = None
    reply_to_id: int = 0
    sender_id: str = ""
    sender_name: str = ""
    views: int = 0


class ChatHistoryOut(BaseModel):
    items: list[ChatMessageOut] = []
    # 0 означает «дальше в прошлое ничего нет», отдельного флага не нужно.
    next_offset_id: int = 0


class ChatSendIn(BaseModel):
    account_id: int | None = None
    peer_id: str
    text: str
    reply_to: int = 0


class ChatSendOut(BaseModel):
    message_id: int = 0
    date: int = 0


class ChatReadIn(BaseModel):
    account_id: int | None = None
    peer_id: str


class ChatPinIn(BaseModel):
    account_id: int | None = None
    peer_id: str
    pinned: bool = True


class ChatMuteIn(BaseModel):
    account_id: int | None = None
    peer_id: str
    muted: bool = True


class ChatImageOut(BaseModel):
    """Картинка одним data:URL.

    Отдаём строкой, а не потоком байт: содержимое приходит из шлюза уже
    закодированным, и раскодировать его ради повторного кодирования в HTTP-ответ
    значило бы гонять одни и те же байты трижды без единой выгоды.
    """

    image: str = ""


class ChatAccountOut(BaseModel):
    """Подключённый Telegram-аккаунт в переключателе над списком диалогов."""

    id: int
    label: str = ""
    username: str = ""
    connected: bool = False


# ---------- сеансы входа ----------


class SessionOut(BaseModel):
    """Один вход в аккаунт.

    jti — идентификатор токена, он же ключ для закрытия сеанса. Отдавать его
    безопасно: сам токен по jti не восстановить, а без него нечем указать,
    какую именно строку закрыть.
    """

    jti: str = ""
    device: str = ""
    ip: str = ""
    created_at: datetime | None = None
    last_seen_at: datetime | None = None
    current: bool = False


class SessionsOut(BaseModel):
    items: list[SessionOut] = []


class SessionRevokeIn(BaseModel):
    jti: str


# ---------- риск блокировки аккаунта ----------


class AccountRiskOut(BaseModel):
    """Вердикт по одному Telegram-аккаунту простым языком."""

    account_id: int
    label: str = ""
    level: str = "ok"
    title: str = ""
    reasons: list[str] = []
    advice: list[str] = []
    cooldown_until: datetime | None = None
    can_send: bool = True


class AccountRiskListOut(BaseModel):
    # Общий уровень — по худшему аккаунту: один заблокированный номер среди
    # пяти здоровых остаётся проблемой.
    level: str = "ok"
    items: list[AccountRiskOut] = []


class AdminViewAsOut(BaseModel):
    """Доступ на просмотр чужого кабинета.

    Отдельная схема, а не TokenOut: у этого токена другой смысл и другой срок,
    и путать их в коде фронтенда нельзя — обычный вход перезаписал бы админскую
    сессию, и вернуться из просмотра было бы некуда.
    """

    access_token: str
    expires_in: int
    user_name: str = ""




# ---------- поддержка ----------


class SupportMessageOut(BaseModel):
    """Одно сообщение переписки с поддержкой."""

    id: int
    author: Literal["user", "admin"]
    text: str
    created_at: datetime | None = None
    # Прочитано второй стороной. Клиенту показывает, что вопрос увидели.
    read: bool = False


class SupportThreadOut(BaseModel):
    messages: list[SupportMessageOut] = []
    # Сколько ответов владельца было непрочитанными на момент открытия.
    unread: int = 0
    can_write: bool = True


class SupportUnreadOut(BaseModel):
    unread: int = 0


class SupportSendIn(BaseModel):
    text: str = Field(min_length=1, max_length=2000)


class AdminSupportThreadOut(BaseModel):
    """Строка списка обращений в админке."""

    user_id: int
    name: str = ""
    username: str = ""
    telegram_id: int | None = None
    messages: int = 0
    unread: int = 0
    last_at: datetime | None = None


class AdminSupportListOut(BaseModel):
    threads: list[AdminSupportThreadOut] = []
    unread_total: int = 0


class AdminSupportReplyIn(BaseModel):
    text: str = Field(min_length=1, max_length=2000)
