#!/usr/bin/env python3
"""Обновление справочника карт — с честным поведением без сети.

    python3 update_cards.py            — обновить, если сеть есть
    python3 update_cards.py --статус   — только проверить свежесть

Скилл живёт в песочнице, где сети может не быть: на Claude API её нет
никогда, на claude.ai — по настройкам. Поэтому справочник карт вложен
снимком, а обновление необязательно.

Правило: если сети нет, скрипт **не притворяется**, что обновил. Он
называет дату снимка и предупреждает, что новые карты в нём отсутствуют.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common as C  # noqa: E402

ASSET = C.ASSETS / "cards-ru.json"
SOURCE = "https://api.hearthstonejson.com/v1/latest/ruRU/cards.collectible.json"
STALE_DAYS = 45  # примерно межпатчевый интервал: дольше — справочник мог отстать


def snapshot() -> dict:
    if not ASSET.exists():
        print(f"нет справочника: {ASSET}", file=sys.stderr)
        sys.exit(2)
    return json.loads(ASSET.read_text(encoding="utf-8"))


def age_days(d: dict) -> int | None:
    taken = d.get("_снято")
    if not taken:
        return None
    try:
        return (dt.date.today() - dt.date.fromisoformat(taken)).days
    except ValueError:
        return None


def fetch() -> list | None:
    """Скачать справочник. None — сети нет или источник недоступен."""
    try:
        req = urllib.request.Request(SOURCE, headers={"User-Agent": "editorteam"})
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError):
        pass
    # в некоторых песочницах доступен только curl
    if shutil.which("curl"):
        try:
            p = subprocess.run(["curl", "-sS", "-m", "30", SOURCE],
                               capture_output=True, text=True, timeout=45)
            if p.returncode == 0 and p.stdout.strip():
                return json.loads(p.stdout)
        except (subprocess.SubprocessError, json.JSONDecodeError):
            pass
    return None


def report_status(d: dict) -> int:
    age = age_days(d)
    print(f"справочник: {len(d['карты'])} карт, снят {d.get('_снято', 'неизвестно')}")
    if age is None:
        print("  возраст неизвестен — дата снимка не записана")
        return 0
    print(f"  возраст: {age} дн.")
    if age > STALE_DAYS:
        print(f"  ! Старше {STALE_DAYS} дней: карты из новых дополнений могут")
        print("    отсутствовать. Названия, которых нет в справочнике, проверка")
        print("    не подтвердит — она о них просто не знает.")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Обновление справочника карт")
    ap.add_argument("--статус", dest="status", action="store_true",
                    help="только проверить свежесть, не обновлять")
    args = ap.parse_args()

    d = snapshot()
    if args.status:
        return report_status(d)

    old_count = len(d["карты"])
    cards = fetch()
    if cards is None:
        print("Сети нет — справочник не обновлён.")
        report_status(d)
        print("\n  Это не ошибка: в песочнице Claude API сети нет никогда,")
        print("  на claude.ai она зависит от настроек. Работаем по снимку.")
        return 0

    names = sorted({c["name"] for c in cards if c.get("name")})
    added = sorted(set(names) - set(d["карты"]))
    removed = sorted(set(d["карты"]) - set(names))

    d["карты"] = names
    d["_снято"] = dt.date.today().isoformat()
    d["_источник"] = SOURCE
    ASSET.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")

    print(f"обновлено: {old_count} → {len(names)} карт")
    if added:
        print(f"  добавлено {len(added)}: {', '.join(added[:8])}"
              + (" …" if len(added) > 8 else ""))
    if removed:
        print(f"  исчезло {len(removed)}: {', '.join(removed[:5])}"
              + (" …" if len(removed) > 5 else ""))
    if not added and not removed:
        print("  состав не изменился")
    return 0


if __name__ == "__main__":
    sys.exit(main())
