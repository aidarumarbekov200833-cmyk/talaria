"""Единый клиент серверного Telegram-gateway.

GramJS-сервис — единственное место, где живёт строка сессии, поэтому всё
общение с Telegram идёт через него: проверка контакта, публичные посты для
AI-оффера, рассылка и переписка в кабинете. Все модули backend используют этот
клиент, поэтому формат ответа и обработка ошибок не расходятся.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import asdict, dataclass, field, replace
from typing import Any

import httpx

from .config import settings


@dataclass(frozen=True)
class TelegramAccountIssue:
    account_id: int
    error: str
    code: str = ""
    retry_after: int | None = None


@dataclass(frozen=True)
class TelegramProfile:
    """Нормализованный результат MTProto resolve."""

    ok: bool = False
    username: str = ""
    tg_id: str = ""
    kind: str = ""
    first_name: str = ""
    last_name: str = ""
    title: str = ""
    verified: bool = False
    scam: bool = False
    fake: bool = False
    account_id: int | None = None
    error: str = ""
    error_code: str = ""
    retry_after: int | None = None
    account_issues: tuple[TelegramAccountIssue, ...] = field(default_factory=tuple)

    @property
    def is_user(self) -> bool:
        return self.ok and self.kind == "user"

    @property
    def sendable(self) -> bool:
        return self.is_user and bool(self.username) and not self.scam and not self.fake

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["is_user"] = self.is_user
        data["sendable"] = self.sendable
        return data


class TelegramGatewayError(RuntimeError):
    """Structured error returned by tg-mini-client."""

    def __init__(self, message: str, *, code: str = "", retry_after: int | None = None) -> None:
        super().__init__(message)
        self.code = (code or "")[:64]
        self.retry_after = retry_after


class TelegramGateway:
    """Синхронный клиент внутреннего GramJS-сервиса."""

    def __init__(self, base_url: str | None = None, secret: str | None = None) -> None:
        self.base_url = (base_url if base_url is not None else settings.tg_client_url).rstrip("/")
        self.secret = secret if secret is not None else settings.tg_client_secret

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.secret:
            headers["X-Send-Secret"] = self.secret
        return headers

    def _post(self, path: str, payload: dict[str, Any], timeout: float) -> dict[str, Any]:
        if not self.base_url:
            raise TelegramGatewayError("Telegram-gateway не настроен")
        try:
            response = httpx.post(
                self.base_url + path,
                json=payload,
                headers=self._headers(),
                timeout=timeout,
            )
        except httpx.RequestError as exc:
            raise TelegramGatewayError(f"Telegram-gateway недоступен: {exc}") from exc
        try:
            body = response.json()
        except ValueError as exc:
            raise TelegramGatewayError(
                f"Telegram-gateway вернул HTTP {response.status_code} без JSON"
            ) from exc
        if response.status_code >= 400 or not body.get("ok"):
            detail = str(body.get("error") or f"HTTP {response.status_code}")[:500]
            raw_retry = body.get("retry_after")
            try:
                retry_after = int(raw_retry) if raw_retry is not None else None
            except (TypeError, ValueError):
                retry_after = None
            raise TelegramGatewayError(
                detail,
                code=str(body.get("error_code") or ""),
                retry_after=retry_after,
            )
        return body

    def resolve(
        self,
        owner_id: int,
        username: str,
        account_id: int | None = None,
    ) -> TelegramProfile:
        handle = (username or "").strip().lstrip("@")
        if not handle:
            return TelegramProfile(error="Пустой username", account_id=account_id)
        payload: dict[str, Any] = {"owner_id": owner_id, "username": handle}
        if account_id is not None:
            payload["account_id"] = account_id
        try:
            body = self._post("/resolve", payload, 20.0)
        except (httpx.RequestError, RuntimeError) as exc:
            return TelegramProfile(
                error=str(exc),
                error_code=str(getattr(exc, "code", "")),
                retry_after=getattr(exc, "retry_after", None),
                account_id=account_id,
            )

        profile_value = body.get("profile")
        raw: dict[str, Any] = profile_value if isinstance(profile_value, dict) else body
        kind = str(raw.get("kind") or "").lower()
        resolved_username = str(raw.get("username") or handle).strip().lstrip("@")
        return TelegramProfile(
            ok=True,
            username=resolved_username[:64],
            tg_id=str(raw.get("id") or "")[:40],
            kind=kind,
            first_name=str(raw.get("first_name") or "")[:128],
            last_name=str(raw.get("last_name") or "")[:128],
            title=str(raw.get("title") or "")[:300],
            verified=bool(raw.get("verified")),
            scam=bool(raw.get("scam")),
            fake=bool(raw.get("fake")),
            account_id=account_id,
        )

    def verify_candidates(
        self,
        owner_id: int,
        usernames: Iterable[str],
        account_ids: Iterable[int],
        *,
        start_index: int = 0,
    ) -> TelegramProfile:
        """Найти первый подтверждённый личный аккаунт.

        Каждый кандидат проверяется через пул аккаунтов. Успешный канал/бот
        запоминается как диагностический результат, но поиск продолжается:
        описание YouTube часто содержит сначала канал, а затем личный контакт.
        """
        candidates = list(
            dict.fromkeys(
                (value or "").strip().lstrip("@") for value in usernames if (value or "").strip()
            )
        )
        accounts = list(dict.fromkeys(int(value) for value in account_ids))
        if not candidates:
            return TelegramProfile(error="Telegram-кандидаты не найдены")
        if not accounts:
            return TelegramProfile(error="Нет подключённых Telegram-аккаунтов")

        fallback: TelegramProfile | None = None
        issues: list[TelegramAccountIssue] = []
        last_error = "Контакт не найден"
        candidate_index = 0
        seen = {value.lower() for value in candidates}
        # Очередь может пополняться контактами из about/последних постов
        # канала или бота. Жёсткий потолок защищает от графа взаимных ссылок.
        while candidate_index < len(candidates) and candidate_index < 30:
            candidate = candidates[candidate_index]
            resolved: TelegramProfile | None = None
            for attempt in range(len(accounts)):
                account_id = accounts[(start_index + candidate_index + attempt) % len(accounts)]
                result = self.resolve(owner_id, candidate, account_id)
                if not result.ok:
                    last_error = result.error or last_error
                    issues.append(
                        TelegramAccountIssue(
                            account_id=account_id,
                            error=result.error or last_error,
                            code=result.error_code,
                            retry_after=result.retry_after,
                        )
                    )
                    continue
                resolved = result
                fallback = fallback or result
                if result.sendable:
                    return replace(result, account_issues=tuple(issues))
                # Ответ Telegram корректный; другой аккаунт увидит тот же тип
                # сущности, поэтому переходим к следующему кандидату.
                break
            if resolved and resolved.kind in {"channel", "group", "bot"}:
                account_id = (
                    resolved.account_id or accounts[(start_index + candidate_index) % len(accounts)]
                )
                for discovered in self.contact_candidates(owner_id, candidate, account_id):
                    key = discovered.lower()
                    if key not in seen:
                        seen.add(key)
                        candidates.append(discovered)
            candidate_index += 1
        if fallback is not None:
            return replace(fallback, account_issues=tuple(issues))
        return TelegramProfile(error=last_error, account_issues=tuple(issues))

    def contact_candidates(self, owner_id: int, username: str, account_id: int) -> list[str]:
        """Публичные личные контакты из about/постов канала или бота."""
        try:
            body = self._post(
                "/contact-candidates",
                {
                    "owner_id": owner_id,
                    "account_id": account_id,
                    "username": username,
                },
                35.0,
            )
        except RuntimeError:
            # Поиск владельца — дополнительная эвристика. Если Telegram не дал
            # прочитать about/посты, исходный channel/bot-результат всё равно
            # сохраняется и остаётся виден пользователю.
            return []
        result: list[str] = []
        for item in body.get("candidates") or []:
            raw = item.get("username") if isinstance(item, dict) else item
            handle = str(raw or "").strip().lstrip("@")
            if handle and handle.lower() not in {value.lower() for value in result}:
                result.append(handle[:64])
        return result[:30]

    def send(
        self,
        owner_id: int,
        account_id: int,
        username: str,
        text: str,
        *,
        image: str = "",
        images: list[str] | None = None,
        watch_seconds: int = 3600,
    ) -> dict[str, Any]:
        return self._post(
            "/send",
            {
                "owner_id": owner_id,
                "account_id": account_id,
                "username": username,
                "text": text,
                "image": image,
                # Альбом: список картинок одним сообщением. Пустой список шлюз
                # трактует как «медиа нет» и отправляет обычный текст.
                "images": list(images or []),
                "watch_seconds": max(0, min(3600, int(watch_seconds))),
            },
            45.0,
        )

    def session_status(self, owner_id: int, account_id: int) -> dict[str, Any]:
        """Подтвердить, что сохранённая MTProto-сессия всё ещё авторизована.

        Наличие строки в БД недостаточно: пользователь может завершить сессию
        в Telegram на другом устройстве. Проверку выполняет gateway, у которого
        единственного есть доступ к StringSession.
        """
        return self._post(
            "/session/status",
            {"owner_id": owner_id, "account_id": account_id},
            20.0,
        )

    def account_safety(self, owner_id: int, account_id: int) -> dict[str, Any]:
        """Фактический снимок непрочитанных личных диалогов Telegram."""
        return self._post(
            "/account/safety",
            {"owner_id": owner_id, "account_id": account_id},
            30.0,
        )

    def drop_session(self, owner_id: int, account_id: int) -> None:
        """Удалить локальную gateway-сессию при отмене/удалении слота."""
        self._post(
            "/session/drop",
            {"owner_id": owner_id, "account_id": account_id},
            15.0,
        )

    def spambot_status(self, owner_id: int, account_id: int) -> dict[str, Any]:
        return self._post(
            "/spambot/status",
            {"owner_id": owner_id, "account_id": account_id},
            35.0,
        )

    def spambot_appeal(self, owner_id: int, account_id: int) -> dict[str, Any]:
        return self._post(
            "/spambot/appeal",
            {"owner_id": owner_id, "account_id": account_id},
            35.0,
        )

    def channel_posts(
        self,
        owner_id: int,
        username: str,
        limit: int,
        account_id: int | None = None,
    ) -> list[str]:
        payload: dict[str, Any] = {
            "owner_id": owner_id,
            "username": username,
            "limit": max(1, min(20, int(limit))),
        }
        if account_id is not None:
            payload["account_id"] = account_id
        body = self._post("/channel-posts", payload, 30.0)
        result: list[str] = []
        for item in body.get("posts") or []:
            text = item.get("text") if isinstance(item, dict) else str(item)
            if text and text.strip():
                result.append(text.strip()[:1000])
        return result[: payload["limit"]]

    # --- Личная переписка --------------------------------------------------
    #
    # Отдельная группа методов: остальные ходят в Telegram ради лидов и
    # проверок, эти — ради собственной переписки владельца аккаунта. Данные
    # проходят насквозь и нигде не сохраняются: приложению незачем держать у
    # себя копию чужих личных сообщений, и хранить её было бы хуже, чем не
    # уметь показывать переписку вовсе.

    def chat_dialogs(
        self,
        owner_id: int,
        account_id: int,
        *,
        limit: int = 40,
        offset_date: int = 0,
        offset_id: int = 0,
        offset_peer: str = "",
    ) -> dict[str, Any]:
        return self._post(
            "/chat/dialogs",
            {
                "owner_id": owner_id,
                "account_id": account_id,
                "limit": limit,
                "offset_date": offset_date,
                "offset_id": offset_id,
                "offset_peer": offset_peer,
            },
            45.0,
        )

    def chat_history(
        self,
        owner_id: int,
        account_id: int,
        peer_id: str,
        *,
        limit: int = 50,
        offset_id: int = 0,
    ) -> dict[str, Any]:
        return self._post(
            "/chat/history",
            {
                "owner_id": owner_id,
                "account_id": account_id,
                "peer_id": peer_id,
                "limit": limit,
                "offset_id": offset_id,
            },
            45.0,
        )

    def chat_send(
        self,
        owner_id: int,
        account_id: int,
        peer_id: str,
        text: str,
        *,
        reply_to: int = 0,
    ) -> dict[str, Any]:
        return self._post(
            "/chat/send",
            {
                "owner_id": owner_id,
                "account_id": account_id,
                "peer_id": peer_id,
                "text": text,
                "reply_to": reply_to,
            },
            45.0,
        )

    def chat_read(self, owner_id: int, account_id: int, peer_id: str) -> dict[str, Any]:
        return self._post(
            "/chat/read",
            {"owner_id": owner_id, "account_id": account_id, "peer_id": peer_id},
            30.0,
        )

    def chat_avatar(self, owner_id: int, account_id: int, peer_id: str) -> str:
        body = self._post(
            "/chat/avatar",
            {"owner_id": owner_id, "account_id": account_id, "peer_id": peer_id},
            45.0,
        )
        return str(body.get("image") or "")

    def chat_media(
        self, owner_id: int, account_id: int, peer_id: str, message_id: int
    ) -> str:
        body = self._post(
            "/chat/media",
            {
                "owner_id": owner_id,
                "account_id": account_id,
                "peer_id": peer_id,
                "message_id": message_id,
            },
            60.0,
        )
        return str(body.get("image") or "")


gateway = TelegramGateway()
