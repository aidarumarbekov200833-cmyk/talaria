"""Спинтакс для обычных офферов.

Зачем. Одинаковый текст, отправленный сотне людей подряд, — самый быстрый
способ получить спам-блок аккаунта. Спинтакс не «переписывает» текст ИИ, а
выбирает один из вариантов, которые написал сам человек: {Привет|Здравствуйте},
{хотел|хотела} предложить. Это честная вариативность без выдумывания смысла.

Поддерживаются вложенные блоки: {Привет{, {друг|коллега}}|Здравствуйте}.
Разбор рекурсивный по строке, без регулярок — регулярка вложенность не берёт.

Важно: подстановки плейсхолдеров ({channel}, {first_name}) выглядят так же,
как блоки спинтакса. Отличаем по наличию разделителя «|»: блок без «|» —
это плейсхолдер, и его надо оставить как есть, иначе спинтакс съел бы
подстановку и оффер ушёл бы без имени.
"""

from __future__ import annotations

import random
import re

TOKEN_RE = re.compile(r"^[a-z_]+$")


def _split_top_level(body: str) -> list[str]:
    """Разбить тело блока по «|», не заходя внутрь вложенных фигурных скобок."""
    parts: list[str] = []
    depth = 0
    current: list[str] = []
    for ch in body:
        if ch == "{":
            depth += 1
            current.append(ch)
        elif ch == "}":
            depth -= 1
            current.append(ch)
        elif ch == "|" and depth == 0:
            parts.append("".join(current))
            current = []
        else:
            current.append(ch)
    parts.append("".join(current))
    return parts


def _find_block(text: str, start: int) -> tuple[int, int] | None:
    """Найти первый сбалансированный блок {...} начиная с позиции start."""
    open_at = text.find("{", start)
    if open_at == -1:
        return None
    depth = 0
    for i in range(open_at, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return open_at, i
    return None  # незакрытая скобка — считаем, что блоков больше нет


def expand(text: str, rng: random.Random | None = None) -> str:
    """Развернуть спинтакс: из каждого блока выбрать один вариант.

    Блок без «|» (то есть плейсхолдер вида {channel}) остаётся нетронутым.
    """
    if not text or "{" not in text:
        return text or ""
    rnd = rng or random
    out = text
    cursor = 0
    guard = 0
    while guard < 5000:
        guard += 1
        found = _find_block(out, cursor)
        if not found:
            break
        open_at, close_at = found
        body = out[open_at + 1 : close_at]
        if "|" not in body:
            # Плейсхолдер или просто текст в скобках — пропускаем целиком.
            cursor = close_at + 1
            continue
        choice = rnd.choice(_split_top_level(body)).strip()
        out = out[:open_at] + choice + out[close_at + 1 :]
        # Курсор не двигаем: выбранный вариант мог сам содержать блоки.
    return out


def variants_count(text: str) -> int:
    """Сколько разных текстов способен дать этот шаблон.

    Нужно для интерфейса: «1 вариант» означает, что переключатель спинтакса
    включён, а вариантов в тексте нет — предупредить об этом полезнее, чем
    молча отправить сотню одинаковых сообщений.
    """
    if not text:
        return 1
    total = 1
    cursor = 0
    guard = 0
    while guard < 5000:
        guard += 1
        found = _find_block(text, cursor)
        if not found:
            break
        open_at, close_at = found
        body = text[open_at + 1 : close_at]
        cursor = close_at + 1
        if "|" not in body:
            continue
        options = _split_top_level(body)
        inner = sum(max(1, variants_count(o)) for o in options)
        total *= max(1, inner)
    return total


def has_spintax(text: str) -> bool:
    return variants_count(text) > 1
