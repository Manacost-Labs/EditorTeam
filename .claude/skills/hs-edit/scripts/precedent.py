#!/usr/bin/env python3
"""Прецедент: как автор пишет это на самом деле.

    python3 precedent.py "зачастую"              — частота и живые примеры
    python3 precedent.py "зачастую" "обычно"     — сравнить варианты
    python3 precedent.py --словоформы винрейт    — все формы слова

Вместо «мне кажется, автор написал бы так» — посмотреть, как он писал.
Любое решение о стиле должно опираться на корпус, а не на вкус редактора.
"""

import argparse
import re
import sys
from collections import Counter
from pathlib import Path

# scripts -> hs-edit -> skills -> .claude -> корень проекта
CORPUS = Path(__file__).resolve().parents[4] / "гайды"


def load():
    if not CORPUS.exists():
        print(f"нет корпуса: {CORPUS}", file=sys.stderr)
        sys.exit(2)
    return {f.stem: f.read_text(encoding="utf-8") for f in CORPUS.glob("*.md")}


def find(texts, pattern):
    hits, files = [], 0
    rx = re.compile(pattern, re.I)
    for name, t in texts.items():
        got = list(rx.finditer(t))
        if got:
            files += 1
        for m in got:
            a, b = max(0, m.start() - 55), min(len(t), m.end() + 55)
            ctx = " ".join(t[a:b].split())
            hits.append((name, ctx, m.group(0)))
    return hits, files


def show(word, hits, files, total_files, examples):
    print(f"\n«{word}» — {len(hits)} раз в {files} из {total_files} гайдов")
    if not hits:
        print("  в корпусе не встречается — это не твоё слово")
        return
    forms = Counter(h[2].lower() for h in hits)
    if len(forms) > 1:
        print("  формы:", ", ".join(f"{f} ({c})" for f, c in forms.most_common(8)))
    step = max(1, len(hits) // examples)
    print()
    for name, ctx, _ in hits[::step][:examples]:
        print(f"  …{ctx}…")
        print(f"     {name[3:60]}")


def main():
    ap = argparse.ArgumentParser(description="Как автор пишет это в корпусе")
    ap.add_argument("words", nargs="+", help="слово или два для сравнения")
    ap.add_argument("--словоформы", dest="stem", action="store_true",
                    help="искать все формы: добавляет \\w* к концу")
    ap.add_argument("-n", type=int, default=4, help="сколько примеров")
    args = ap.parse_args()

    texts = load()
    results = []
    for w in args.words:
        pat = rf"\b{re.escape(w)}\w*" if args.stem else rf"\b{re.escape(w)}\b"
        hits, files = find(texts, pat)
        results.append((w, hits, files))
        show(w, hits, files, len(texts), args.n)

    if len(results) > 1:
        print("\nСРАВНЕНИЕ")
        results.sort(key=lambda r: -len(r[1]))
        top = results[0]
        for w, hits, _ in results:
            print(f"  {w:<20}{len(hits):>6}")
        loser = results[-1]
        if len(top[1]) > len(loser[1]) * 3 and len(top[1]) > 5:
            print(f"\n  Твоё слово — «{top[0]}». «{loser[0]}» "
                  f"{'не встречается вовсе' if not loser[1] else 'сильно реже'}.")
        else:
            print("\n  Оба варианта твои — выбирает фраза, а не правило.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
