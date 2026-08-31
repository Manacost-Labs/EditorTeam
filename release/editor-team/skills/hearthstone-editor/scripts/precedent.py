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

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common as C  # noqa: E402


def load():
    records = C.corpus_records()
    if not records:
        print("нет approved style corpus", file=sys.stderr)
        sys.exit(2)
    return {f.stem: {"text": body, "meta": meta} for f, meta, body in records}


def find(texts, pattern):
    hits, files = [], 0
    rx = re.compile(pattern, re.I)
    for name, record in texts.items():
        t = record["text"]
        got = list(rx.finditer(t))
        if got:
            files += 1
        for m in got:
            a, b = max(0, m.start() - 55), min(len(t), m.end() + 55)
            ctx = " ".join(t[a:b].split())
            hits.append((name, ctx, m.group(0), record["meta"]))
    return hits, files


def show(word, hits, files, total_files, examples):
    print(f"\n«{word}» — {len(hits)} раз в {files} из {total_files} гайдов")
    if not hits:
        print("  в текущем корпусе не встречается")
        return
    forms = Counter(h[2].lower() for h in hits)
    if len(forms) > 1:
        print("  формы:", ", ".join(f"{f} ({c})" for f, c in forms.most_common(8)))
    if examples <= 0:  # -n 0 просили только счёт, без примеров
        return
    step = max(1, len(hits) // examples)
    print()
    for name, ctx, _, meta in hits[::step][:examples]:
        print(f"  …{ctx}…")
        print(
            f"     {name[3:60]}  {meta.get('published_at', 'unknown')}  patch {meta.get('patch', 'unknown')}"
        )
        print("     STYLE PRECEDENT — не current gameplay evidence")


def main():
    ap = argparse.ArgumentParser(description="Как автор пишет это в корпусе")
    ap.add_argument("words", nargs="+", help="слово или два для сравнения")
    ap.add_argument(
        "--словоформы",
        dest="stem",
        action="store_true",
        help="искать все формы: добавляет \\w* к концу",
    )
    ap.add_argument("-n", type=int, default=4, help="сколько примеров")
    ap.add_argument("--scope", choices=("STYLE", "FACT"), default="STYLE")
    args = ap.parse_args()

    if args.scope == "FACT":
        print(
            "CURRENT_EVIDENCE_REQUIRED: archive precedent cannot answer factual strategy queries",
            file=sys.stderr,
        )
        return 2

    texts = load()
    print("STYLE PRECEDENT — корпус показывает язык автора, а не текущую мету")
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
            state = "в корпусе не встречается" if not loser[1] else "встречается сильно реже"
            print(f"\n  В корпусе преобладает «{top[0]}»; «{loser[0]}» {state}.")
            print("  Корпус — 49 текстов, это не полный словарь автора.")
        else:
            print("\n  Оба варианта представлены в корпусе — выбирает фраза, а не правило.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
