"""Шифрование секретов, которые хранятся в БД (ключи YouTube Data API).

Зачем: дамп базы уезжает в локальные бэкапы и в B2. До этого ключи лежали
открытым текстом — утечка любого снапшота отдавала чужие оплачиваемые
Google-ключи. Теперь в колонке лежит `enc1:<fernet-token>`; сам ключ
шифрования — в окружении (DATA_ENCRYPTION_KEY), в базу не попадает.

Формат с префиксом версии, чтобы:
  * отличать уже зашифрованные значения от старых открытых (перешифровка
    при старте, см. reencrypt_legacy в services/api_keys.py);
  * менять алгоритм в будущем, не ломая чтение старых строк (enc2:...).

Fernet (AES-128-CBC + HMAC-SHA256, случайный IV) — одно и то же значение
шифруется каждый раз по-разному, поэтому искать ключ равенством по колонке
нельзя: сравнение делаем в Python после расшифровки.
"""

from __future__ import annotations

import base64
import hashlib
import logging
from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken

from .config import settings

logger = logging.getLogger("outly.secretbox")

PREFIX = "enc1:"


@lru_cache(maxsize=1)
def _fernet() -> Fernet:
    # Fernet ждёт ровно 32 байта в urlsafe-base64. Пользовательская строка
    # любой длины сводится к ним через SHA-256 — это KDF для случайного
    # секрета, не для пароля, поэтому без соли и растяжения.
    digest = hashlib.sha256(settings.data_encryption_material.encode("utf-8")).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def reset_cache() -> None:
    """Сбросить кешированный ключ (для тестов, меняющих настройки)."""
    _fernet.cache_clear()


def is_encrypted(value: str | None) -> bool:
    return bool(value) and value.startswith(PREFIX)


def encrypt(plain: str) -> str:
    if plain is None:
        return plain
    if is_encrypted(plain):
        # Значение уже из нашей колонки — второй раз не заворачиваем.
        return plain
    token = _fernet().encrypt(plain.encode("utf-8")).decode("ascii")
    return PREFIX + token


def decrypt(stored: str | None) -> str | None:
    """Расшифровать значение из БД; открытый текст (legacy) вернуть как есть.

    Неверный ключ шифрования — не повод ронять каждый запрос списка: пишем
    ошибку один раз на значение и отдаём пустую строку. Пустой ключ YouTube
    провалит проверку статусом «invalid», и владелец увидит это в кабинете,
    а в логе будет причина.
    """
    if stored is None or not is_encrypted(stored):
        return stored
    try:
        return _fernet().decrypt(stored[len(PREFIX) :].encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError):
        logger.error(
            "Не удалось расшифровать секрет из БД: DATA_ENCRYPTION_KEY не тот, "
            "которым значение шифровалось. Проверьте .env — ключ менять нельзя."
        )
        return ""
