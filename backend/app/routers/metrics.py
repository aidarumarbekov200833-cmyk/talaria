"""Prometheus-совместимые метрики приложения.

Отдельный роутер, а не middleware-эндпоинт: так /metrics виден в общем списке
ручек и его проще прикрыть токеном или вырезать из документации.
"""

from __future__ import annotations

import secrets

from fastapi import APIRouter, Header, HTTPException, Response

from .. import metrics
from ..config import settings

router = APIRouter(tags=["metrics"])


@router.get("/metrics", include_in_schema=False)
def get_metrics(x_metrics_token: str | None = Header(default=None)) -> Response:
    """Отдать метрики в текстовом формате Prometheus.

    Доступ только по токену: METRICS_TOKEN обязан быть задан, пустое значение
    ЗАКРЫВАЕТ ручку целиком (fail closed). И при пустом токене, и при
    несовпадении отвечаем 404, а не 403: незачем подсказывать сканеру, что
    ручка вообще существует и просто требует другой токен.
    """
    expected = settings.metrics_token.strip()
    # Пустой METRICS_TOKEN раньше ОТКЛЮЧАЛ проверку, а не доступ: забыл задать
    # токен — и метрики (имена пользователей процессов нет, но объёмы и статусы
    # есть) торчали в интернет через /api/. Нет токена — ручки «нет».
    if not expected or not secrets.compare_digest(x_metrics_token or "", expected):
        raise HTTPException(404, "Not Found")
    return Response(content=metrics.render_prometheus(), media_type="text/plain; version=0.0.4")
