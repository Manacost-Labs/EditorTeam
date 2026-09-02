#!/usr/bin/env python3
"""Не твои слова: доля лемм текста, которых нет в корпусе автора.

    python3 lexicon.py текст.md
    python3 lexicon.py текст.md --format json
    python3 lexicon.py --калибровка          # leave-one-out по корпусу

Голос считается по «вы», императивам и «но», но «Бриллиант», «отрезок» и
«оболочка» проходят через всё это спокойно. Здесь другая мера: сколько слов
текста автор не употребляет вовсе. Между собственными гайдами автор
расходится на 2–3% лемм (leave-one-out по корпусу); переплавка из
исследовательского PDF дала 10%.

Не считаются: названия карт из справочника, классы, слова с заглавной
внутри предложения (имена, архетипы), таблицы и коды. Список «не твоих
слов» — адреса для редактора, а не приговор: половина в нём — язык отчёта
аналитика, половина — просто чужие слова.
"""

import argparse
import json
import re
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common as C  # noqa: E402

WORD = re.compile(r"[а-яё]{4,}")
TOKEN = re.compile(r"[А-Яа-яЁёA-Za-z'’-]{2,}")
MIN_WORDS = 150          # ниже — доля шумит
WARN_PCT = 4.0           # автор между гайдами: 2–3%
FAIL_PCT = 6.0
CACHE_DIR = C.ROOT / "build"


def _lemma_set(words):
    out = set()
    for w in set(words):
        out |= C.lemmas(w)
    return out


def corpus_lexicon(exclude=None):
    """Леммы всех слов корпуса. Кэш в build/ по версии корпуса; exclude —
    stem гайда, который нужно исключить (для leave-one-out кэш не используется)."""
    records = C.corpus_records()
    if not records:
        return set()
    if exclude is None:
        version = C.corpus_manifest().get("current_version", "legacy-v1")
        cache = CACHE_DIR / f"lexicon-{version}-{len(records)}.json"
        if cache.exists():
            try:
                return set(json.loads(cache.read_text(encoding="utf-8")))
            except json.JSONDecodeError:
                pass
    words = []
    for path, _, body in records:
        if exclude is not None and path.stem == exclude:
            continue
        words.extend(WORD.findall(body.lower()))
    lex = _lemma_set(words)
    if exclude is None:
        try:
            CACHE_DIR.mkdir(exist_ok=True)
            cache.write_text(json.dumps(sorted(lex), ensure_ascii=False), encoding="utf-8")
        except OSError:
            pass
    return lex


def _skip_words():
    """Слова, которые не считаются чужими: части названий карт и классов."""
    skip = set()
    try:
        for name in C.card_db()["карты"]:
            skip.update(w.lower() for w in WORD.findall(name.lower()))
    except SystemExit:
        pass
    structure = C.sibling("structure")
    for cls in structure.CLASSES:
        skip.update(WORD.findall(cls.lower()))
    return skip


def _proper_names(text):
    """Слова с заглавной не в начале предложения: имена, архетипы, дополнения."""
    names = set()
    for s in C.sentences(text):
        toks = TOKEN.findall(s)
        names.update(t.lower() for t in toks[1:] if t[0].isupper())
    return names


def measure(text, lexicon=None):
    """{'unique', 'missing', 'ratio', 'words'} или None без корпуса/текста."""
    lexicon = corpus_lexicon() if lexicon is None else lexicon
    if not lexicon:
        return None
    prose = C.prose_only(text)
    words = WORD.findall(prose.lower())
    if not words:
        return None
    skip = _skip_words() | _proper_names(prose)
    unique = sorted(set(words))
    considered = [w for w in unique if w not in skip]
    missing = [w for w in considered if not (C.lemmas(w) & lexicon)]
    return {
        "words": len(prose.split()),
        "unique": len(considered),
        "missing": missing,
        "ratio": round(100 * len(missing) / len(considered), 1) if considered else 0.0,
    }


def findings(text, m=None):
    m = measure(text) if m is None else m
    if not m or m["words"] < MIN_WORDS:
        return []
    if m["ratio"] <= WARN_PCT:
        return []
    severity = "error" if m["ratio"] > FAIL_PCT else "review"
    shown = ", ".join(m["missing"][:12])
    return [{
        "id": "lexicon.gap", "analyzer": "lexicon", "category": "voice", "severity": severity,
        "confidence": 0.75,
        "message": f"слов, которых нет у автора: {m['ratio']}% лемм при норме 2–3% "
                   f"({len(m['missing'])} из {m['unique']}): {shown}",
        "suggestion": "заменить на слова автора или убедиться, что это термин, которого в корпусе просто не было",
        "meta": {"ratio": m["ratio"], "missing": m["missing"][:40]},
    }]


def calibrate(sample=8):
    """Leave-one-out: доля чужих слов у самого автора между гайдами."""
    records = C.corpus_records()
    if len(records) < 3:
        return None
    step = max(1, len(records) // sample)
    ratios = []
    for path, _, body in records[::step][:sample]:
        lex = corpus_lexicon(exclude=path.stem)
        m = measure(body, lexicon=lex)
        if m:
            ratios.append((m["ratio"], C.guide_name(path)[:40]))
    return ratios


def main():
    ap = argparse.ArgumentParser(description="Доля слов, которых нет у автора")
    ap.add_argument("file", nargs="?")
    ap.add_argument("--калибровка", dest="cal", action="store_true")
    ap.add_argument("--format", choices=["text", "json"], default="text")
    args = ap.parse_args()
    C.ensure_venv("pymorphy3")
    if args.cal:
        ratios = calibrate()
        if not ratios:
            print("нет корпуса", file=sys.stderr)
            return 2
        vals = [r for r, _ in ratios]
        print(f"leave-one-out по {len(ratios)} гайдам: медиана {statistics.median(vals):.1f}%, "
              f"max {max(vals):.1f}%  (предупреждение от {WARN_PCT}%, отказ от {FAIL_PCT}%)")
        for r, n in sorted(ratios):
            print(f"  {r:5.1f}%  {n}")
        return 0
    if not args.file:
        ap.error("нужен файл или --калибровка")
    p = Path(args.file)
    if not p.exists():
        print(f"нет файла: {p}", file=sys.stderr)
        return 2
    m = measure(p.read_text(encoding="utf-8"))
    if not m:
        print("нет корпуса или текста: доля не считается", file=sys.stderr)
        return 0
    f = findings(p.read_text(encoding="utf-8"), m)
    if args.format == "json":
        print(json.dumps({"metrics": m, "findings": f}, ensure_ascii=False, indent=2))
        return 0
    print(f"\n{p.name}: {m['ratio']}% лемм нет у автора ({len(m['missing'])} из {m['unique']}; "
          f"норма 2–3%, предупреждение от {WARN_PCT}%, отказ от {FAIL_PCT}%)")
    if m["missing"]:
        print("  " + ", ".join(m["missing"][:60]))
    return 1 if any(x["severity"] == "error" for x in f) else 0


if __name__ == "__main__":
    sys.exit(main())
