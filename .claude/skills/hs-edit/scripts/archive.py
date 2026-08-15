#!/usr/bin/env python3
"""Сводный аудит архива: что чинить в уже опубликованном.

    python3 archive.py                — сводка по всем гайдам
    python3 archive.py --файлы        — с разбивкой по гайдам
    python3 archive.py --только карты — одна проверка

Инструменты правки работают по одному тексту. Этот прогоняет их по всему
архиву сразу и выдаёт список правок, отсортированный по надёжности находки.

Важно про корпус: тексты извлечены из PDF, поэтому часть расхождений —
следы вёрстки и переносов, а не ошибки автора. Такие находки помечены
отдельно и в итог не идут.
"""

import argparse
import importlib.util
import json
import os
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common as C  # noqa: E402

HERE, ROOT, CORPUS = C.SCRIPTS, C.ROOT, C.CORPUS
C.ensure_venv("pymorphy3")


load = C.sibling


def main():
    ap = argparse.ArgumentParser(description="Аудит всего архива")
    ap.add_argument("--файлы", dest="byfile", action="store_true")
    ap.add_argument("--только", dest="only",
                    choices=["карты", "живое", "ритм", "структура"])
    args = ap.parse_args()

    if not CORPUS.exists():
        print(f"нет корпуса: {CORPUS}", file=sys.stderr)
        return 2

    cards = load("cards")
    cards.ensure_pymorphy()
    import pymorphy3

    soul, rhythm, structure = load("soul"), load("rhythm"), load("structure")
    d = json.loads((HERE.parent / "assets" / "cards-ru.json").read_text(encoding="utf-8"))
    idx = cards.Index(d["карты"], pymorphy3.MorphAnalyzer())
    mech = set(d.get("механики", []))

    files = sorted(CORPUS.glob("*.md"))
    apo, dash, caps = Counter(), Counter(), Counter()
    artifacts = Counter()
    where = defaultdict(set)
    flat, dry, holes = [], [], []

    for f in files:
        t = f.read_text(encoding="utf-8")
        short = re.sub(r"^\d+_", "", f.stem)[:46]

        if args.only in (None, "карты"):
            for k, c in cards.check_apostrophes(t, idx).items():
                apo[k] += c
                where[("апостроф",) + k].add(short)
            for k, c in cards.check_dashes(t, d["карты"]).items():
                # перенос строки внутри имени — след PDF, а не ошибка автора
                if re.search(rf"{re.escape(k[1].split('-')[0])}-\s*\n", t):
                    artifacts[k] += c
                else:
                    dash[k] += c
                    where[("тире",) + k].add(short)
            for k, c in cards.check_caps(t, d["карты"], mech).items():
                caps[(k[0], k[1], k[2])] += c
                where[("регистр", k[0], k[1])].add(short)

        if args.only in (None, "живое"):
            s, _ = soul.measure(t)
            tot = sum(v["per1k"] for v in s.values())
            if tot < soul.TOTAL_LOW:
                dry.append((short, tot))

        if args.only in (None, "ритм"):
            r = rhythm.measure(t)
            if r and r["ratio"] < 0.45:
                flat.append((short, r["ratio"]))

        if args.only in (None, "структура"):
            got = structure.find_blocks(structure.headings(t))
            lack = [n for n, _, _, req in structure.BLOCKS if req and n not in got]
            if lack:
                holes.append((short, lack))

    print(f"\nАРХИВ: {len(files)} гайдов\n")

    def block(title, data, note=""):
        if not data:
            return
        print(f"── {title} ({sum(data.values())} в {len(data)} видах){note}")
        for k, c in data.most_common(12):
            was, off = k[0], k[1]
            mech_tail = "   ← возможно механика, не карта" if len(k) > 2 and k[2] else ""
            files_tail = ""
            if args.byfile:
                key = (title.split()[0].lower(), was, off)
                names = sorted(where.get(key, ()))[:3]
                if names:
                    files_tail = "\n      " + "; ".join(names)
            print(f"  «{was}» → «{off}»  ×{c}{mech_tail}{files_tail}")
        print()

    block("АПОСТРОФ в названии", apo, "   — чинить в первую очередь, проверка точная")
    block("ТИРЕ в названии", dash)
    block("РЕГИСТР в названии", caps)

    if artifacts:
        print(f"── следы вёрстки PDF, не ошибки автора: {sum(artifacts.values())} "
              f"в {len(artifacts)} видах (перенос строки внутри имени)\n")

    if dry:
        print(f"── СУШЕ НОРМЫ ({len(dry)} гайдов, порог {soul.TOTAL_LOW} живых сигналов)")
        for n, v in sorted(dry, key=lambda x: x[1])[:8]:
            print(f"  {v:5.1f}  {n}")
        print()

    if flat:
        print(f"── РОВНЫЙ РИТМ ({len(flat)} гайдов, порог 0.45 при норме "
              f"{rhythm.BASE['ratio']})")
        for n, v in sorted(flat, key=lambda x: x[1])[:8]:
            print(f"  {v:.2f}   {n}")
        print()

    if holes:
        print(f"── ДЫРЫ В СТРУКТУРЕ ({len(holes)} гайдов)")
        for n, lack in holes:
            print(f"  {n}\n      нет: {', '.join(lack)}")
        print()

    total = sum(apo.values()) + sum(dash.values()) + sum(caps.values())
    print("ИТОГ")
    print(f"  правок в названиях карт: {total}")
    print(f"  из них надёжных (апостроф и тире): {sum(apo.values()) + sum(dash.values())}")
    if dry or flat or holes:
        print(f"  гайдов с вопросами к тексту: "
              f"{len(set(n for n, _ in dry) | set(n for n, _ in flat) | set(n for n, _ in holes))}")
    print("\n  Правки в названиях — механические, их можно вносить списком.")
    print("  Вопросы к ритму и живому — не ошибки, а повод перечитать.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
