"""Transport-neutral exceptions raised by application services."""

from __future__ import annotations

from typing import Any


class DomainError(Exception):
    """An expected domain failure that the HTTP boundary can render."""

    def __init__(
        self,
        status_code: int,
        detail: Any,
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail
        self.headers = headers
