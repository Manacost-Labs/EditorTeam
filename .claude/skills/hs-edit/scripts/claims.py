#!/usr/bin/env python3
"""Утверждения источника и их покрытие после переплавки.

    python3 claims.py исходник.md                       # инвентаризация
    python3 claims.py исходник.md --после результат.md  # что потерялось
    python3 claims.py исходник.md --format json

При переплавке исходник — источник фактов, а не формы. Значит, проверять
надо не «сколько текста изменилось», а «все ли факты выжили». Скрипт
извлекает из источника то, что должно пережить любую перестройку:

  * многословные названия карт (однословные совпадают со случайными словами);
  * советы «оставлять / сбрасывать» по картам — из consistency.py;
  * отрицания с якорем: карта, класс или число в одном предложении с «не»;
  * числа с контекстом, коды колод, упомянутые классы, заголовки.

Покрытие: карта пропала — отказ; совет перевёрнут — отказ; отрицание снято
при том же якоре и глаголе — отказ. Всё, что распознано не до конца, —
предупреждение, а не приговор: разбирать смысл автоматически нельзя.
"""

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common as C  # noqa: E402

NEGATION = re.compile(r"\b(?:не|нельзя|никогда|ни\s+в\s+коем\s+случае)\b", re.I)
NUMBERS = re.compile(r"(?<!\w)\d+(?:[.,]\d+)?%?(?!\w)")
DECK_CODE = re.compile(r"\bAAECA\S{10,}")
ADVICE_VERB = re.compile(
    r"\b(оставля\w+|оставь\w*|оставить|держ\w+|ищ[еи]\w+|берит[еь]|бер\w+|сбрас\w+|скид\w+|"
    r"избега\w+|игра\w+|разыгрыва\w+|разгоня\w+|торгу\w+|размен\w+|став\w+|броса\w+|"
    r"спеш\w+|тратьт\w+|трат\w+|копит\w+|добива\w+|разгон\w+|форс\w+|коммит\w+)\b",
    re.I,
)
CONTEXT_WORDS = 6


def _line_of(text, pos):
    return text.count("\n", 0, pos) + 1


def _lemma(word):
    return sorted(C.lemmas(word.lower()))[0]


def _verb_lemmas(sentence):
    """Все леммы советующих глаголов: «оставляйте» → {оставлять, оставляйте}."""
    out = set()
    for v in ADVICE_VERB.findall(sentence):
        out |= set(C.lemmas(v.lower()))
    return sorted(out)


def _sentence_positions(text):
    """Предложения с позицией начала: нужны номера строк и разделы.

    Перенос строки тоже граница: иначе заголовок «Муллиган» прилипает к
    первому предложению раздела и карта попадает не в тот раздел.
    """
    out, pos = [], 0
    for s in re.split(r"(?<=[.!?…])\s+|\n+", text):
        if len(s.split()) > 1:
            at = text.find(s, pos)
            if at < 0:
                at = pos
            out.append((at, s))
            pos = at + len(s)
    return out


def _section_index(text, sections):
    structure = C.sibling("structure")
    bodies = structure.resolve_sections(text, sections)
    spans = []
    for sid, b in bodies.items():
        if b.toc_only:
            continue
        spans.append({"id": sid, "title": b.title, "line": b.heading_line,
                      "start": b.start_line, "end": b.end_line, "words": b.words})
    spans.sort(key=lambda x: x["line"])
    return spans


def _section_at(line, spans):
    for s in spans:
        if s["start"] <= line <= s["end"]:
            return s["id"]
    return None


def _classes_in(text):
    structure = C.sibling("structure")
    seen, rest = [], text
    for cls in structure.CLASSES:
        pat = structure.CLASS_PATTERNS[cls]
        if re.search(rf"\b{pat}\b", rest, re.I):
            seen.append(cls)
            rest = re.sub(rf"\b{pat}\b", " ", rest, flags=re.I)
    return seen


def _archetype(text):
    consistency = C.sibling("consistency")
    m = re.search(consistency.ARCHETYPE, text[:1500])
    if not m:
        return None
    prefix, cls = m.group(1), m.group(2)
    try:
        cls = _lemma(cls).capitalize()
    except Exception:  # noqa: BLE001
        pass
    return f"{prefix} {cls}"


_B64_LINE = re.compile(r"^[A-Za-z0-9+/=]{8,}$")
_UNIT = re.compile(r"\b(?:игр\w*|побед\w*|ман\w*|ход\w*|карт\w*|звезд\w*|уровн\w*|кристалл\w*|урон\w*|"
                   r"здоровь\w*|атак\w*|процент\w*|раз\w*|дней|сут\w*|недел\w*)\b", re.I)


def deck_codes(text):
    """Коды колод, склеенные через переносы строк: в PDF код рвётся на две.

    Строка целиком из base64 считается продолжением, если предыдущая тоже код.
    """
    out, buf = [], ""
    for raw in text.split("\n"):
        line = raw.strip()
        if buf and _B64_LINE.match(line) and not line.startswith("AAECA"):
            buf += line
            continue
        if buf:
            out.append(buf)
            buf = ""
        if line.startswith("AAECA") and _B64_LINE.match(line):
            buf = line
        elif "AAECA" in line:
            m = DECK_CODE.search(line)
            if m:
                buf = m.group(0)
    if buf:
        out.append(buf)
    return out


def fact_numbers(text):
    """Числа, которые читатель считает фактом: с классом, картой, процентом или
    единицей в том же предложении. Год в колонтитуле и номер страницы — нет.

    Ключ — (число, класс в предложении или ""), поэтому «36,1% против Друида»
    и «36,1% за 1 000 игр» — разные факты, а повторы колонтитула не считаются.
    """
    structure = C.sibling("structure")
    keys = set()
    for sentence in re.split(r"(?<=[.!?…])\s+|\n+", text):
        nums = NUMBERS.findall(sentence)
        if not nums:
            continue
        classes = [c for c in structure.CLASSES
                   if re.search(rf"\b{structure.CLASS_PATTERNS[c]}\b", sentence, re.I)]
        factual = classes or "%" in sentence or _UNIT.search(sentence)
        if not factual:
            continue
        for n in set(nums):
            if classes:
                keys.update((n, c) for c in classes)
            else:
                keys.add((n, ""))
    return keys


def extract(text, *, profile="constructed-guide"):
    """Всё, что должно пережить переплавку, с адресами в тексте."""
    consistency = C.sibling("consistency")
    structure = C.sibling("structure")
    sections = structure.load_profile_sections(profile)
    spans = _section_index(text, sections)
    sents = _sentence_positions(text)
    sent_lemmas = [(pos, s, consistency.lemma_set(s)) for pos, s in sents]

    # is_common_phrase здесь не применяется: «Мастер брони» встречается в корпусе
    # часто именно потому, что это карта, а не обычный оборот речи
    cand = consistency.card_candidates(text)
    cards = []
    for name, need in cand:
        mentions, first_line, first_pos = 0, None, None
        for pos, s, sl in sent_lemmas:
            if all(any(l in sl for l in C.lemmas(w)) for w in need):
                mentions += 1
                if first_line is None:
                    first_line, first_pos = _line_of(text, pos), pos
        if not mentions:
            continue
        cards.append({"name": name, "lemmas": sorted(need), "mentions": mentions,
                      "line": first_line, "section": _section_at(first_line, spans)})

    stances = []
    for card, buckets in consistency.advice_stances(text, cand).items():
        for stance, segs in buckets.items():
            for seg in segs:
                at = text.find(seg[:40])
                line = _line_of(text, at) if at >= 0 else None
                stances.append({"card": card, "stance": stance, "evidence": seg,
                                "line": line, "section": _section_at(line, spans) if line else None})

    negations = []
    for pos, s, sl in sent_lemmas:
        if not NEGATION.search(s):
            continue
        line = _line_of(text, pos)
        anchors = []
        for name, need in cand:
            if all(any(l in sl for l in C.lemmas(w)) for w in need):
                anchors.append(("card", name))
        for cls in _classes_in(s):
            anchors.append(("class", cls))
        for num in NUMBERS.findall(s):
            anchors.append(("number", num))
        if not anchors:
            continue
        verbs = _verb_lemmas(s)
        for kind, anchor in anchors:
            negations.append({"anchor": anchor, "anchor_kind": kind, "verb_lemmas": verbs,
                              "sentence": " ".join(s.split())[:200], "line": line,
                              "section": _section_at(line, spans)})

    numbers = []
    words_list = text.split()
    for m in NUMBERS.finditer(text):
        before = text[:m.start()].split()[-CONTEXT_WORDS:]
        after = text[m.end():].split()[:CONTEXT_WORDS]
        line = _line_of(text, m.start())
        numbers.append({"value": m.group(0), "context": " ".join(before + [m.group(0)] + after),
                        "line": line, "section": _section_at(line, spans)})

    return {
        "words": len(words_list),
        "profile": profile,
        "archetype": _archetype(text),
        "sections": spans,
        "headings": [[i + 1, h] for i, h in structure.headings(text)],
        "cards": cards,
        "stances": stances,
        "negations": negations,
        "numbers": numbers,
        "deck_codes": DECK_CODE.findall(text),
        "classes": _classes_in(text),
    }


def _card_present(need_lemmas, doc_lemmas):
    return all(any(l in doc_lemmas for l in C.lemmas(w)) for w in need_lemmas)


def _anchor_sentences(kind, anchor, sent_lemmas, structure):
    out = []
    for pos, s, sl in sent_lemmas:
        if kind == "card":
            need = [w for w in re.findall(r"[А-Яа-яЁёA-Za-z'’-]{3,}", anchor)]
            if all(any(l in sl for l in C.lemmas(w.lower())) for w in need):
                out.append(s)
        elif kind == "class":
            if re.search(rf"\b{structure.CLASS_PATTERNS[anchor]}\b", s, re.I):
                out.append(s)
        elif kind == "number":
            if anchor in NUMBERS.findall(s):
                out.append(s)
    return out


def coverage(source, after, *, declared_missing=None):
    """Что из утверждений источника не пережило переплавку.

    source — результат extract(); after — новый текст.
    Возвращает (violations, warnings, metrics).
    """
    consistency = C.sibling("consistency")
    structure = C.sibling("structure")
    guide_voice = C.sibling("guide_voice")
    declared = set(declared_missing or [])
    drop_re = re.compile(consistency.DROP, re.I)

    doc_lemmas = consistency.lemma_set(after)
    sent_lemmas = [(pos, s, consistency.lemma_set(s)) for pos, s in _sentence_positions(after)]
    violations, warnings = [], []

    # карты
    covered_cards = 0
    for card in source.get("cards", []):
        present = _card_present(card["lemmas"], doc_lemmas)
        if present:
            covered_cards += 1
            continue
        where = f", раздел «{card['section']}»" if card.get("section") else ""
        message = (f"в переплавке пропала карта «{card['name']}» "
                   f"(в исходнике {card['mentions']} упоминани{'е' if card['mentions'] == 1 else 'й'}{where})")
        if card.get("section") in declared:
            warnings.append({"kind": "claim_coverage_review", "field": "card", "claim": card["name"],
                             "message": message + " — раздел объявлен отсутствующим",
                             "severity": "review"})
        else:
            violations.append({"kind": "CLAIM_COVERAGE_LOST", "field": "card", "claim": card["name"],
                               "message": message, "severity": "error"})

    # советы по картам
    after_stances = consistency.advice_stances(after)
    src_by_card = {}
    for st in source.get("stances", []):
        src_by_card.setdefault(st["card"], set()).add(st["stance"])
    kept_stances = 0
    for card, kinds in src_by_card.items():
        got = after_stances.get(card, {"оставлять": [], "сбрасывать": []})
        got_kinds = {k for k, v in got.items() if v}
        if kinds & got_kinds:
            kept_stances += 1
            continue
        if got_kinds and not (kinds & got_kinds):
            was, now = next(iter(kinds)), next(iter(got_kinds))
            violations.append({
                "kind": "CLAIM_COVERAGE_LOST", "field": "stance", "claim": card,
                "message": f"совет перевёрнут: «{card}» — было «{was}», стало «{now}»",
                "severity": "error",
            })
        elif any(c["name"] == card for c in source.get("cards", [])) and \
                _card_present(next(c["lemmas"] for c in source["cards"] if c["name"] == card), doc_lemmas):
            warnings.append({
                "kind": "claim_coverage_review", "field": "stance", "claim": card,
                "message": f"совет по карте «{card}» не распознан после переплавки: проверить, "
                           f"что «{'/'.join(sorted(kinds))}» сохранилось",
                "severity": "review",
            })

    # отрицания с якорем
    kept_negations, total_negations = 0, 0
    seen_neg = set()
    for neg in source.get("negations", []):
        key = (neg["anchor_kind"], neg["anchor"], tuple(neg["verb_lemmas"]))
        if key in seen_neg:
            continue
        seen_neg.add(key)
        total_negations += 1
        cands = _anchor_sentences(neg["anchor_kind"], neg["anchor"], sent_lemmas, structure)
        if not cands:
            continue                                  # якорь пропал — это ловит проверка карт/классов
        any_neg = any(NEGATION.search(s) or drop_re.search(s) for s in cands)
        verbs = set(neg["verb_lemmas"])
        flipped = None
        for s in cands:
            sl = set(_verb_lemmas(s))
            if verbs and (verbs & sl) and not NEGATION.search(s) and not drop_re.search(s):
                flipped = s
                break
        if any_neg:
            kept_negations += 1
        elif flipped:
            violations.append({
                "kind": "CLAIM_COVERAGE_LOST", "field": "negation", "claim": neg["anchor"],
                "message": f"отрицание потеряно: было «{neg['sentence'][:110]}», "
                           f"стало «{' '.join(flipped.split())[:110]}»",
                "severity": "error",
            })
        else:
            warnings.append({
                "kind": "claim_coverage_review", "field": "negation", "claim": neg["anchor"],
                "message": f"отрицание про «{neg['anchor']}» не найдено после переплавки: "
                           f"было «{neg['sentence'][:110]}»",
                "severity": "review",
            })

    # классы
    after_classes = set(_classes_in(after))
    lost_classes = [c for c in source.get("classes", []) if c not in after_classes]
    for cls in lost_classes:
        warnings.append({"kind": "claim_coverage_review", "field": "class", "claim": cls,
                         "message": f"в переплавке пропал класс «{cls}» из матч-апов",
                         "severity": "review"})

    # числа только в метриках: их ловят protected_lost и number_drift
    src_numbers = Counter(n["value"] for n in source.get("numbers", []))
    after_numbers = Counter(NUMBERS.findall(after))
    numbers_kept = sum((src_numbers & after_numbers).values())

    cards_total = len(source.get("cards", []))
    metrics = {
        "cards_total": cards_total, "cards_covered": covered_cards,
        "stances_total": len(src_by_card), "stances_kept": kept_stances,
        "negations_total": total_negations, "negations_kept": kept_negations,
        "classes_total": len(source.get("classes", [])),
        "classes_kept": len(source.get("classes", [])) - len(lost_classes),
        "numbers_total": sum(src_numbers.values()), "numbers_kept": numbers_kept,
    }
    total = cards_total + len(src_by_card) + total_negations
    covered = covered_cards + kept_stances + kept_negations
    metrics["claims_total"] = total
    metrics["claims_covered"] = covered
    metrics["coverage_pct"] = round(100 * covered / total, 1) if total else 100.0
    return violations, warnings, metrics


def compare_sets(source, candidate):
    """Что появилось в кандидате, чего не было в источнике: анти-выдумывание."""
    src_cards = {c["name"] for c in source.get("cards", [])}
    cand_cards = {c["name"] for c in candidate.get("cards", [])}
    src_numbers = Counter(n["value"] for n in source.get("numbers", []))
    cand_numbers = Counter(n["value"] for n in candidate.get("numbers", []))
    return {
        "added_cards": sorted(cand_cards - src_cards),
        "added_numbers": sorted((cand_numbers - src_numbers).elements()),
        "added_classes": [c for c in candidate.get("classes", []) if c not in source.get("classes", [])],
    }


def main():
    ap = argparse.ArgumentParser(description="Утверждения источника и их покрытие")
    ap.add_argument("source")
    ap.add_argument("--после", dest="after", help="переплавленный текст")
    ap.add_argument("--profile", default="constructed-guide")
    ap.add_argument("--declared-missing", default="")
    ap.add_argument("--format", choices=["text", "json"], default="text")
    args = ap.parse_args()
    p = Path(args.source)
    if not p.exists():
        print(f"нет файла: {p}", file=sys.stderr)
        return 2
    C.ensure_venv("pymorphy3")
    source = extract(p.read_text(encoding="utf-8"), profile=args.profile)
    if not args.after:
        if args.format == "json":
            print(json.dumps(source, ensure_ascii=False, indent=2))
            return 0
        print(f"\n{p.name}: {source['words']} слов, архетип {source['archetype'] or '—'}")
        print(f"  разделов {len(source['sections'])}, карт {len(source['cards'])}, "
              f"советов {len(source['stances'])}, отрицаний {len(source['negations'])}, "
              f"чисел {len(source['numbers'])}, классов {len(source['classes'])}")
        for c in source["cards"]:
            print(f"  карта   {c['name']} ×{c['mentions']}  стр.{c['line']}")
        for n in source["negations"]:
            print(f"  «не»    [{n['anchor_kind']}] {n['anchor']}: {n['sentence'][:90]}")
        return 0
    ap_path = Path(args.after)
    if not ap_path.exists():
        print(f"нет файла: {ap_path}", file=sys.stderr)
        return 2
    declared = [x.strip() for x in args.declared_missing.split(",") if x.strip()]
    violations, warnings, metrics = coverage(
        source, ap_path.read_text(encoding="utf-8"), declared_missing=declared)
    if args.format == "json":
        print(json.dumps({"accepted": not violations, "violations": violations,
                          "warnings": warnings, "metrics": metrics}, ensure_ascii=False, indent=2))
        return 1 if violations else 0
    print("PASS" if not violations else "REJECTED")
    for item in violations:
        print(f"  [{item['kind']}:{item['field']}] {item['message']}")
    for item in warnings:
        print(f"  [REVIEW:{item['field']}] {item['message']}")
    print(f"\n  покрытие {metrics['coverage_pct']}%: карты {metrics['cards_covered']}/{metrics['cards_total']}, "
          f"советы {metrics['stances_kept']}/{metrics['stances_total']}, "
          f"отрицания {metrics['negations_kept']}/{metrics['negations_total']}, "
          f"классы {metrics['classes_kept']}/{metrics['classes_total']}")
    return 1 if violations else 0


if __name__ == "__main__":
    sys.exit(main())
