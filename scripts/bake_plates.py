"""Гравюры-маски: по набору файлов на каждую тему.

Гравюры на сайте — это МАСКИ: в файле лежит только альфа, цвет даёт тема
(`--plate-ink`). Альфа выпечена из яркости исходника, где рисунок светился на
чёрном, поэтому у неё низкая медиана (0.19–0.40) и по всему кадру размазана
дымка 0.02–0.07. Отсюда две жалобы разом:

* «фотки тёмные» — 20–40% чернил поверх фона читаются как полумрак в обеих
  темах;
* «фотка фигово выглядит» — дымка по краю обрывается на границе бокса, и
  гравюра выглядит наклеенным прямоугольником.

Скрипт печёт из одного исходника два набора с той же геометрией:

* `<stem>-dark.*`  — для тёмной темы (розовые чернила на чёрном);
* `<stem>-light.*` — для светлой (тёмно-фиолетовые чернила на бумаге, им нужно
  больше плотности, поэтому гамма ниже).

Кривая: срезаем подложку (дымка уходит в ноль), поднимаем полутона гаммой,
у крупных гравюр дополнительно растушёвываем край косинусом — тогда рисунок
растворяется в фоне вместо прямоугольной рамки.

Запуск из корня проекта:
    python3 scripts/bake_plates.py
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
from PIL import Image

ASSETS = Path(__file__).resolve().parent.parent / "frontend" / "assets" / "talaria"

# Крупные гравюры во весь блок: у них дымка доходит до края кадра, поэтому
# край растушёвываем. Шесть карточек-перьев уже приходят с нулём по краю.
FEATHERED = {"hero-plate": 0.1, "pricing-plate": 0.09}
STEMS = (
    "hero-plate",
    "pricing-plate",
    "feather-score",
    "feather-context",
    "feather-intro",
    "feather-pace",
    "feather-ledger",
    "feather-funnel",
)
PEDESTAL = 0.05
GAMMA = {"dark": 0.62, "light": 0.5}


def lift(alpha: np.ndarray, gamma: float) -> np.ndarray:
    """Срезать подложку и поднять полутона, не трогая почти прозрачный край."""
    lifted = np.clip((alpha - PEDESTAL) / (1.0 - PEDESTAL), 0.0, 1.0)
    return lifted**gamma


def feather(shape: tuple[int, int], frac: float) -> np.ndarray:
    """Косинусный спад от края внутрь: 0 на самой кромке, 1 на отступе frac."""
    height, width = shape
    ramps = []
    for size in (height, width):
        edge = max(int(round(size * frac)), 1)
        ramp = np.ones(size, dtype=np.float32)
        window = 0.5 - 0.5 * np.cos(np.linspace(0.0, np.pi, edge, dtype=np.float32))
        ramp[:edge] = window
        ramp[-edge:] = window[::-1]
        ramps.append(ramp)
    return ramps[0][:, None] * ramps[1][None, :]


def bake(stem: str, theme: str) -> tuple[float, float]:
    src = ASSETS / f"{stem}.webp"
    image = Image.open(src).convert("RGBA")
    arr = np.asarray(image).copy()
    alpha = arr[..., 3].astype(np.float32) / 255.0
    before = float(np.median(alpha[alpha > 0.02]))
    baked = lift(alpha, GAMMA[theme])
    if stem in FEATHERED:
        baked = baked * feather(alpha.shape, FEATHERED[stem])
    arr[..., 3] = np.rint(np.clip(baked, 0.0, 1.0) * 255).astype(np.uint8)
    # Цвет пикселей маску не интересует, но пусть будет белым: так файл
    # остаётся пригодным и как обычная картинка.
    arr[..., :3] = 255
    out = Image.fromarray(arr, "RGBA")
    out.save(ASSETS / f"{stem}-{theme}.webp", "WEBP", quality=82, method=6)
    out.save(ASSETS / f"{stem}-{theme}.avif", "AVIF", quality=58)
    after_alpha = arr[..., 3][arr[..., 3] > 5] / 255.0
    return before, float(np.median(after_alpha))


def main() -> None:
    for stem in STEMS:
        line = [f"{stem}:"]
        for theme in ("dark", "light"):
            before, after = bake(stem, theme)
            line.append(f"{theme} {before:.2f} -> {after:.2f}")
        print("  ".join(line))


if __name__ == "__main__":
    main()
