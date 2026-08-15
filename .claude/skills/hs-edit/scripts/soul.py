#!/usr/bin/env python3
"""Детектор живого: следит, чтобы правка не вычистила текст до стерильности.

    python3 soul.py текст.md
    python3 soul.py после.md --было до.md

Сканер маркеров ищет плохое. Этот скрипт ищет хорошее — то, чем автор
разговаривает с читателем. Нормы сняты с корпуса «гайды/» (49 текстов),
считаются на 1000 слов, чтобы работать на коротких фрагментах.

Правка, которая уменьшила живые сигналы, — плохая правка, даже если
маркеров стало меньше.
"""

import argparse
import re
import sys
from pathlib import Path

# медиана и нижний квартиль по корпусу, на 1000 слов
SIGNALS = {
    "обращение к читателю": {
        "re": r"\b(вы|вам|вас|ваш\w+)\b",
        "med": 11.2, "low": 7.1,
        "hint": "«вы найдете», «вам будет некогда» — прямой разговор с читателем",
    },
    "императив читателю": {
        "re": (r"\b(оставляйте|играйте|берите|раскапывайте|держите|ищите|помните|"
               r"учтите|обратите|старайтесь|используйте|не\s+бойтесь|не\s+спешите|"
               r"забирайте|добивайте|разменивайте|задумайтесь|рискуйте)\b"),
        "med": 5.1, "low": 3.6,
        "hint": "«оставляйте с хорошей картой» — совет глаголом, а не «стоит оставить»",
    },
    "уступка и поворот": {
        "re": r"(?:^|[.!?]\s)(Но|Однако|Зато)\s|\bхотя\b|\bзато\b",
        "med": 4.2, "low": 3.0,
        "hint": "«Но», «хотя», «зато» — автор сам себя оговаривает, это живая мысль",
    },
    "короткое предложение": {
        "re": None,
        "med": 7.6, "low": 6.0,
        "hint": "фраза короче 8 слов рядом с длинным периодом — твой обычный ритм",
    },
    "скобка с пояснением": {
        "re": r"\([^)]{12,}\)",
        "med": 1.3, "low": 0.8,
        "hint": "живое отступление в скобках",
    },
}

TOTAL_MED, TOTAL_LOW = 29.4, 20.6


def measure(text):
    words = len(text.split())
    if not words:
        return None, 0
    out = {}
    for name, cfg in SIGNALS.items():
        if cfg["re"] is None:
            sents = [s for s in re.split(r"(?<=[.!?…])\s+", text) if len(s.split()) > 1]
            hits = sum(1 for s in sents if len(s.split()) < 8)
        else:
            hits = len(re.findall(cfg["re"], text, re.I))
        out[name] = {"n": hits, "per1k": 1000 * hits / words}
    return out, words


def report(now, words, before=None, bwords=0):
    print(f"\nЖИВЫЕ СИГНАЛЫ  ({words} слов)")
    print(f"{'':<24}{'на 1000 сл.':>12}{'норма':>8}{'мин.':>7}")
    print("-" * 53)
    low_flags, lost = [], []
    for name, cfg in SIGNALS.items():
        cur = now[name]["per1k"]
        mark = ""
        if cur < cfg["low"]:
            mark = "  ниже нормы"
            low_flags.append(name)
        line = f"{name:<24}{cur:>12.1f}{cfg['med']:>8.1f}{cfg['low']:>7.1f}{mark}"
        if before:
            was = before[name]["per1k"]
            arrow = "→"
            if cur < was - 0.5:
                arrow = "↓ ПОТЕРЯ"
                lost.append((name, was, cur))
            elif cur > was + 0.5:
                arrow = "↑"
            line += f"   (было {was:.1f} {arrow})"
        print(line)

    total = sum(v["per1k"] for v in now.values())
    tline = f"\n{'ВСЕГО':<24}{total:>12.1f}{TOTAL_MED:>8.1f}{TOTAL_LOW:>7.1f}"
    if before:
        btotal = sum(v["per1k"] for v in before.values())
        tline += f"   (было {btotal:.1f})"
    print(tline)

    print("\nВЕРДИКТ")
    if lost:
        print("  ! Правка вычистила живое:")
        for name, was, cur in lost:
            print(f"      {name}: {was:.1f} → {cur:.1f} на 1000 слов")
            print(f"      {SIGNALS[name]['hint']}")
        print("    Вернуть эти места. Чистота не стоит потери голоса.")
    elif total < TOTAL_LOW:
        print(f"  ! Текст суше обычного: {total:.1f} против {TOTAL_MED} в корпусе.")
        print("    Правкой это не лечится — это вопрос к черновику.")
        for name in low_flags:
            print(f"      мало: {name} — {SIGNALS[name]['hint']}")
    else:
        print(f"  Живого достаточно: {total:.1f} на 1000 слов при норме {TOTAL_MED}.")

    if len(now) and words < 250:
        print(f"\n  Фрагмент короткий ({words} слов) — числа шумные, смотреть на порядок, не на десятые.")


def main():
    ap = argparse.ArgumentParser(description="Детектор живых сигналов")
    ap.add_argument("file")
    ap.add_argument("--было", dest="before", help="текст до правки")
    args = ap.parse_args()

    p = Path(args.file)
    if not p.exists():
        print(f"нет файла: {p}", file=sys.stderr)
        return 2
    now, words = measure(p.read_text(encoding="utf-8"))
    if not now:
        print("текста нет", file=sys.stderr)
        return 2

    before, bwords = None, 0
    if args.before and Path(args.before).exists():
        before, bwords = measure(Path(args.before).read_text(encoding="utf-8"))

    report(now, words, before, bwords)
    return 0


if __name__ == "__main__":
    sys.exit(main())
