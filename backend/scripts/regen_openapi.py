"""Обновить снимок OpenAPI после добавления или изменения ручек.

Снимок — договор с фронтендом: тест падает на любом расхождении, и это
правильно. Но пересобирать его руками легко неправильно, поэтому здесь один
шаг и одна страховка: если из схемы ИСЧЕЗЛА ручка, скрипт останавливается.
Пропавший путь почти всегда означает сломанный импорт роутера, а не
осознанное удаление, и записать такой снимок молча — значит узаконить поломку.

    cd backend && .venv2/bin/python scripts/regen_openapi.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.main import app  # noqa: E402
from tests.test_plan_catalog import normalize_openapi  # noqa: E402

SNAPSHOT = ROOT / "tests" / "contracts" / "openapi-contract.json"


def main() -> int:
    fresh = normalize_openapi(app.openapi())
    old = json.loads(SNAPSHOT.read_text(encoding="utf-8")) if SNAPSHOT.exists() else {}

    old_paths = set((old.get("paths") or {}).keys())
    new_paths = set((fresh.get("paths") or {}).keys())

    gone = sorted(old_paths - new_paths)
    if gone:
        print("Из схемы пропали пути — снимок не обновлён:")
        for path in gone:
            print(f"  - {path}")
        print("Проверьте, подключён ли роутер в app/main.py.")
        return 1

    added = sorted(new_paths - old_paths)
    SNAPSHOT.write_text(json.dumps(fresh, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Снимок обновлён: {SNAPSHOT.relative_to(ROOT)}")
    if added:
        print("Новые пути:")
        for path in added:
            print(f"  + {path}")
    else:
        print("Новых путей нет — изменились только описания или схемы.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
