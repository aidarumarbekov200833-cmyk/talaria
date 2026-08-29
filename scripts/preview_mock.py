"""Локальный предпросмотр лендинга с заглушками API.

Нужен, чтобы посмотреть Динамику, Тариф и Финал без поднятия бэкенда.
Реальные значения берём с прода, чтобы картинка совпадала с боевой.
"""

import json
import os
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

ROOT = os.environ.get("FRONTEND_ROOT", "/work/temp/neuro_base/frontend")

# Значения с прода на момент проверки (см. /api/public/stats).
STATS_LIVE = {
    "leads": 591,
    "databases": 1,
    "campaigns": 1,
    "messages_sent": 3,
    "users": 2,
    "delivered": 3,
    "replies": 1,
    "audience": 24_800_000,
    "countries": 4,
}
STATS_EMPTY = {k: 0 for k in STATS_LIVE}

PLAN = {
    "slug": "zephyr",
    "name": "Talaria",
    "tagline": "Полный доступ к поиску, контексту и доставке",
    "features": [
        "Безлимитный поиск лидов на YouTube",
        "Карточки контекста и скоринг",
        "Персональные вступления к офферу",
        "До 5 Telegram-аккаунтов отправки",
        "Очередь, автопаузы, статус-леджер",
        "Уведомления об ответах",
    ],
    "price_rub": 5000,
    "price_rub_first": 5000,
    "price_kzt": 25000,
    "price_kzt_first": 25000,
    "ai": True,
    "ai_offer": True,
    "max_databases": -1,
    "max_offers": -1,
    "max_per_launch": -1,
    "max_warmup": -1,
    "monthly_ai_offers": -1,
    "monthly_leads": -1,
    "monthly_messages": -1,
}


# Демо-переписка для визуальной проверки раздела «Чат». Реальный Telegram в
# песочнице недоступен, а верстать ленту по пустому экрану нельзя: не видно ни
# длинных имён, ни переносов, ни того, как ведут себя пузыри рядом.
CHAT_DIALOGS = [
    {
        "peer_id": "5001",
        "kind": "user",
        "title": "Анастасия Ковальчук",
        "username": "nastya_kov",
        "can_send": True,
        "unread": 2,
        "last_message": "Да, интересно. Пришлите условия, пожалуйста",
        "last_out": False,
        "last_date": 1755950000000,
        "has_photo": False,
        "lead_id": 41,
    },
    {
        "peer_id": "5002",
        "kind": "user",
        "title": "Артём",
        "username": "artem_dev",
        "can_send": True,
        "unread": 0,
        "last_message": "Ок, до понедельника",
        "last_out": True,
        "last_date": 1755860000000,
        "has_photo": False,
        "lead_id": None,
    },
    {
        "peer_id": "-1001234567890",
        "kind": "channel",
        "title": "Талария — объявления",
        "username": "talaria_news",
        "can_send": False,
        "unread": 5,
        "last_message": "Фотография",
        "last_out": False,
        "last_date": 1755700000000,
        "has_photo": False,
        "lead_id": None,
    },
    {
        "peer_id": "-4455",
        "kind": "group",
        "title": "Проект: запуск рассылки по бьюти-нише",
        "username": "",
        "can_send": True,
        "unread": 0,
        "muted": True,
        "last_message": "Илья: посмотрю вечером",
        "last_out": False,
        "last_date": 1755600000000,
        "has_photo": False,
        "lead_id": None,
    },
]

CHAT_HISTORY = {
    "items": [
        {
            "id": 1,
            "out": True,
            "date": 1755940000000,
            "text": "Здравствуйте, Анастасия! Смотрел ваш канал — сделали бы интеграцию?",
            "media": None,
            "service": False,
        },
        {
            "id": 2,
            "out": False,
            "date": 1755944000000,
            "text": "Здравствуйте! А что за продукт?",
            "media": None,
            "service": False,
        },
        {
            "id": 3,
            "out": True,
            "date": 1755946000000,
            "text": (
                "Сервис поиска блогеров через YouTube и рассылки в Telegram. "
                "Могу прислать короткое описание и вилку по бюджету."
            ),
            "media": None,
            "service": False,
        },
        {
            "id": 4,
            "out": False,
            "date": 1755950000000,
            "text": "Да, интересно. Пришлите условия, пожалуйста",
            "media": None,
            "service": False,
        },
    ],
    "next_offset_id": 0,
}


# Сеансы и риск блокировки — для глазной проверки панелей в настройках.
SESSIONS = {
    "items": [
        {
            "jti": "cur",
            "device": "Chrome, Windows",
            "ip": "203.0.113.×",
            "created_at": "2026-08-20T09:12:00Z",
            "last_seen_at": "2026-08-24T10:40:00Z",
            "current": True,
        },
        {
            "jti": "phone",
            "device": "Safari, iPhone",
            "ip": "198.51.100.×",
            "created_at": "2026-08-11T18:02:00Z",
            "last_seen_at": "2026-08-23T21:15:00Z",
            "current": False,
        },
    ]
}

ACCOUNT_RISK = {
    "level": "danger",
    "items": [
        {
            "account_id": 2,
            "label": "Запасной",
            "level": "danger",
            "title": "Близко к блокировке",
            "reasons": [
                "Telegram ограничил переписку с незнакомыми (PEER_FLOOD)",
                "Подряд 4 ошибки отправки",
            ],
            "advice": [
                "Остановите рассылку минимум на сутки. Дальше начинайте с 5–10 сообщений в день.",
            ],
            "can_send": True,
        },
        {
            "account_id": 1,
            "label": "Основной",
            "level": "ok",
            "title": "Всё в порядке",
            "reasons": ["Ошибок и ограничений за последнее время не было"],
            "advice": ["Продолжайте в том же темпе — признаков риска нет."],
            "can_send": True,
        },
    ],
}


# Офферы: один написан человеком, второй сохранил помощник — нужно видеть,
# что второй помечен и отличается от первого.
OFFERS = {
    "offers": [
        {
            "id": 1,
            "name": "Коллаборация с каналом",
            "text": "Здравствуйте! Смотрю ваш канал и хочу предложить сотрудничество.",
            "image": "",
            "created_by": "user",
            "created_at": "2026-08-19T12:00:00Z",
        },
        {
            "id": 2,
            "name": "Письмо для тревел-блогеров",
            "text": "Добрый день! Мы работаем с авторами тревел-контента и подготовили предложение.",
            "image": "",
            "created_by": "ai",
            "created_at": "2026-08-24T09:30:00Z",
        },
    ]
}


class Handler(SimpleHTTPRequestHandler):
    def _json(self, payload):
        body = json.dumps(payload).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = self.path.split("?")[0]
        if path == "/api/public/stats":
            empty = os.environ.get("EMPTY") == "1"
            return self._json(STATS_EMPTY if empty else STATS_LIVE)
        if path == "/api/config":
            return self._json({"demo_mode": False, "bot_username": "talaria_bot"})
        if path == "/api/plans":
            return self._json({"admin_contact": "Cxentrall", "plans": [PLAN]})
        if path == "/api/offers":
            return self._json(OFFERS)
        if path == "/api/auth/sessions":
            return self._json(SESSIONS)
        if path == "/api/warmup/risk":
            return self._json(ACCOUNT_RISK)
        if path == "/api/chat/accounts":
            return self._json(
                [{"id": 1, "label": "Основной", "username": "samat", "connected": True}]
            )
        if path == "/api/chat/dialogs":
            return self._json({"items": CHAT_DIALOGS, "next": None, "account_id": 1})
        if path == "/api/chat/history":
            return self._json(CHAT_HISTORY)
        if path.startswith("/api/chat/"):
            return self._json({"image": ""})
        if path.startswith("/api/"):
            return self._json({})
        return super().do_GET()

    def do_POST(self):
        path = self.path.split("?")[0]
        if path == "/api/plans/quote":
            return self._json(
                {
                    "currency": "RUB",
                    "period_months": 1,
                    "promo_valid": False,
                    "total_amount": 5000,
                }
            )
        return self._json({})

    def log_message(self, *args):
        pass


def main():
    port = int(os.environ.get("PORT", "8903"))
    server = ThreadingHTTPServer(("127.0.0.1", port), partial(Handler, directory=ROOT))
    print(f"preview on http://127.0.0.1:{port}/index.html")
    server.serve_forever()


if __name__ == "__main__":
    main()
