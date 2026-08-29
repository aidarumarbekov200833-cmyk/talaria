"""Извлечение и верификация контактов лида.

Поток намеренно разделён на два шага:

1. Из публичного описания собираются все Telegram/e-mail кандидаты и
   ранжируются по контексту.
2. Telegram-кандидаты проверяются через подключённые аккаунты. В `tg_username`
   попадает только личный аккаунт; канал хранится отдельно и используется как
   источник контента для AI-оффера.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from sqlalchemy.orm import object_session

from .account_health import apply_error, classify_error, mark_success, release_expired
from .models import WarmupAccount
from .telegram_gateway import TelegramProfile, gateway

if TYPE_CHECKING:
    from .models import Lead


@dataclass(frozen=True)
class TelegramCandidate:
    handle: str
    score: int
    source: str


_TME_RE = re.compile(
    r"(?:https?://)?(?:www\.)?(?:t\.me|telegram\.me|telegram\.dog)/(?:s/)?([A-Za-z0-9_+]{4,64})",
    re.IGNORECASE,
)
_TG_DEEP_RE = re.compile(r"tg://resolve\?domain=([A-Za-z0-9_]{4,32})", re.IGNORECASE)
_AT_NEAR_RE = re.compile(
    r"(?:telegram|telega|телеграм|телега|тг|tg)\W{0,16}@([A-Za-z0-9_]{4,32})",
    re.IGNORECASE,
)
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]{1,64}@[A-Za-z0-9.-]{2,63}\.[A-Za-z]{2,16}")

_TME_STOP = {
    "joinchat",
    "share",
    "addstickers",
    "proxy",
    "socks",
    "iv",
    "setlanguage",
    "addtheme",
    "bg",
    "login",
    "telegram",
    "durov",
    "boost",
    "addemoji",
    "giftcode",
    "invoice",
    "contact",
    "confirmphone",
    "premium",
    "settings",
    "download",
    "faq",
    "apps",
}
_CONTACT_WORDS = (
    "связ",
    "контакт",
    "личн",
    "автор",
    "владел",
    "админ",
    "менеджер",
    "писать",
    "пишите",
    "реклама",
    "сотруднич",
    "business",
    "contact",
    "owner",
    "admin",
    "manager",
    "dm",
    "collab",
)
_CHANNEL_WORDS = (
    "канал",
    "новост",
    "сообщество",
    "чат",
    "community",
    "channel",
    "news",
    "group",
)
_EMAIL_JUNK_SUFFIXES = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg")


def _candidate_score(raw: str, start: int, source: str, handle: str) -> int:
    # Контекст ограничиваем текущим предложением/строкой. Фиксированное широкое
    # окно приписывало ссылке на канал слова «для сотрудничества» из следующего
    # предложения и поднимало её выше личного контакта.
    left_candidates = [raw.rfind(marker, 0, start) for marker in (". ", "\n", ";")]
    left = max(left_candidates) + 1
    ends = [raw.find(marker, start + len(handle)) for marker in (". ", "\n", ";")]
    right_candidates = [value for value in ends if value >= 0]
    right = min(right_candidates) if right_candidates else len(raw)
    context = raw[left:right].lower()
    score = {"link": 50, "deep-link": 45, "mention": 35}.get(source, 30)
    score += 30 if any(word in context for word in _CONTACT_WORDS) else 0
    score -= 20 if any(word in context for word in _CHANNEL_WORDS) else 0
    score -= 55 if handle.lower().endswith("bot") else 0
    return score


def extract_telegram_candidates(*texts: str) -> list[TelegramCandidate]:
    """Все правдоподобные Telegram-контакты, лучшие первыми."""
    best: dict[str, TelegramCandidate] = {}
    patterns = ((_TME_RE, "link"), (_TG_DEEP_RE, "deep-link"), (_AT_NEAR_RE, "mention"))
    for raw in texts:
        if not raw:
            continue
        for pattern, source in patterns:
            for match in pattern.finditer(raw):
                handle = match.group(1).strip().lstrip("@")
                normalized = handle.lower()
                if handle.startswith("+") or normalized in _TME_STOP:
                    continue
                if not re.fullmatch(r"[A-Za-z0-9_]{4,32}", handle):
                    continue
                candidate = TelegramCandidate(
                    handle=handle,
                    score=_candidate_score(raw, match.start(), source, handle),
                    source=source,
                )
                current = best.get(normalized)
                if current is None or candidate.score > current.score:
                    best[normalized] = candidate
    return sorted(best.values(), key=lambda item: (-item.score, item.handle.lower()))


def extract_telegram(*texts: str) -> str:
    """Лучший Telegram-кандидат; совместимый фасад для старых вызовов."""
    candidates = extract_telegram_candidates(*texts)
    return candidates[0].handle if candidates else ""


def extract_email(*texts: str) -> str:
    """Первый уникальный e-mail, исключая имена файлов изображений."""
    for raw in texts:
        if not raw:
            continue
        for found in _EMAIL_RE.finditer(raw):
            email = found.group(0).strip(".,;:()[]<>")
            if email.lower().endswith(_EMAIL_JUNK_SUFFIXES):
                continue
            return email
    return ""


def lead_candidates(lead: Lead) -> list[TelegramCandidate]:
    """Кандидаты из сохранённых полей и исходного описания лида."""
    parsed = extract_telegram_candidates(lead.description or "", lead.title or "")
    seeded: list[TelegramCandidate] = []
    if lead.tg_username:
        seeded.append(TelegramCandidate(lead.tg_username.lstrip("@"), 120, "saved-user"))
    if lead.telegram_channel:
        seeded.append(TelegramCandidate(lead.telegram_channel.lstrip("@"), 80, "saved-channel"))
    best: dict[str, TelegramCandidate] = {}
    for candidate in [*seeded, *parsed]:
        key = candidate.handle.lower()
        if key not in best or candidate.score > best[key].score:
            best[key] = candidate
    return sorted(best.values(), key=lambda item: (-item.score, item.handle.lower()))


def resolve_telegram(handle: str, owner_id: int, account_id: int | None = None) -> dict:
    """Совместимый словарный ответ для единичной проверки."""
    return gateway.resolve(owner_id, handle, account_id).to_dict()


def verify_lead(
    lead: Lead,
    owner_id: int,
    account_ids: list[int],
    *,
    start_index: int = 0,
    extra_handles: list[str] | None = None,
) -> TelegramProfile:
    """Проверить кандидатов и записать нормализованный результат в лида."""
    db = object_session(lead)
    if db is not None:
        release_expired(db, owner_id)
        account_ids = [
            row[0]
            for row in db.query(WarmupAccount.id)
            .filter(
                WarmupAccount.owner_id == owner_id,
                WarmupAccount.id.in_(account_ids),
                WarmupAccount.operational_status == "ready",
            )
            .order_by(WarmupAccount.id)
            .all()
        ]
    candidates = lead_candidates(lead)
    if extra_handles:
        preferred = [
            TelegramCandidate(value.strip().lstrip("@"), 200 - index, "manual")
            for index, value in enumerate(extra_handles)
            if value and value.strip()
        ]
        merged = {candidate.handle.lower(): candidate for candidate in candidates}
        for candidate in preferred:
            merged[candidate.handle.lower()] = candidate
        candidates = sorted(merged.values(), key=lambda item: (-item.score, item.handle.lower()))
    now = datetime.now(UTC)
    lead.contacts_checked_at = lead.contacts_checked_at or now
    if not candidates:
        lead.tg_status = "missing"
        lead.tg_error = "Telegram-контакты не найдены в публичных данных"
        return TelegramProfile(error=lead.tg_error)

    # Первый кандидат остаётся как канал-источник до проверки. Личным
    # получателем он станет только после ответа Telegram kind=user.
    if not lead.telegram_channel:
        lead.telegram_channel = candidates[0].handle[:64]
        lead.telegram_url = f"https://t.me/{candidates[0].handle}"[:256]
    lead.tg_status = "pending"

    result = gateway.verify_candidates(
        owner_id,
        [candidate.handle for candidate in candidates],
        account_ids,
        start_index=start_index,
    )
    if db is not None:
        for issue in result.account_issues:
            account = db.get(WarmupAccount, issue.account_id)
            decision = classify_error(issue.code, issue.error, issue.retry_after)
            if account and account.owner_id == owner_id and decision.quarantine:
                apply_error(
                    account,
                    code=issue.code,
                    error=issue.error,
                    retry_after=issue.retry_after,
                )
        if result.ok and result.account_id is not None:
            account = db.get(WarmupAccount, result.account_id)
            if account and account.owner_id == owner_id:
                mark_success(account)
    lead.tg_checked_at = now
    lead.tg_verified_by_account_id = result.account_id
    lead.tg_kind = result.kind[:20]

    if result.sendable:
        lead.tg_id = result.tg_id
        lead.tg_username = result.username
        lead.tg_first_name = result.first_name
        lead.tg_last_name = result.last_name
        lead.tg_status = "verified"
        lead.tg_error = ""
        return result

    # Не оставляем в поле получателя канал, бота или подозрительный профиль.
    lead.tg_username = ""
    lead.tg_first_name = ""
    lead.tg_last_name = ""
    lead.tg_id = result.tg_id
    if result.ok:
        if result.username:
            lead.telegram_channel = result.username
            lead.telegram_url = f"https://t.me/{result.username}"[:256]
        if result.scam or result.fake:
            lead.tg_status = "rejected"
            lead.tg_error = "Telegram пометил профиль как scam/fake"
        elif result.kind == "bot":
            lead.tg_status = "bot"
            lead.tg_error = "Контакт ведёт на бота, а не на личный аккаунт"
        else:
            lead.tg_status = "channel"
            lead.tg_error = "Найден Telegram-канал/группа, личный аккаунт не найден"
    else:
        lead.tg_status = "error"
        lead.tg_error = (result.error or "Контакт не найден")[:500]
    return result
