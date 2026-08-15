#!/usr/bin/env python3
"""Замер ритма: сверка текста с эталоном автора из ГОЛОС.md.

    python3 rhythm.py текст.md
    python3 rhythm.py черновик.md --было исходник.md

Второй режим — главный: показывает, не выровняла ли правка ритм.
"""

import argparse
import re
import statistics
import sys
from pathlib import Path

# эталон снят с корпуса из 49 гайдов (гайды/)
BASE = {"mean": 14.9, "sd": 7.6, "ratio": 0.51, "short": 12, "long": 7, "para": 2.2}
ALARM = 0.45  # ниже — текст выровнен


def measure(text):
    sents = [len(s.split()) for s in re.split(r"(?<=[.!?…])\s+", text) if len(s.split()) > 1]
    if not sents:
        return None
    paras = [len([x for x in re.split(r"(?<=[.!?…])\s+", p) if x.strip()])
             for p in text.split("\n") if len(p.split()) > 12]
    mean = statistics.mean(sents)
    sd = statistics.pstdev(sents)
    return {
        "n": len(sents),
        "mean": mean,
        "median": statistics.median(sents),
        "sd": sd,
        "ratio": sd / mean if mean else 0,
        "short": 100 * sum(1 for x in sents if x < 8) / len(sents),
        "long": 100 * sum(1 for x in sents if x > 25) / len(sents),
        "max": max(sents),
        "para": statistics.mean(paras) if paras else 0,
    }


def show(m, title):
    print(f"\n{title}")
    print(f"  предложений      {m['n']}")
    print(f"  среднее          {m['mean']:.1f} сл.   (эталон {BASE['mean']})")
    print(f"  медиана          {m['median']:.0f} сл.")
    print(f"  разброс          {m['sd']:.1f}      (эталон {BASE['sd']})")
    print(f"  разброс/среднее  {m['ratio']:.2f}     (эталон {BASE['ratio']}, тревога <{ALARM})")
    print(f"  коротких <8 сл.  {m['short']:.0f}%       (эталон {BASE['short']}%)")
    print(f"  длинных >25 сл.  {m['long']:.0f}%       (эталон {BASE['long']}%)")
    print(f"  самое длинное    {m['max']} сл.")
    print(f"  абзац            {m['para']:.1f} предл. (эталон {BASE['para']})")


def verdict(m, before=None):
    print()
    if before and m["ratio"] < before["ratio"] - 0.03:
        print(f"! Правка выровняла ритм: {before['ratio']:.2f} → {m['ratio']:.2f}.")
        print("  Найти склеенные короткие фразы и разрубленные длинные периоды — вернуть как было.")
    elif m["ratio"] < ALARM:
        print(f"! Разброс {m['ratio']:.2f} ниже порога {ALARM}. Текст читается ровно и мёртво.")
        print("  Проверить, не подогнаны ли предложения под одну длину.")
    else:
        print(f"Ритм в норме: {m['ratio']:.2f}.")

    if m["short"] < BASE["short"] - 5:
        print(f"! Коротких предложений {m['short']:.0f}% против {BASE['short']}% в корпусе — крайности срезаны.")
    if m["para"] > BASE["para"] + 1.5:
        print(f"! Абзац {m['para']:.1f} предложения против {BASE['para']} в корпусе — плотнее обычного.")


def main():
    ap = argparse.ArgumentParser(description="Замер ритма относительно эталона автора")
    ap.add_argument("file")
    ap.add_argument("--было", dest="before", help="исходник до правки — для сравнения")
    args = ap.parse_args()

    path = Path(args.file)
    if not path.exists():
        print(f"нет файла: {path}", file=sys.stderr)
        return 2

    m = measure(path.read_text(encoding="utf-8"))
    if not m:
        print("текста мало для замера", file=sys.stderr)
        return 2

    before = None
    if args.before:
        bp = Path(args.before)
        if bp.exists():
            before = measure(bp.read_text(encoding="utf-8"))
            show(before, f"ДО — {bp.name}")

    show(m, f"{'ПОСЛЕ — ' if before else ''}{path.name}")
    verdict(m, before)
    return 0


if __name__ == "__main__":
    sys.exit(main())
