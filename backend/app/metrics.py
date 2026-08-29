"""Метрики в формате Prometheus без внешних зависимостей.

Почему не пакет prometheus-client. В requirements.txt действует правило «новая
зависимость — только по необходимости», а всё, что нужно для /metrics —
счётчик запросов по (метод, путь, код) и сумма/количество длительностей для
среднего времени ответа — укладывается в полсотни строк на stdlib. Формат
выдачи (# HELP/# TYPE + строки `name{labels} value`) совместим с обычным
Prometheus-скрейпером, поэтому подключать Grafana/Prometheus можно без правок
на их стороне.

Кардинальность лейбла `path` держим под контролем: пишем не сырой URL
(`/api/leads/123`), а шаблон роута FastAPI (`/api/leads/{lead_id}`), который
кладёт в request.scope сам роутер. Иначе за день накопились бы тысячи
уникальных путей от одних только id.
"""

from __future__ import annotations

import threading
import time

_lock = threading.Lock()
_request_counts: dict[tuple[str, str, str], int] = {}
_duration_sum: dict[tuple[str, str], float] = {}
_duration_count: dict[tuple[str, str], int] = {}
_START_TIME = time.time()

# Путь для запросов, которые не попали ни в один роут (404 на случайный URL).
# Без этой заглушки сканирование случайных путей ботами раздувало бы
# кардинальность лейбла path так же, как и сырые URL.
UNMATCHED_PATH = "unmatched"


def observe_request(
    method: str, path_template: str, status_code: int, duration_seconds: float
) -> None:
    """Учесть один завершённый HTTP-запрос. Вызывается из middleware в main.py."""
    key = (method, path_template, str(status_code))
    dkey = (method, path_template)
    with _lock:
        _request_counts[key] = _request_counts.get(key, 0) + 1
        _duration_sum[dkey] = _duration_sum.get(dkey, 0.0) + duration_seconds
        _duration_count[dkey] = _duration_count.get(dkey, 0) + 1


def _escape_label(value: str) -> str:
    """Экранировать значение лейбла по правилам формата Prometheus."""
    return value.replace("\\", "\\\\").replace('"', '\\"')


def render_prometheus() -> str:
    """Собрать текстовую выдачу /metrics (Prometheus text exposition format)."""
    lines: list[str] = []

    lines.append("# HELP outly_http_requests_total Всего HTTP-запросов")
    lines.append("# TYPE outly_http_requests_total counter")
    with _lock:
        counts_snapshot = sorted(_request_counts.items())
        sum_snapshot = sorted(_duration_sum.items())
        count_snapshot = sorted(_duration_count.items())

    for (method, path, status), count in counts_snapshot:
        lines.append(
            'outly_http_requests_total{method="%s",path="%s",status="%s"} %d'
            % (_escape_label(method), _escape_label(path), _escape_label(status), count)
        )

    lines.append("# HELP outly_http_request_duration_seconds_sum Суммарная длительность запросов")
    lines.append("# TYPE outly_http_request_duration_seconds_sum counter")
    for (method, path), total in sum_snapshot:
        lines.append(
            "outly_http_request_duration_seconds_sum"
            f'{{method="{_escape_label(method)}",path="{_escape_label(path)}"}} {total:.6f}'
        )

    lines.append(
        "# HELP outly_http_request_duration_seconds_count Число запросов с учтённой длительностью"
    )
    lines.append("# TYPE outly_http_request_duration_seconds_count counter")
    for (method, path), count in count_snapshot:
        lines.append(
            'outly_http_request_duration_seconds_count{method="%s",path="%s"} %d'
            % (_escape_label(method), _escape_label(path), count)
        )

    lines.append("# HELP outly_uptime_seconds Время с момента запуска процесса")
    lines.append("# TYPE outly_uptime_seconds gauge")
    lines.append("outly_uptime_seconds %.1f" % (time.time() - _START_TIME))

    return "\n".join(lines) + "\n"
