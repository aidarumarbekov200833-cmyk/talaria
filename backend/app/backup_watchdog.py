"""Monitor backup freshness and alert Talaria admins through the existing bot."""

from __future__ import annotations

import json
import logging
import os
import shutil
import time
import urllib.parse
import urllib.request
from pathlib import Path

from .config import settings

logger = logging.getLogger("outly.backup_watchdog")

BACKUP_DIR = Path(os.getenv("BACKUP_DIR", "/backups"))
CHECK_SECONDS = max(60, int(os.getenv("BACKUP_CHECK_INTERVAL_SECONDS", "300")))
REPEAT_SECONDS = max(300, int(os.getenv("BACKUP_ALERT_REPEAT_SECONDS", "21600")))
MAX_DUMP_AGE = int(os.getenv("BACKUP_MAX_AGE_SECONDS", "32400"))
MAX_RESTORE_AGE = int(os.getenv("BACKUP_VERIFY_MAX_AGE_SECONDS", "691200"))
MIN_FREE_BYTES = int(os.getenv("BACKUP_MIN_FREE_BYTES", str(2 * 1024**3)))


def _marker_age(name: str, now: int) -> int | None:
    try:
        value = int((BACKUP_DIR / name).read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None
    return max(0, now - value)


def backup_issues(now: int | None = None) -> list[str]:
    """Return operational problems without reading any backup contents."""
    current = int(now if now is not None else time.time())
    issues: list[str] = []
    dump_age = _marker_age(".last-ok.epoch", current)
    restore_age = _marker_age(".last-restore-ok.epoch", current)
    if dump_age is None:
        issues.append("нет подтверждённого ежедневного дампа")
    elif dump_age > MAX_DUMP_AGE:
        issues.append(f"дамп старше {dump_age // 3600} ч")
    if restore_age is None:
        issues.append("нет успешной проверки восстановления")
    elif restore_age > MAX_RESTORE_AGE:
        issues.append(f"проверка восстановления старше {restore_age // 86400} дн")
    try:
        free = shutil.disk_usage(BACKUP_DIR).free
        if free < MIN_FREE_BYTES:
            issues.append(f"на диске осталось {free // (1024**2)} МБ")
    except OSError:
        issues.append("каталог бэкапов недоступен")
    return issues


def _send(text: str) -> None:
    if not settings.bot_token or not settings.admin_ids or not settings.telegram_alerts_enabled:
        logger.warning("Backup alert not sent: Telegram admin alerts are not configured")
        return
    url = f"https://api.telegram.org/bot{settings.bot_token}/sendMessage"
    for admin_id in settings.admin_ids:
        payload = urllib.parse.urlencode({"chat_id": str(admin_id), "text": text}).encode()
        request = urllib.request.Request(url, data=payload, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                if response.status >= 300:
                    raise RuntimeError(f"HTTP {response.status}")
        except Exception as exc:  # noqa: BLE001 - watchdog must keep running
            logger.error("Backup alert delivery failed: %s", type(exc).__name__)


def main() -> None:
    logging.basicConfig(level=settings.log_level, format="%(asctime)s %(levelname)s %(message)s")
    logger.info("Backup watchdog started: %s", json.dumps({"check_seconds": CHECK_SECONDS}))
    previous: tuple[str, ...] = ()
    last_alert = 0.0
    # Даём backup-verify закончить первое восстановление после деплоя.
    time.sleep(min(CHECK_SECONDS, 300))
    while True:
        issues = tuple(backup_issues())
        now = time.time()
        if issues and (issues != previous or now - last_alert >= REPEAT_SECONDS):
            _send("Talaria: проблема с резервными копиями\n• " + "\n• ".join(issues))
            last_alert = now
        elif previous and not issues:
            _send("Talaria: резервные копии снова проходят все проверки.")
            last_alert = now
        previous = issues
        time.sleep(CHECK_SECONDS)


if __name__ == "__main__":
    main()
