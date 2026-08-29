"""Клиент YouTube Data API v3.

Только stdlib (urllib): ради трёх GET-запросов тянуть зависимость незачем.
Каждый вызов сначала списывает квоту в нашем счётчике (см. quota.py), потом
уходит в сеть — завышенный счётчик безопаснее заниженного.

Ошибки Google пробрасываем как YouTubeError с исходным reason: «quotaExceeded»
и «keyInvalid» требуют от пользователя разных действий, и склеивать их в общее
«ошибка запроса» — значит скрывать причину.
"""

from __future__ import annotations

import json
import logging
import time
import urllib.error
import urllib.parse
import urllib.request

logger = logging.getLogger("outly.youtube")

API_ROOT = "https://www.googleapis.com/youtube/v3"
TIMEOUT = 20
# Сетевые сбои и 5xx у Google — почти всегда мимолётные. Пара повторов с
# паузой спасает поиск, за который уже списана квота; ошибки 4xx (квота,
# ключ) повторять бессмысленно — они не «рассосутся».
RETRIES = 3
RETRY_PAUSE = 1.5


class YouTubeError(Exception):
    def __init__(self, message: str, reason: str = "", status: int = 0) -> None:
        super().__init__(message)
        self.message = message
        self.reason = reason
        self.status = status

    @property
    def is_quota(self) -> bool:
        return self.reason in {"quotaExceeded", "dailyLimitExceeded", "rateLimitExceeded"}

    @property
    def is_key_problem(self) -> bool:
        return self.reason in {
            "keyInvalid",
            "keyExpired",
            "accessNotConfigured",
            "ipRefererBlocked",
            "forbidden",
        }


def _call(endpoint: str, params: dict, key: str) -> dict:
    query = {k: v for k, v in params.items() if v not in (None, "", [])}
    query["key"] = key
    url = f"{API_ROOT}/{endpoint}?{urllib.parse.urlencode(query)}"
    last_error: YouTubeError | None = None

    for attempt in range(1, RETRIES + 1):
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            reason, message = "", ""
            try:
                body = json.loads(exc.read().decode("utf-8"))
                err = body.get("error", {})
                message = err.get("message", "")
                errors = err.get("errors") or [{}]
                reason = errors[0].get("reason", "")
            except Exception:
                pass
            last_error = YouTubeError(
                message or f"YouTube ответил HTTP {exc.code}", reason, exc.code
            )
            if exc.code < 500:
                raise last_error from exc  # квота/ключ/запрос — повтор не поможет
        except urllib.error.URLError as exc:
            last_error = YouTubeError(f"Нет связи с YouTube: {exc.reason}")
        except TimeoutError:
            last_error = YouTubeError("YouTube не ответил за отведённое время")

        if attempt < RETRIES:
            logger.warning(
                "YouTube %s: попытка %d/%d не удалась (%s) — повтор через %.1f с",
                endpoint,
                attempt,
                RETRIES,
                last_error.message,
                RETRY_PAUSE * attempt,
            )
            time.sleep(RETRY_PAUSE * attempt)

    if last_error is None:
        last_error = YouTubeError("YouTube request failed without an error response")
    raise last_error


def most_popular(key: str, region: str = "KZ", limit: int = 25) -> list[dict]:
    """Реально популярные видео региона — основа для «Популярных» тем.

    videos.list с chart=mostPopular стоит 1 единицу и отдаёт до 50 записей за
    страницу, поэтому за один вызов обходимся без пагинации.
    """
    data = _call(
        "videos",
        {
            "part": "snippet,statistics",
            "chart": "mostPopular",
            "regionCode": region or "KZ",
            "maxResults": min(50, max(1, limit)),
        },
        key,
    )

    out = []
    for item in data.get("items", []):
        snippet = item.get("snippet", {})
        stats = item.get("statistics", {})
        out.append(
            {
                "video_id": item.get("id", ""),
                "title": snippet.get("title", ""),
                "channel": snippet.get("channelTitle", ""),
                "channel_id": snippet.get("channelId", ""),
                "tags": snippet.get("tags", []) or [],
                "category_id": snippet.get("categoryId", ""),
                "views": int(stats.get("viewCount", 0) or 0),
            }
        )
    return out


def search_channels(
    key: str, query: str, page_token: str = "", per_page: int = 50, region: str = ""
) -> tuple[list[str], str]:
    """Найти id каналов по запросу. Возвращает (id, next_page_token).

    search.list стоит 100 единиц за страницу — это самая дорогая операция в
    приложении, поэтому число страниц считается заранее и показывается
    пользователю до запуска.
    """
    data = _call(
        "search",
        {
            "part": "snippet",
            "type": "channel",
            "q": query,
            "maxResults": min(50, max(1, per_page)),
            "pageToken": page_token or None,
            "regionCode": region or None,
        },
        key,
    )

    ids = []
    for item in data.get("items", []):
        cid = (item.get("id") or {}).get("channelId") or ""
        if not cid:
            # У type=channel id канала иногда лежит в snippet.
            cid = (item.get("snippet") or {}).get("channelId") or ""
        if cid:
            ids.append(cid)
    return ids, data.get("nextPageToken", "") or ""


def channels_details(key: str, channel_ids: list[str]) -> list[dict]:
    """Подробности каналов. channels.list — 1 единица, до 50 id за вызов.

    Возвращаем только то, что API действительно отдал. subscribers = None,
    когда канал скрыл счётчик: ноль там означал бы «нет подписчиков», а это
    другое утверждение.
    """
    out: list[dict] = []
    for start in range(0, len(channel_ids), 50):
        chunk = channel_ids[start : start + 50]
        data = _call(
            "channels",
            {
                "part": "snippet,statistics,brandingSettings",
                "id": ",".join(chunk),
            },
            key,
        )
        for item in data.get("items", []):
            snippet = item.get("snippet", {})
            stats = item.get("statistics", {})
            thumbs = snippet.get("thumbnails", {}) or {}
            thumb = (thumbs.get("high") or thumbs.get("medium") or thumbs.get("default") or {}).get(
                "url", ""
            )
            hidden = str(stats.get("hiddenSubscriberCount", "")).lower() == "true"
            subs = stats.get("subscriberCount")
            out.append(
                {
                    "channel_id": item.get("id", ""),
                    "title": snippet.get("title", ""),
                    "description": snippet.get("description", "") or "",
                    "custom_url": snippet.get("customUrl", "") or "",
                    "country": snippet.get("country", "") or "",
                    "thumbnail": thumb,
                    "published_at": snippet.get("publishedAt", "") or "",
                    "subscribers": None if (hidden or subs is None) else int(subs),
                    "video_count": int(stats.get("videoCount", 0) or 0),
                    "view_count": int(stats.get("viewCount", 0) or 0),
                }
            )
    return out


def channel_recent_videos(key: str, channel_id: str, limit: int = 5) -> list[dict]:
    """Последние видео канала с названиями и превью.

    Идём через uploads-плейлист, а не через search.list: search стоит 100 единиц
    за вызов, а связка channels.list + playlistItems.list — всего 2. При анализе
    десятков лидов эта разница — между двумя сотнями единиц и десятью тысячами,
    то есть между «работает» и «суточная квота сожжена за один прогон».
    """
    limit = max(1, min(20, limit))
    data = _call("channels", {"part": "contentDetails", "id": channel_id}, key)
    items = data.get("items") or []
    if not items:
        return []
    uploads = ((items[0].get("contentDetails") or {}).get("relatedPlaylists") or {}).get(
        "uploads", ""
    )
    if not uploads:
        return []

    data = _call(
        "playlistItems",
        {
            "part": "snippet",
            "playlistId": uploads,
            "maxResults": limit,
        },
        key,
    )

    out: list[dict] = []
    for item in data.get("items", []):
        snippet = item.get("snippet", {}) or {}
        thumbs = snippet.get("thumbnails", {}) or {}
        thumb = (thumbs.get("high") or thumbs.get("medium") or thumbs.get("default") or {}).get(
            "url", ""
        )
        out.append(
            {
                "title": snippet.get("title", "") or "",
                "description": (snippet.get("description", "") or "")[:400],
                "published_at": snippet.get("publishedAt", "") or "",
                "thumbnail": thumb,
            }
        )
    return out


def calls_for_channels(keywords: int, limit: int) -> tuple[int, int]:
    """Сколько страниц search.list и вызовов channels.list потребуется.

    Считаем по 50 результатов на страницу — максимум, который отдаёт API.
    """
    keywords = max(1, keywords)
    limit = max(1, limit)
    per_keyword = max(1, -(-limit // keywords))  # ceil
    pages_per_keyword = max(1, -(-per_keyword // 50))
    search_pages = pages_per_keyword * keywords
    detail_calls = max(1, -(-limit // 50))
    return search_pages, detail_calls
