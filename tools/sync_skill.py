#!/usr/bin/env python3
"""Общие блоки двух инструкций скилла.

    python3 tools/sync_skill.py            # перенести общие блоки в tools/SKILL.md
    python3 tools/sync_skill.py --check    # только сверить; код 1, если разошлись

`.claude/skills/hs-edit/SKILL.md` опирается на CLAUDE.md папки, `tools/SKILL.md`
уходит в автономную сборку и обязан быть самодостаточным. Поэтому файлы
разные по замыслу, но часть разделов должна совпадать дословно: порядок
переплавки, правило про новые карты. Такие разделы помечены в источнике
`<!-- shared: id -->` … `<!-- /shared -->` и переносятся отсюда, с заменой
путей под автономную сборку. Править их руками в tools/SKILL.md нельзя:
проверка в тестах и в сборщике это заметит.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / ".claude" / "skills" / "hs-edit" / "SKILL.md"
TARGET = ROOT / "tools" / "SKILL.md"
BLOCK = re.compile(r"<!-- shared: (?P<id>[\w-]+) -->\n(?P<body>.*?)<!-- /shared -->", re.S)

# автономная сборка: скрипты лежат рядом, CLI — файлом, CLAUDE.md не вкладывается
REWRITES = [
    ("python3 .claude/skills/hs-edit/scripts/", "python3 scripts/"),
    ("editor-team ", "python3 scripts/editor_team.py "),
    ("словарь в `CLAUDE.md`", "словарь в разделе «Словарь» этого файла"),
    ("`CLAUDE.md`", "этот файл"),
]


def blocks(text: str) -> dict[str, str]:
    return {m.group("id"): m.group("body") for m in BLOCK.finditer(text)}


def render(body: str) -> str:
    for old, new in REWRITES:
        body = body.replace(old, new)
    return body


def expected() -> dict[str, str]:
    return {bid: render(body) for bid, body in blocks(SOURCE.read_text(encoding="utf-8")).items()}


def check() -> list[str]:
    """Расхождения между источником и целью; пусто — всё синхронно."""
    want = expected()
    have = blocks(TARGET.read_text(encoding="utf-8"))
    problems = []
    for bid, body in want.items():
        if bid not in have:
            problems.append(f"в tools/SKILL.md нет блока «{bid}»")
        elif have[bid] != body:
            problems.append(f"блок «{bid}» в tools/SKILL.md отстал от источника")
    for bid in have:
        if bid not in want:
            problems.append(f"блок «{bid}» есть только в tools/SKILL.md")
    return problems


def apply() -> int:
    want = expected()
    text = TARGET.read_text(encoding="utf-8")
    changed = 0

    def swap(m):
        nonlocal changed
        bid = m.group("id")
        if bid not in want:
            return m.group(0)
        if m.group("body") != want[bid]:
            changed += 1
        return f"<!-- shared: {bid} -->\n{want[bid]}<!-- /shared -->"

    new = BLOCK.sub(swap, text)
    if new != text:
        TARGET.write_text(new, encoding="utf-8")
    return changed


def main() -> int:
    ap = argparse.ArgumentParser(description="Общие блоки инструкций скилла")
    ap.add_argument("--check", action="store_true", help="только сверить")
    args = ap.parse_args()
    if args.check:
        problems = check()
        for p in problems:
            print(f"  ! {p}")
        print("общие блоки синхронны" if not problems else "запустите: python3 tools/sync_skill.py")
        return 1 if problems else 0
    n = apply()
    print(f"обновлено блоков: {n}")
    missing = [p for p in check() if "нет блока" in p or "только в" in p]
    for p in missing:
        print(f"  ! {p}")
    return 1 if missing else 0


if __name__ == "__main__":
    sys.exit(main())
