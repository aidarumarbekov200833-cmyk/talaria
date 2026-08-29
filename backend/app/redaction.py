"""Dependency-free, fail-closed redaction for logs and diagnostic sinks."""

from __future__ import annotations

import re
import threading
from collections.abc import Mapping
from urllib.parse import quote, quote_plus

REDACTED = "[REDACTED]"
_MAX_DEPTH = 12
_LOCK = threading.RLock()
_SECRETS: set[str] = set()

_SENSITIVE_KEYS = frozenset(
    {
        "key",
        "api_key",
        "access_token",
        "authorization",
        "bearer",
        "bot_token",
        "jwt",
        "jwt_secret",
        "password",
        "secret",
        "tg_client_secret",
        "x_send_secret",
        "sentry_dsn",
        "session",
        "session_string",
        "string_session",
        "telegram_session",
    }
)
_TEXT_KEYS = "|".join(
    sorted(
        (re.escape(key).replace("_", "[_-]") for key in _SENSITIVE_KEYS),
        key=len,
        reverse=True,
    )
)
_BEARER_RE = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+")
_JWE_RE = re.compile(r"\beyJ[A-Za-z0-9_-]*(?:\.[A-Za-z0-9_-]+){4}\b")
_JWT_RE = re.compile(r"\beyJ[A-Za-z0-9_-]*(?:\.[A-Za-z0-9_-]+){2}\b")
_BOT_TOKEN_RE = re.compile(r"(?<!\d)\d{6,12}:[A-Za-z0-9_-]{20,}(?![A-Za-z0-9_-])")
_GOOGLE_KEY_RE = re.compile(r"\bAIza[A-Za-z0-9_-]{20,}\b")
_STRING_SESSION_RE = re.compile(r"(?i)\bStringSession\s*\(\s*[^)\s]{20,}\s*\)")
_LONG_TELEGRAM_SESSION_RE = re.compile(r"(?<![A-Za-z0-9_-])1[A-Za-z0-9_-]{80,}(?![A-Za-z0-9_-])")
_ASSIGNMENT_RE = re.compile(
    rf"(?i)(?P<prefix>(?<![A-Za-z0-9_])(?:{_TEXT_KEYS})\s*(?:=|:)\s*)"
    r"(?P<quote>['\"]?)(?P<value>[^&\s,}\]\"']+)(?P=quote)"
)


def _stringify(value: object) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    try:
        return str(value)
    except Exception:
        try:
            return repr(value)
        except Exception:
            return REDACTED


def register_secret(*values: object) -> None:
    """Register exact and URL-encoded forms without exposing the registry."""
    variants: set[str] = set()
    for value in values:
        if value is None:
            continue
        raw = _stringify(value)
        if raw == "":
            continue
        variants.update({raw, quote(raw, safe=""), quote_plus(raw, safe="")})
    if variants:
        with _LOCK:
            _SECRETS.update(variants)


def _replace_assignment(match: re.Match[str]) -> str:
    return f"{match.group('prefix')}{REDACTED}"


def _redact_text(value: object) -> str:
    text = _stringify(value)
    with _LOCK:
        secrets = tuple(sorted(_SECRETS, key=len, reverse=True))
    for secret in secrets:
        if secret:
            text = text.replace(secret, REDACTED)
    text = _BEARER_RE.sub(f"Bearer {REDACTED}", text)
    text = _JWE_RE.sub(REDACTED, text)
    text = _JWT_RE.sub(REDACTED, text)
    text = _BOT_TOKEN_RE.sub(REDACTED, text)
    text = _GOOGLE_KEY_RE.sub(REDACTED, text)
    text = _STRING_SESSION_RE.sub(f"StringSession({REDACTED})", text)
    text = _LONG_TELEGRAM_SESSION_RE.sub(REDACTED, text)
    return _ASSIGNMENT_RE.sub(_replace_assignment, text)


def redact_text(value: object) -> str:
    """Return text with registered and credential-shaped values removed."""
    try:
        return _redact_text(value)
    except Exception:
        return REDACTED


def _normalized_key(value: object) -> str:
    text = _stringify(value).strip().lower()
    return re.sub(r"[^a-z0-9]+", "_", text).strip("_")


def _redact_value(value: object, *, depth: int, seen: set[int]) -> object:
    if depth > _MAX_DEPTH:
        return REDACTED
    if value is None or isinstance(value, bool | int | float):
        return value
    if isinstance(value, str | bytes | BaseException):
        return _redact_text(value)

    if isinstance(value, Mapping):
        marker = id(value)
        if marker in seen:
            return REDACTED
        seen.add(marker)
        try:
            output: dict[object, object] = {}
            for key, item in value.items():
                safe_key = _redact_text(key)
                output[safe_key] = (
                    REDACTED
                    if _normalized_key(key) in _SENSITIVE_KEYS
                    else _redact_value(item, depth=depth + 1, seen=seen)
                )
            return output
        finally:
            seen.discard(marker)

    if isinstance(value, list | tuple | set | frozenset):
        marker = id(value)
        if marker in seen:
            return REDACTED
        seen.add(marker)
        try:
            items = [_redact_value(item, depth=depth + 1, seen=seen) for item in value]
            if isinstance(value, tuple):
                return tuple(items)
            if isinstance(value, set):
                return set(items)
            if isinstance(value, frozenset):
                return frozenset(items)
            return items
        finally:
            seen.discard(marker)

    return _redact_text(value)


def redact_value(value: object) -> object:
    """Recursively sanitize a diagnostic value while preserving safe shape."""
    try:
        return _redact_value(value, depth=0, seen=set())
    except Exception:
        return REDACTED


__all__ = ["REDACTED", "register_secret", "redact_text", "redact_value"]
