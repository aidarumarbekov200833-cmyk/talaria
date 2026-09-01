#!/usr/bin/env python3
"""Сторож: сначала чинит сам, беспокоит человека только если не помогло.

Зачем отдельно от `restart: unless-stopped`. Docker перезапускает контейнер,
который **упал**. Он ничего не делает с контейнером, который жив, но завис:
процесс есть, порт слушает, а healthcheck красный. Именно так выглядит
большинство реальных зависаний, и именно их этот сторож и разбирает.

Логика на каждый проход:

1. Смотрим сквозную проверку сайта (HTTP) и состояние каждого контейнера.
2. Что-то не так -> перезапускаем только сломанный сервис и ждём, пока он
   позеленеет.
3. Помогло -> запись в лог, человеку ничего не шлём. Так и просили: если
   решилось само, беспокоить незачем.
4. Не помогло после двух попыток -> сообщение в Telegram: что сломалось, что
   уже пробовали, что делать руками.
5. Сервис чинится снова и снова (три и больше раз за час) -> тоже сообщение:
   формально сайт живой, но это не жизнь, а мигание, и человек должен знать.

Запускается системным таймером раз в две минуты. Состояние между запусками
лежит в JSON рядом со скриптом, поэтому счётчик попыток переживает перезапуск
самого сторожа.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

APP_DIR = Path(os.environ.get("ZAPP_DIR", Path.home() / "zapp"))
STATE_FILE = Path(os.environ.get("SELFHEAL_STATE", APP_DIR / ".selfheal-state.json"))
HEALTH_URL = os.environ.get("SELFHEAL_HEALTH_URL", "http://127.0.0.1:8080/api/health")

# Сколько ждём, пока сервис позеленеет после перезапуска.
RECOVER_TIMEOUT = int(os.environ.get("SELFHEAL_RECOVER_TIMEOUT", "120"))
# Сколько раз пробуем починить один инцидент, прежде чем звать человека.
MAX_ATTEMPTS = int(os.environ.get("SELFHEAL_MAX_ATTEMPTS", "2"))
# Окно и порог для «мигания».
FLAP_WINDOW = int(os.environ.get("SELFHEAL_FLAP_WINDOW", "3600"))
FLAP_THRESHOLD = int(os.environ.get("SELFHEAL_FLAP_THRESHOLD", "3"))
# Не повторять одно и то же сообщение чаще, чем раз в N секунд.
ALERT_REPEAT = int(os.environ.get("SELFHEAL_ALERT_REPEAT", "10800"))

# Что сторожим и что человеку делать, если автоматика не справилась.
SERVICES: dict[str, dict[str, str]] = {
    "backend": {
        "container": "zapp-backend-1",
        "human": "Бэкенд (API сайта)",
        "impact": "Сайт открывается, но ничего не работает: вход, поиск, рассылка.",
        "manual": "docker compose logs --tail 50 backend — смотреть последнюю ошибку перед падением.",
    },
    "frontend": {
        "container": "zapp-frontend-1",
        "human": "Фронтенд (отдача сайта)",
        "impact": "Сайт вообще не открывается.",
        "manual": "docker compose logs --tail 50 frontend; проверить nginx.conf на опечатку.",
    },
    "db": {
        "container": "zapp-db-1",
        "human": "База данных",
        "impact": "Не работает ничего, и это риск для данных.",
        "manual": "docker compose logs --tail 50 db; проверить место на диске: df -h.",
    },
    "web-tg": {
        "container": "zapp-web-tg-1",
        "human": "Сервис отправки в Telegram",
        "impact": "Сообщения лидам не уходят, остальной сайт работает.",
        "manual": "docker compose logs --tail 50 web-tg; возможно, слетела сессия аккаунта.",
    },
    "caddy": {
        "container": "zapp-caddy-1",
        "human": "Caddy (HTTPS)",
        "impact": "Сайт недоступен снаружи или отдаёт битый сертификат.",
        "manual": "docker compose logs --tail 50 caddy; проверить срок сертификата.",
    },
    "umami": {
        "container": "zapp-umami-1",
        "human": "Аналитика",
        "impact": "Не собирается статистика. На работу сайта не влияет.",
        "manual": "docker compose logs --tail 50 umami.",
        "notify": "no",  # чинить — да, будить человека — нет
    },
}


def run(args: list[str], timeout: int = 60) -> tuple[int, str]:
    try:
        p = subprocess.run(
            args, capture_output=True, text=True, timeout=timeout, cwd=str(APP_DIR)
        )
        return p.returncode, (p.stdout + p.stderr).strip()
    except subprocess.TimeoutExpired:
        return 124, "таймаут команды"
    except FileNotFoundError:
        return 127, "команда не найдена"


def container_state(name: str) -> str:
    """running-healthy | running-unhealthy | running | stopped | missing"""
    code, out = run(
        ["docker", "inspect", "--format", "{{.State.Status}}|{{if .State.Health}}{{.State.Health.Status}}{{end}}", name]
    )
    if code != 0 or not out:
        return "missing"
    status, _, health = out.partition("|")
    status = status.strip()
    health = health.strip()
    if status != "running":
        return "stopped"
    if health == "healthy":
        return "running-healthy"
    if health == "unhealthy":
        return "running-unhealthy"
    if health in ("starting", ""):
        return "running"
    return "running"


def site_reachable() -> bool:
    try:
        with urllib.request.urlopen(HEALTH_URL, timeout=10) as resp:
            return 200 <= resp.status < 400
    except Exception:  # noqa: BLE001 - любая ошибка означает «не отвечает»
        return False


def is_broken(state: str) -> bool:
    return state in ("stopped", "running-unhealthy", "missing")


@dataclass
class State:
    incidents: dict = field(default_factory=dict)
    restarts: dict = field(default_factory=dict)
    alerts: dict = field(default_factory=dict)

    @classmethod
    def load(cls) -> "State":
        try:
            raw = json.loads(STATE_FILE.read_text())
            return cls(
                incidents=raw.get("incidents", {}),
                restarts=raw.get("restarts", {}),
                alerts=raw.get("alerts", {}),
            )
        except Exception:  # noqa: BLE001 - битый или отсутствующий файл начинаем заново
            return cls()

    def save(self) -> None:
        try:
            STATE_FILE.write_text(
                json.dumps(
                    {"incidents": self.incidents, "restarts": self.restarts, "alerts": self.alerts},
                    ensure_ascii=False,
                    indent=1,
                )
            )
        except OSError as exc:
            log(f"не смог сохранить состояние: {exc}")


def log(message: str) -> None:
    print(f"[selfheal] {message}", flush=True)


def read_env(key: str) -> str:
    """Достать значение из ~/zapp/.env, не тащя зависимостей."""
    try:
        for line in (APP_DIR / ".env").read_text().splitlines():
            line = line.strip()
            if line.startswith(f"{key}="):
                return line.split("=", 1)[1].strip()
    except OSError:
        pass
    return ""


def notify(text: str, key: str, state: State) -> None:
    """Сообщение владельцу. С защитой от повторов одного и того же."""
    now = time.time()
    if now - float(state.alerts.get(key, 0)) < ALERT_REPEAT:
        log(f"сообщение «{key}» придержано (недавно уже отправляли)")
        return

    token = read_env("BOT_TOKEN")
    admins = read_env("ADMIN_TELEGRAM_IDS")
    if not token or not admins:
        log("Telegram не настроен (BOT_TOKEN / ADMIN_TELEGRAM_IDS) — только лог")
        return

    sent = False
    for admin in [a.strip() for a in admins.replace(";", ",").split(",") if a.strip()]:
        payload = urllib.parse.urlencode({"chat_id": admin, "text": text[:3900]}).encode()
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{token}/sendMessage", data=payload, method="POST"
        )
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                sent = sent or resp.status < 300
        except Exception as exc:  # noqa: BLE001 - сторож не должен падать из-за Telegram
            log(f"не смог отправить сообщение: {type(exc).__name__}")
    if sent:
        state.alerts[key] = now
        log(f"отправлено сообщение «{key}»")


def wait_recovered(container: str) -> bool:
    deadline = time.time() + RECOVER_TIMEOUT
    while time.time() < deadline:
        time.sleep(5)
        if container_state(container) == "running-healthy":
            return True
    # У сервиса может не быть healthcheck — тогда достаточно, что он просто крутится.
    return container_state(container) == "running"


def note_restart(name: str, state: State) -> int:
    now = time.time()
    history = [t for t in state.restarts.get(name, []) if now - t < FLAP_WINDOW]
    history.append(now)
    state.restarts[name] = history
    return len(history)


def handle(name: str, meta: dict, state: State) -> None:
    container = meta["container"]
    current = container_state(container)

    if not is_broken(current):
        if name in state.incidents:
            log(f"{name}: восстановился, инцидент закрыт")
            state.incidents.pop(name, None)
        return

    attempts = int(state.incidents.get(name, 0)) + 1
    state.incidents[name] = attempts
    log(f"{name}: состояние «{current}», попытка починить №{attempts}")

    code, out = run(["docker", "restart", container], timeout=120)
    if code != 0:
        log(f"{name}: перезапуск не удался: {out[:200]}")

    if wait_recovered(container):
        flaps = note_restart(name, state)
        state.incidents.pop(name, None)
        log(f"{name}: поднялся после перезапуска (перезапусков за час: {flaps})")

        if flaps >= FLAP_THRESHOLD and meta.get("notify") != "no":
            notify(
                f"🔁 {meta['human']} падает раз за разом\n\n"
                f"За последний час перезапускался {flaps} раза. Каждый раз "
                f"поднимался сам, но причина не ушла.\n\n"
                f"Чем это грозит: {meta['impact']}\n\n"
                f"Что посмотреть:\n{meta['manual']}",
                key=f"flap:{name}",
                state=state,
            )
        return

    log(f"{name}: перезапуск не помог")
    if attempts >= MAX_ATTEMPTS and meta.get("notify") != "no":
        notify(
            f"🔴 {meta['human']} не поднимается\n\n"
            f"Сервис перезапускали {attempts} раза подряд — не помогло, "
            f"он до сих пор не отвечает.\n\n"
            f"Чем это грозит: {meta['impact']}\n\n"
            f"Что сделать руками:\n{meta['manual']}\n\n"
            f"Сервер: ssh aidar@20.3.102.97, каталог ~/zapp",
            key=f"down:{name}",
            state=state,
        )


def main() -> int:
    if not APP_DIR.exists():
        log(f"каталог {APP_DIR} не найден")
        return 1

    state = State.load()
    for name, meta in SERVICES.items():
        try:
            handle(name, meta, state)
        except Exception as exc:  # noqa: BLE001 - один сервис не должен ронять обход
            log(f"{name}: сторож споткнулся: {type(exc).__name__}: {exc}")

    # Сквозная проверка: контейнеры бывают зелёными, а сайт всё равно не отвечает.
    if not site_reachable():
        log("сайт не отвечает на /api/health")
        attempts = int(state.incidents.get("__site__", 0)) + 1
        state.incidents["__site__"] = attempts
        if attempts >= MAX_ATTEMPTS:
            notify(
                "🔴 Сайт не отвечает\n\n"
                "Проверка https://talaria.mooo.com/api/health не "
                "проходит, хотя контейнеры перезапускались.\n\n"
                "Что сделать: ssh aidar@20.3.102.97, затем\n"
                "cd ~/zapp && docker compose ps && docker compose logs --tail 50",
                key="site-down",
                state=state,
            )
    else:
        state.incidents.pop("__site__", None)

    state.save()
    return 0


if __name__ == "__main__":
    sys.exit(main())
