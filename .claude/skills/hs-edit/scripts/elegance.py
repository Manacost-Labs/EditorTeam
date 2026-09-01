#!/usr/bin/env python3
"""Метрики аккуратности: то, чего не видят ни маркеры, ни детектор живого.

    python3 elegance.py текст.md
    python3 elegance.py текст.md --format json

Маркеры ищут плохое, soul.py — живое. Здесь три признака сухого или
машинного текста, откалиброванные по корпусу из 49 гайдов:

  * номинализации на 100 слов («-ние», «-ость», «-ация»): медиана 2,2,
    третий квартиль 2,7, максимум 3,9;
  * серии предложений с одного первого слова: у автора их нет;
  * конкретика на 100 слов (числа плюс имена собственные внутри предложения:
    карты, классы, архетипы, дополнения): минимум корпуса 11,4, медиана 14,2.

Имена считаются по заглавной букве не в начале предложения — так падежи
и короткие имена карт не теряются. Все находки — review: это адреса для
редактора, не приговор.
"""

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common as C  # noqa: E402

NOMINAL = re.compile(
    r"\b[а-яё]+(?:ние|нии|нием|ния|нию|ость|ости|остью|ация|ации|ацию|ацией|"
    r"ение|ении|ением|ения|ению)\b",
    re.I,
)
NUMBERS = re.compile(r"(?<!\w)\d+(?:[.,]\d+)?%?(?!\w)")
TOKEN = re.compile(r"[А-Яа-яЁёA-Za-z'’-]{3,}")
# «Некроситет» и «дополнение» проходят через тот же регекс номинализаций —
# это грубая, но откалиброванная по корпусу мера

# нормы по корпусу из 49 гайдов (на 100 слов / на текст)
NORMS = {
    "nominalization_per_100w": {"median": 2.2, "warn": 2.7, "fail": 4.0},
    "same_start_runs": {"median": 0, "warn": 1, "fail": 2},
    "concreteness_per_100w": {"median": 14.2, "warn": 11.0, "fail": 9.0},
}
RUN_LENGTH = 3        # три подряд с одного слова — уже тик
SHORT_ANAPHORA = 4    # рубленые фразы ≤4 слов — приём, не считаются


def proper_names(sentences):
    """Слова с заглавной не в начале предложения: карты, классы, архетипы."""
    count = 0
    for s in sentences:
        toks = TOKEN.findall(s)
        count += sum(1 for t in toks[1:] if t[0].isupper())
    return count


def measure(text):
    words = text.split()
    n = len(words)
    if not n:
        return None
    low = text.lower()
    nominal = len(NOMINAL.findall(low))
    numbers = len(NUMBERS.findall(text))
    sents = C.sentences(text)
    cards = proper_names(sents)
    starts = []
    for s in sents:
        toks = re.findall(r"[а-яёa-z]+", s.lower())
        if not toks or len(s.split()) <= SHORT_ANAPHORA:
            starts.append(None)
            continue
        starts.append(toks[0])
    runs, run_words = 0, []
    i = 0
    while i < len(starts):
        w = starts[i]
        j = i
        while w is not None and j < len(starts) and starts[j] == w:
            j += 1
        if w is not None and j - i >= RUN_LENGTH:
            runs += 1
            run_words.append((w, j - i))
        i = max(j, i + 1)

    return {
        "words": n,
        "nominalization_per_100w": round(100 * nominal / n, 2),
        "same_start_runs": runs,
        "same_start_words": run_words,
        "concreteness_per_100w": round(100 * (numbers + cards) / n, 2),
        "numbers": numbers,
        "proper_names": cards,
    }


def findings(text, m=None):
    """Находки в формате Finding: review, с подсказкой, где смотреть."""
    m = m or measure(text)
    if not m:
        return []
    out = []
    nv = m["nominalization_per_100w"]
    if nv > NORMS["nominalization_per_100w"]["warn"]:
        first = NOMINAL.search(text)
        out.append({
            "id": "elegance.nominalization", "analyzer": "elegance", "category": "elegance",
            "severity": "review", "confidence": 0.7,
            "message": f"отглагольных существительных {nv} на 100 слов при норме автора 2.2",
            "evidence": first.group(0) if first else "",
            "suggestion": "свернуть в глагол: «осуществляет добор» → «добирает»",
            "line": text.count("\n", 0, first.start()) + 1 if first else None,
            "meta": {"level": "fail" if nv > NORMS["nominalization_per_100w"]["fail"] else "warn"},
        })
    if m["same_start_runs"] >= NORMS["same_start_runs"]["warn"]:
        word, length = m["same_start_words"][0]
        out.append({
            "id": "elegance.same-start", "analyzer": "elegance", "category": "elegance",
            "severity": "review", "confidence": 0.75,
            "message": f"{length} предложения подряд начинаются с «{word}»",
            "suggestion": "у автора начала предложений разные; повтор допустим только как короткая анафора",
            "meta": {"runs": m["same_start_runs"]},
        })
    cv = m["concreteness_per_100w"]
    if m["words"] >= 150 and cv < NORMS["concreteness_per_100w"]["warn"]:
        out.append({
            "id": "elegance.abstract", "analyzer": "elegance", "category": "elegance",
            "severity": "review", "confidence": 0.65,
            "message": f"конкретики мало: {cv} чисел и имён на 100 слов при минимуме корпуса 11.4",
            "suggestion": "назвать карты, классы, ходы и числа вместо общих слов о «силе» и «темпе»",
            "meta": {"level": "fail" if cv < NORMS["concreteness_per_100w"]["fail"] else "warn"},
        })
    return out


def main():
    ap = argparse.ArgumentParser(description="Метрики аккуратности текста")
    ap.add_argument("file")
    ap.add_argument("--format", choices=["text", "json"], default="text")
    args = ap.parse_args()
    p = Path(args.file)
    if not p.exists():
        print(f"нет файла: {p}", file=sys.stderr)
        return 2
    text = p.read_text(encoding="utf-8")
    m = measure(text)
    if not m:
        print("текста нет", file=sys.stderr)
        return 2
    f = findings(text, m)
    if args.format == "json":
        print(json.dumps({"metrics": m, "findings": f}, ensure_ascii=False, indent=2))
        return 0
    print(f"\n{p.name}  ({m['words']} слов)")
    print(f"  номинализации     {m['nominalization_per_100w']:>5}  на 100 сл.   (норма 2.2, тревога >2.7)")
    print(f"  серии начал       {m['same_start_runs']:>5}               (норма 0)")
    print(f"  конкретика        {m['concreteness_per_100w']:>5}  на 100 сл.   (норма 14.2, тревога <11.0)")
    if f:
        print()
        for item in f:
            print(f"  [review] {item['message']}")
            print(f"      → {item['suggestion']}")
    else:
        print("\n  в пределах нормы автора")
    return 0


if __name__ == "__main__":
    sys.exit(main())
