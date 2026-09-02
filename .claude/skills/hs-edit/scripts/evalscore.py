#!/usr/bin/env python3
"""Оценка переплавки по набору эвалов: прошёл ли результат пороги автора.

    python3 evalscore.py tests/evals/cases/01-slop-rhetoric-bomb-warrior результат.md
    python3 evalscore.py tests/evals/cases/01-slop-rhetoric-bomb-warrior --input   # оценить сам вход

Затвор переплавки говорит «не хуже автора». Эвалы строже: они спрашивают,
дотянул ли результат до медианы корпуса, всё ли из исходника выжило и не
появилось ли того, чего в исходнике не было. Решение accept/fail здесь
делегируется rewrite_gate и claims; поверх — пороги из thresholds.yaml и
проверки, которые есть только у эвала: факты по ключевым словам, отсутствие
новых карт, отношение длин, зачин.

Идентификаторы проверок общие с затвором, чтобы `input_must_fail` в case.yaml
означал то же, что видит модель.
"""

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common as C  # noqa: E402

_VENDOR = Path(__file__).resolve().parents[1] / "vendor"
if _VENDOR.exists() and str(_VENDOR) not in sys.path:
    sys.path.insert(0, str(_VENDOR))
import yaml  # noqa: E402

EVALS_SCHEMA_VERSION = "1.0"
THRESHOLDS_PATH = C.ROOT / "tests" / "evals" / "thresholds.yaml"
NUMBERS = re.compile(r"(?<!\w)\d+(?:[.,]\d+)?%?(?!\w)")
NEGATION = re.compile(r"\b(?:не|нельзя|никогда)\b", re.I)
# сленг, который словарь автора заменяет всегда (config/terminology.yaml, auto_replace)
REPLACE_SLANG = ["дека", "деки", "деку", "деке", "статы", "статов", "сетап", "сетапа"]

# kind затвора → id проверки эвала
GATE_KINDS = {
    "voice_below_norm": "soul_low",
    "rhythm_below_norm": "rhythm_flat",
    "markers_remove_present": "markers_remove",
    "markers_above_norm": "markers_high",
    "structure_missing": "sections_missing",
    "structure_declared_missing": "sections_declared_missing",
    "structure_order": "sections_order",
    "structure_thin": "section_thin",
    "structure_wall": "wall_paragraph",
    "matchups_incomplete": "classes_coverage",
    "opening_missing": "opening",
    "voice_signal_low": "soul_signal_low",
    "text_below_min_words": "min_words",
    "elegance_nominalization": "elegance.nominalization",
    "elegance_same_start": "elegance.same_start",
    "elegance_abstract": "elegance.concreteness",
    "form_tables": "form_tables",
    "form_codes": "form_codes",
    "form_grade_labels": "form_grade_labels",
    "form_fact_repeated": "fact_repeated",
    "term_replace": "terminology",
    "lexicon_gap": "lexicon",
}


def load_case(path):
    path = Path(path)
    case_file = path / "case.yaml" if path.is_dir() else path
    case = yaml.safe_load(case_file.read_text(encoding="utf-8")) or {}
    case["_dir"] = str(case_file.parent)
    input_file = case_file.parent / "input.md"
    case["_input"] = input_file.read_text(encoding="utf-8") if input_file.exists() else ""
    return case


def load_thresholds(path=None):
    p = Path(path) if path else THRESHOLDS_PATH
    return yaml.safe_load(p.read_text(encoding="utf-8")) or {}


def thresholds_for(case, thresholds):
    base = dict(thresholds.get(case.get("profile", "constructed-guide")) or {})
    base.update(case.get("thresholds") or {})
    return base


def _lemma_set(text):
    consistency = C.sibling("consistency")
    return consistency.lemma_set(text)


def _keywords_present(keywords, sentences_lemmas):
    """Все ключевые слова (как префиксы лемм или слов) в одном предложении."""
    for lemmas in sentences_lemmas:
        joined = " ".join(sorted(lemmas))
        if all(any(l.startswith(k.lower()) for l in lemmas) or k.lower() in joined
               for k in keywords):
            return True
    return False


def _sentences_lemmas(text):
    return [_lemma_set(s) for s in C.sentences(text)]


def numbers_coverage(src, out, may_drop):
    left, right = Counter(NUMBERS.findall(src)), Counter(NUMBERS.findall(out))
    for n in may_drop or []:
        left[str(n)] -= 1
    left = +left
    missing = list((left - right).elements())
    total = sum(left.values())
    return (1.0 if not total else round(1 - len(missing) / total, 3)), missing


def cards_coverage(names, out):
    structure = C.sibling("structure")
    missing = [n for n in names if not structure.phrase_present(n, out)]
    return (1.0 if not names else round(1 - len(missing) / len(names), 3)), missing


def classes_coverage(names, out):
    claims = C.sibling("claims")
    got = set(claims._classes_in(out))
    missing = [n for n in names if n not in got]
    return (1.0 if not names else round(1 - len(missing) / len(names), 3)), missing


def negations_coverage(items, out):
    sents = C.sentences(out)
    sents_lemmas = [_lemma_set(s) for s in sents]
    lost = []
    checked = 0
    for item in items or []:
        if not item.get("must_keep", True):
            continue
        checked += 1
        keys = item.get("keywords") or []
        kept = False
        for s, lemmas in zip(sents, sents_lemmas):
            if _keywords_present(keys, [lemmas]) and NEGATION.search(s):
                kept = True
                break
        if not kept:
            lost.append(item.get("text", " ".join(keys)))
    return (1.0 if not checked else round(1 - len(lost) / checked, 3)), lost


def facts_coverage(items, out):
    sents_lemmas = _sentences_lemmas(out)
    lost = [f.get("id", "?") for f in items or []
            if not _keywords_present(f.get("keywords") or [], sents_lemmas)]
    return (1.0 if not items else round(1 - len(lost) / len(items), 3)), lost


def new_cards(src, out, allowed):
    claims = C.sibling("claims")
    src_cards = {c["name"] for c in claims.extract(src)["cards"]} | set(allowed or [])
    return sorted(c["name"] for c in claims.extract(out)["cards"] if c["name"] not in src_cards)


def score(text, case, thresholds, *, source_text=None, is_input=False):
    """Оценка одного текста по кейсу. Возвращает accepted, failed, warnings, metrics."""
    th = thresholds_for(case, thresholds)
    profile = case.get("profile", "constructed-guide")
    src = source_text if source_text is not None else case.get("_input", "")
    claims_cfg = case.get("claims") or {}
    topic = case.get("topic") or {}
    struct_cfg = case.get("structure") or {}
    declared = list(struct_cfg.get("sections_missing_in_input") or [])

    gate = C.sibling("rewrite_gate")
    soul = C.sibling("soul")
    rhythm = C.sibling("rhythm")
    markers = C.sibling("markers")
    guide_voice = C.sibling("guide_voice")

    failed, warnings = [], []
    metrics = {"words": len(text.split())}

    expected_classes = claims_cfg.get("classes") or None
    gate_v, gate_w, gate_m = gate.analyze(
        text, norms=gate.norms_for(case.get("game", "hearthstone")), profile=profile,
        declared_missing=declared, expected_classes=expected_classes,
        archetype=topic.get("archetype"), expansion=topic.get("expansion"),
    )
    for v in gate_v:
        failed.append(GATE_KINDS.get(v["kind"], v["kind"]))
    for w in gate_w:
        warnings.append(GATE_KINDS.get(w["kind"], w["kind"]))
    metrics["sections"] = {
        "present": gate_m.get("sections_present", []),
        "missing": gate_m.get("sections_missing", []),
        "order_ok": gate_m.get("sections_order_ok", True),
    }
    metrics["opening"] = gate_m.get("opening", {})
    metrics["elegance"] = gate_m.get("elegance", {})
    if th.get("sections_order_required") and not gate_m.get("sections_order_ok", True):
        failed.append("sections_order")
    walls = sum(1 for w in gate_w if w["kind"] == "structure_wall")
    thin = [w.get("signal") for w in gate_w if w["kind"] == "structure_thin"]
    metrics["walls"], metrics["thin_sections"] = walls, thin
    if th.get("wall_paragraph_max") is not None and walls > th["wall_paragraph_max"]:
        failed.append("wall_paragraph")
    if th.get("thin_sections_max") is not None and len(thin) > th["thin_sections_max"]:
        failed.append("section_thin")
    if th.get("opening_requires") and not is_input:
        op = gate_m.get("opening", {})
        for key in th["opening_requires"]:
            if topic.get(key) and op.get(key) is False:
                failed.append("opening")
                break

    # голос и ритм — пороги эвала строже затвора
    s, words = soul.measure(text)
    soul_total = round(sum(v["per1k"] for v in s.values()), 1) if s else 0.0
    metrics["soul_per_1k"] = soul_total
    if th.get("soul_per_1k_min") is not None and words >= soul.MIN_WORDS and soul_total < th["soul_per_1k_min"]:
        failed.append("soul_low")
    signal_ids = {"обращение к читателю": "soul_address_low", "императив читателю": "soul_imperative_low",
                  "уступка и поворот": "soul_contrast_low", "короткое предложение": "soul_short_low",
                  "скобка с пояснением": "soul_parenthesis_low"}
    if s and words >= soul.MIN_WORDS:
        for name, floor in (th.get("soul_signals_min") or {}).items():
            if name in s and s[name]["per1k"] < floor:
                failed.append(signal_ids.get(name, "soul_signal_low"))
    metrics["soul_signals"] = {k: round(v["per1k"], 1) for k, v in (s or {}).items()}
    r = rhythm.measure(text)
    if r:
        metrics.update({"rhythm_ratio": round(r["ratio"], 3), "short_pct": round(r["short"], 1),
                        "long_pct": round(r["long"], 1)})
        if r["n"] >= 15:
            if th.get("rhythm_ratio_min") is not None and r["ratio"] < th["rhythm_ratio_min"]:
                failed.append("rhythm_flat")
            if th.get("short_pct_min") is not None and r["short"] < th["short_pct_min"]:
                failed.append("short_pct_low")
            if th.get("long_pct_min") is not None and r["long"] < th["long_pct_min"]:
                failed.append("long_pct_low")

    # маркеры
    hits = markers.scan(text, markers.load_patterns())
    by = Counter(h["action"] for h in hits)
    per10k = 10000 * len(hits) / max(1, words)
    metrics["markers_per_10k"] = {"remove": by["remove"], "rewrite": by["rewrite"],
                                  "review": by["review"], "total": round(per10k, 1)}
    if th.get("markers_remove_max") is not None and by["remove"] > th["markers_remove_max"]:
        failed.append("markers_remove")
    if th.get("markers_total_max_per_10k") is not None and words >= soul.MIN_WORDS and per10k > th["markers_total_max_per_10k"]:
        failed.append("markers_high")

    # утечки исследовательского нарратива
    leaks = guide_voice.scan(text, case.get("editorial_mode", "GUIDE"), False)
    metrics["guide_voice_leaks"] = len(leaks)
    if th.get("guide_voice_leaks_max") is not None and len(leaks) > th["guide_voice_leaks_max"]:
        failed.append("guide_voice_leak")

    # понятность для статей
    if th.get("clarity_errors_max") is not None or th.get("thesis_required"):
        clarity = C.sibling("clarity")
        cf, cm = clarity.analyze(text, profile)
        errors = [f for f in cf if f["severity"] == "error"]
        metrics["clarity"] = {"errors": len(errors), "unlocalized": cm.get("unlocalized_game_terms", 0),
                              "thesis_problem": cm.get("thesis_problem"),
                              "thesis_consequence": cm.get("thesis_consequence")}
        if th.get("clarity_errors_max") is not None and len(errors) > th["clarity_errors_max"]:
            failed.append("clarity_errors")
        if cm.get("unlocalized_game_terms", 0) and th.get("clarity_errors_max") is not None:
            failed.append("clarity_unlocalized")
        if th.get("thesis_required") and cm.get("thesis_problem") is False:
            failed.append("thesis_missing")
        if cm.get("research_dense_paragraphs"):
            warnings.append("research_density")
        if cm.get("numeric_dense_paragraphs"):
            warnings.append("numeric_density")

    # покрытие утверждений: числа, карты, классы, отрицания, факты
    cov_th = th.get("coverage") or {}
    coverage = {}
    if src:
        cov = {}
        cov["numbers"], missing_numbers = numbers_coverage(
            src, text, claims_cfg.get("numbers_may_drop"))
        cov["cards"], missing_cards = cards_coverage(claims_cfg.get("cards") or [], text)
        cov["classes"], missing_classes = classes_coverage(claims_cfg.get("classes") or [], text)
        cov["negations"], lost_neg = negations_coverage(claims_cfg.get("negations") or [], text)
        cov["facts"], lost_facts = facts_coverage(claims_cfg.get("facts") or [], text)
        coverage = cov
        metrics["coverage_missing"] = {
            "numbers": missing_numbers, "cards": missing_cards, "classes": missing_classes,
            "negations": lost_neg, "facts": lost_facts,
        }
        if not is_input:
            for key in ("numbers", "cards", "negations", "facts"):
                if key in cov_th and cov[key] < cov_th[key]:
                    failed.append(f"coverage.{key}")
            if th.get("classes_coverage") is not None and cov["classes"] < th["classes_coverage"]:
                failed.append("classes_coverage")
            if th.get("no_new_cards"):
                added = new_cards(src, text, claims_cfg.get("cards"))
                metrics["new_cards"] = added
                if added:
                    failed.append("new_cards")
            ratio = round(len(text) / max(1, len(src)), 2)
            metrics["length_ratio"] = ratio
            lo, hi = th.get("length_ratio") or [0, 99]
            if not lo <= ratio <= hi:
                failed.append("length_ratio")
    metrics["coverage"] = coverage

    # балл соответствия — только там, где он калиброван
    if th.get("author_min") is not None and not is_input:
        author = C.sibling("author")
        tools = author.load_tools()
        total, _ = author.evaluate(text, tools, profile)
        metrics["author"] = total
        if total < th["author_min"]:
            failed.append("author_low")

    # элегантность — строгие пороги эвала
    el = th.get("elegance") or {}
    em = metrics.get("elegance") or {}
    if em:
        if el.get("nominalization_per_100w_max") is not None and em.get("nominalization_per_100w", 0) > el["nominalization_per_100w_max"]:
            failed.append("elegance.nominalization")
        if el.get("same_start_runs_max") is not None and em.get("same_start_runs", 0) > el["same_start_runs_max"]:
            failed.append("elegance.same_start")
        if el.get("concreteness_per_100w_min") is not None and words >= 150 and em.get("concreteness_per_100w", 99) < el["concreteness_per_100w_min"]:
            failed.append("elegance.concreteness")
    if th.get("words_max") is not None and words > th["words_max"]:
        failed.append("words_max")

    # терминология: английские имена, сленг из словаря замен, кавычки на картах.
    # Проверяется и на входе (это дефект слопа), и на результате.
    bad_terms = list((claims_cfg.get("expected_terms") or {}).keys())
    found_terms = [t for t in bad_terms if re.search(rf"(?<![\w-]){re.escape(t)}(?![\w-])", text, re.I)]
    found_terms += [h["found"] for h in gate.terminology_hits(text)]
    quoted_cards = [n for n in claims_cfg.get("cards") or []
                    if re.search(r"[«\"]" + re.escape(n.split()[0]), text)]
    metrics["terminology"] = {"bad_terms": found_terms, "quoted_cards": quoted_cards}
    if found_terms:
        failed.append("terminology")
    if quoted_cards:
        failed.append("typography_quotes")

    failed = sorted(set(failed))
    warnings = sorted(set(warnings) - set(failed))
    return {"evals_schema_version": EVALS_SCHEMA_VERSION, "accepted": not failed,
            "failed": failed, "warnings": warnings, "metrics": metrics}


def main():
    ap = argparse.ArgumentParser(description="Оценка результата переплавки по кейсу эвала")
    ap.add_argument("case", help="папка кейса или case.yaml")
    ap.add_argument("candidate", nargs="?", help="файл с результатом переплавки")
    ap.add_argument("--input", action="store_true", help="оценить сам вход (slop baseline)")
    ap.add_argument("--thresholds")
    ap.add_argument("--format", choices=["text", "json"], default="text")
    args = ap.parse_args()
    C.ensure_venv("pymorphy3")
    case = load_case(args.case)
    thresholds = load_thresholds(args.thresholds)
    if args.input or not args.candidate:
        result = score(case["_input"], case, thresholds, is_input=True)
        label = "вход"
    else:
        result = score(Path(args.candidate).read_text(encoding="utf-8"), case, thresholds)
        label = args.candidate
    if args.format == "json":
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"{case.get('id')}  [{label}]  {'PASS' if result['accepted'] else 'FAIL'}")
        if result["failed"]:
            print("  провалено: " + ", ".join(result["failed"]))
        if result["warnings"]:
            print("  на просмотр: " + ", ".join(result["warnings"]))
        m = result["metrics"]
        print(f"  слов {m['words']}, голос {m.get('soul_per_1k')}, ритм {m.get('rhythm_ratio')}, "
              f"маркеров {m['markers_per_10k']['total']}/10к, покрытие {m.get('coverage')}")
    return 0 if result["accepted"] else 1


if __name__ == "__main__":
    sys.exit(main())
