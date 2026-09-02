#!/usr/bin/env python3
"""Затвор переплавки: результат против нормы автора, а не против исходника.

    python3 rewrite_gate.py результат.md --profile constructed-guide
    python3 rewrite_gate.py результат.md --profile constructed-guide --declared-missing matchups
    python3 rewrite_gate.py результат.md --format json

Обычный затвор сравнивает «после» с «до»: не потерян ли голос, не усох ли
текст. Для плохого исходника это бессмысленно — у слопа нечего терять. Здесь
точка отсчёта другая: нормы, снятые с 49 опубликованных гайдов.

  * живой голос не ниже нижней границы корпуса (20,6 на 1000 слов);
  * ритм не ниже минимума корпуса (0,40), тревога ниже 0,45;
  * маркеров шаблона не больше максимума корпуса (60 на 10 000 слов), от нормы
    12,2 — предупреждение; «убрать»-маркеров не больше двух;
  * обязательные разделы профиля на месте, кроме честно объявленных
    отсутствующими; порядок, тонкие разделы и полотна — на просмотр;
  * охват классов и зачин — относительно исходника, не константы;
  * форма подачи как у автора: не больше одной таблицы и четырёх кодов,
    без оценочных букв провайдера, каждая цифра — один раз.

Пороги откалиброваны так, чтобы опубликованные гайды затвор не отвергал:
это проверяет selftest.py.
"""

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common as C  # noqa: E402

_VENDOR = Path(__file__).resolve().parents[1] / "vendor"
if _VENDOR.exists() and str(_VENDOR) not in sys.path:
    sys.path.insert(0, str(_VENDOR))

DEPTHS = ("лёгкая", "обычная", "глубокая", "переплавка")
DEFAULT_DEPTH = "обычная"
REWRITE = "переплавка"

# Пороги отказа стоят за краем корпуса: то, чего у автора не бывает вовсе.
# Предупреждения начинаются от нормы. Замеры по 49 гайдам: ритм min 0.419,
# «убрать»-маркеров max 2 на гайд, маркеров всего max 56.3 на 10 000 слов.
RHYTHM_FLOOR = 0.40          # ниже минимума корпуса — точно выровнено
MARKERS_REMOVE_MAX = 2       # до двух «убрать»-маркеров — предупреждение, три — отказ
MARKERS_HARD_PER_10K = 60.0  # выше максимума корпуса — отказ; от нормы (12.2) — предупреждение
MARKERS_MIN_WORDS = 300      # частота маркеров на 10к слов достоверна от этого объёма
MIN_RHYTHM_SENTENCES = 15

# форма подачи: в корпусе автора 0 таблиц, 0 кодов колод в тексте, ни один процент
# не повторяется в трёх абзацах. Пороги берутся из профиля (form:), здесь — запас
DEFAULT_FORM = {"tables_max": 1, "codes_max": 4, "repeated_facts_max": 0, "grade_labels": "forbidden"}
DECK_CODE = re.compile(r"\bAAECA\S{10,}")
FACT = re.compile(r"(?<!\w)\d+(?:[.,]\d+)?%(?!\w)")            # проценты — то, что читатель запомнит
GRADE_LABEL = re.compile(r"(?m)^\s*Положение:\s*[SABCD]|\b[SABCD][+−-]?/[SABCD][+−-]?\b|\bтир\s+[SABCD]\b")

DEFAULT_NORMS = {
    "voice_low": 20.6, "voice_per_1k": 29.4,
    "rhythm_alarm": 0.45, "rhythm_ratio": 0.51,
    "markers_per_10k": 12.2, "provisional": False,
}


def normalize_depth(value):
    """«Переплавка», «легкая:», «ЛЁГКАЯ» → каноническое слово; иное — ValueError."""
    raw = (value or DEFAULT_DEPTH).strip().lower().strip(" \t:.—–-")
    folded = raw.replace("ё", "е")
    for depth in DEPTHS:
        if folded == depth.replace("ё", "е"):
            return depth
    raise ValueError(f"edit depth must be one of {', '.join(DEPTHS)}")


def norms_for(game="hearthstone"):
    """Нормы игры из config/games/<game>.yaml; без YAML — нормы Hearthstone."""
    try:
        import yaml
    except ImportError:
        return dict(DEFAULT_NORMS)
    path = C.ROOT / "config" / "games" / f"{game}.yaml"
    if not path.exists():
        return dict(DEFAULT_NORMS)
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    norms = dict(DEFAULT_NORMS)
    for key in norms:
        if key in (data.get("norms") or {}):
            norms[key] = data["norms"][key]
    return norms


def _fact_keys(para):
    """Процент вместе с классом из того же предложения: 52,6% у Жреца и 52,6% у
    Чернокнижника — разные факты, а не повтор. Без класса ключ — сам процент."""
    structure = C.sibling("structure")
    keys = []
    for sentence in re.split(r"(?<=[.!?…])\s+|\n+", para):
        facts = FACT.findall(sentence)
        if not facts:
            continue
        classes = [c for c in structure.CLASSES
                   if re.search(rf"\b{structure.CLASS_PATTERNS[c]}\b", sentence, re.I)]
        for fact in set(facts):
            if classes:
                keys.extend(f"{fact} {c.lower()}" for c in classes)
            else:
                keys.append(fact)
    return keys


def form_metrics(text):
    """Таблицы, коды колод, оценочные буквы и проценты, повторённые по абзацам."""
    tables, in_table = 0, False
    for line in text.splitlines():
        row = line.strip().startswith("|") and line.count("|") >= 3
        if row and not in_table:
            tables += 1
        in_table = row
    # свои абзацы, без схлопывания переносов: строки таблицы должны остаться
    # отдельными предложениями, иначе все классы таблицы приклеятся к каждому проценту
    paras = [p for p in re.split(r"\n\s*\n|\n(?=[А-ЯЁ])", text) if len(p.split()) >= 6]
    where = {}
    for i, para in enumerate(paras):
        for key in set(_fact_keys(para)):
            where.setdefault(key, []).append(i + 1)
    repeated = {fact: idx for fact, idx in where.items() if len(idx) >= 2}
    return {
        "tables": tables,
        "codes": len(DECK_CODE.findall(text)),
        "grade_labels": [m.group(0).strip() for m in GRADE_LABEL.finditer(text)],
        "repeated_facts": repeated,
    }


def form_checks(text, form):
    """(violations, warnings, metrics) по форме подачи из профиля."""
    form = {**DEFAULT_FORM, **(form or {})}
    fm = form_metrics(text)
    violations, warnings = [], []
    if form.get("tables_max") is not None and fm["tables"] > form["tables_max"]:
        violations.append(_item(
            "form_tables",
            f"таблиц {fm['tables']} при допустимом {form['tables_max']}: у автора данные объясняются прозой",
            suggestion="оставить одну сводную таблицу, остальное рассказать словами",
        ))
    if form.get("codes_max") is not None and fm["codes"] > form["codes_max"]:
        violations.append(_item(
            "form_codes",
            f"кодов колод {fm['codes']} при допустимом {form['codes_max']}: это каталог, а не гайд",
            suggestion="коды только для рекомендуемых сборок; остальные назвать без кода",
        ))
    if form.get("grade_labels") == "forbidden" and fm["grade_labels"]:
        shown = ", ".join(f"«{g}»" for g in fm["grade_labels"][:3])
        violations.append(_item(
            "form_grade_labels",
            f"оценочные буквы провайдера: {shown}",
            suggestion="сказать словами, где колода стоит и когда её брать",
        ))
    max_rep = form.get("repeated_facts_max")
    hard = {f: idx for f, idx in fm["repeated_facts"].items() if len(idx) >= 3}
    soft = {f: idx for f, idx in fm["repeated_facts"].items() if len(idx) == 2}
    if max_rep is not None and len(hard) > max_rep:
        shown = "; ".join(f"{f} в абзацах {', '.join(map(str, idx))}" for f, idx in list(hard.items())[:3])
        violations.append(_item(
            "form_fact_repeated",
            f"одна и та же цифра звучит в трёх и больше абзацах: {shown}",
            suggestion="назвать цифру один раз там, где читатель принимает решение; дальше ссылаться на вывод",
        ))
    for f, idx in list(soft.items())[:5]:
        warnings.append(_item(
            "form_fact_repeated",
            f"цифра {f} повторяется в абзацах {idx[0]} и {idx[1]}",
            "review",
        ))
    return violations, warnings, fm


_TOKEN = re.compile(r"[А-Яа-яЁёA-Za-z][А-Яа-яЁёA-Za-z'’-]+")


def terminology_rules():
    """Правила словаря с решением auto_replace или forbidden: слово → на что менять."""
    try:
        import yaml
    except ImportError:
        return []
    path = C.ROOT / "config" / "terminology.yaml"
    if not path.exists():
        return []
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    out = []
    for r in data.get("rules") or []:
        if r.get("decision") not in ("auto_replace", "forbidden") or not r.get("preferred"):
            continue
        subject = r.get("slang") or r.get("word")
        if subject:
            out.append({"id": r["id"], "subject": subject, "preferred": r["preferred"],
                        "pattern": r.get("pattern"), "case_sensitive": bool(r.get("case_sensitive"))})
    return out


def terminology_hits(text):
    """Слова словаря замен в тексте: по леммам для одного слова, по образцу для фразы.

    «Бриллианте» находит правило «Бриллиант», «деки» — «дека». Названия карт и
    коды не маскируются: словарь и так не пересекается с локализацией.
    """
    rules = terminology_rules()
    if not rules:
        return []
    hits = []
    tokens = None
    for rule in rules:
        if rule.get("pattern"):
            flags = 0 if rule.get("case_sensitive") else re.I
            for m in re.finditer(rule["pattern"], text, flags):
                hits.append({"rule": rule["id"], "found": m.group(0), "preferred": rule["preferred"],
                             "line": text.count("\n", 0, m.start()) + 1})
            continue
        if tokens is None:
            tokens = [(m.group(0), m.start()) for m in _TOKEN.finditer(text)]
        try:
            want = C.lemmas(rule["subject"].lower())
        except Exception:  # noqa: BLE001 — без морфологии сравниваем строчные
            want = {rule["subject"].lower()}
        for tok, pos in tokens:
            low = tok.lower()
            try:
                same = bool(C.lemmas(low) & want)
            except Exception:  # noqa: BLE001
                same = low == rule["subject"].lower()
            if same:
                hits.append({"rule": rule["id"], "found": tok, "preferred": rule["preferred"],
                             "line": text.count("\n", 0, pos) + 1})
    return hits


def _item(kind, message, severity="error", **extra):
    out = {"kind": kind, "message": message, "severity": severity}
    out.update({k: v for k, v in extra.items() if v not in (None, "", [])})
    return out


def analyze(after, *, norms=None, profile="constructed-guide", declared_missing=None,
            expected_classes=None, archetype=None, expansion=None):
    """(violations, warnings, metrics) для переплавленного текста."""
    norms = {**DEFAULT_NORMS, **(norms or {})}
    declared = set(declared_missing or [])
    soul = C.sibling("soul")
    rhythm = C.sibling("rhythm")
    markers = C.sibling("markers")
    structure = C.sibling("structure")
    elegance = C.sibling("elegance")

    violations, warnings = [], []
    prose = C.prose_only(after)
    words = len(prose.split())              # частоты — по прозе; таблицы и коды не разбавляют
    metrics = {"words": words, "words_all": len(after.split())}

    # голос — абсолютно, а не «стало меньше, чем было»
    s, _ = soul.measure(after)
    voice_total = round(sum(v["per1k"] for v in s.values()), 1) if s else 0.0
    metrics["voice_total"] = voice_total
    metrics["voice_low"] = norms["voice_low"]
    if s and words >= soul.MIN_WORDS:
        if voice_total < norms["voice_low"]:
            violations.append(_item(
                "voice_below_norm",
                f"живой голос ниже нижней границы автора: {voice_total:.1f} "
                f"при минимуме {norms['voice_low']} на 1000 слов",
                suggestion="обращаться к читателю на «вы», советовать глаголом, оговариваться через «но» и «хотя»",
            ))
        for name, cfg in soul.SIGNALS.items():
            if s[name]["per1k"] < cfg["low"]:
                warnings.append(_item(
                    "voice_signal_low",
                    f"ниже нормы: {name} — {s[name]['per1k']:.1f} при минимуме {cfg['low']} на 1000 слов",
                    "review", signal=name, suggestion=cfg["hint"],
                ))

    # ритм — абсолютно, с двумя порогами
    r = rhythm.measure(after)
    metrics["rhythm_ratio"] = round(r["ratio"], 3) if r else None
    metrics["rhythm_alarm"] = norms["rhythm_alarm"]
    if r and r["n"] >= MIN_RHYTHM_SENTENCES:
        if r["ratio"] < RHYTHM_FLOOR:
            violations.append(_item(
                "rhythm_below_norm",
                f"ритм выровнен: разброс/среднее {r['ratio']:.2f} при минимуме корпуса "
                f"{RHYTHM_FLOOR} (норма автора {norms['rhythm_ratio']})",
                suggestion="чередовать короткую фразу с длинным периодом, не подгонять предложения к одной длине",
            ))
        elif r["ratio"] < norms["rhythm_alarm"]:
            warnings.append(_item(
                "rhythm_below_norm",
                f"проверьте ритм: {r['ratio']:.2f} ниже тревоги {norms['rhythm_alarm']}",
                "review",
            ))

    # маркеры шаблона — абсолютно
    pats = markers.load_patterns()
    hits = markers.scan(after, pats)          # сканер сам гасит код и цитаты
    remove_hits = [h for h in hits if h["action"] == "remove"]
    per10k = 10000 * len(hits) / words if words else 0.0
    metrics.update({
        "markers_total": len(hits), "markers_remove": len(remove_hits),
        "markers_per_10k": round(per10k, 1),
    })
    if remove_hits:
        shown = "; ".join(f"«{h['text']}» (стр. {h['line']})" for h in remove_hits[:3])
        item = _item(
            "markers_remove_present",
            f"шаблонные фразы, которых у автора не бывает: {shown}",
            "error" if len(remove_hits) > MARKERS_REMOVE_MAX else "review",
            suggestion="вырезать без замены на другую рамку",
        )
        (violations if len(remove_hits) > MARKERS_REMOVE_MAX else warnings).append(item)
    if words >= MARKERS_MIN_WORDS and per10k > norms["markers_per_10k"]:
        names = {}
        for h in hits:
            names[h["name"]] = names.get(h["name"], 0) + 1
        top = ", ".join(f"{n} ×{c}" for n, c in sorted(names.items(), key=lambda kv: -kv[1])[:3])
        hard = per10k > MARKERS_HARD_PER_10K
        item = _item(
            "markers_above_norm",
            f"маркеров шаблона {per10k:.1f} на 10 000 слов при норме автора "
            f"{norms['markers_per_10k']}: {top}",
            "error" if hard else "review",
            suggestion="переписать от смысла: назвать факт вместо рамки",
        )
        (violations if hard else warnings).append(item)

    # структура — по скелету профиля, с честным «нет материала»
    data = structure.profile_data(profile)
    st_findings, st_metrics = structure.analyze(
        after, profile, archetype=archetype, expansion=expansion,
        expected_classes=expected_classes, deep=True,
    )
    titles = {sec["id"]: sec["title"] for sec in data["sections"]}
    declared_seen = []
    for f in st_findings:
        fid = f["id"]
        if fid.startswith("structure.missing."):
            sid = fid.rsplit(".", 1)[1]
            if sid in declared:
                declared_seen.append(sid)
                warnings.append(_item(
                    "structure_declared_missing",
                    f"раздел «{titles.get(sid, sid)}» не написан: в исходнике нет материала — "
                    "предупредить автора",
                    "review", signal=sid,
                ))
            else:
                violations.append(_item(
                    "structure_missing",
                    f"нет обязательного раздела «{titles.get(sid, sid)}»; если в исходнике нет "
                    "материала — объявить его отсутствующим, а не выдумывать",
                    signal=sid, suggestion=f.get("suggestion", ""),
                ))
        elif fid == "structure.order":
            warnings.append(_item(
                "structure_order",
                "порядок разделов отличается от скелета жанра: " + f.get("evidence", ""),
                "review", suggestion=f.get("suggestion", ""),
            ))
        elif fid.startswith("structure.thin."):
            warnings.append(_item("structure_thin", f["message"], "review",
                                  signal=fid.rsplit(".", 1)[1], line=f.get("line")))
        elif fid == "structure.wall":
            warnings.append(_item("structure_wall", f["message"], "review",
                                  suggestion=f.get("suggestion", "")))
        elif fid == "structure.matchups":
            warnings.append(_item("matchups_incomplete", f["message"], "review",
                                  suggestion=f.get("suggestion", "")))
        elif fid in ("structure.opening.archetype", "structure.opening.expansion"):
            warnings.append(_item("opening_missing", f["message"], "review",
                                  suggestion="назвать предмет в первом предложении, как в зачине автора"))
    metrics.update({
        "sections_present": [sid for sid in st_metrics.get("sections", {})
                             if sid in {sec["id"] for sec in data["sections"]}],
        "sections_missing": [sid for sid in st_metrics.get("missing", []) if sid not in declared],
        "sections_declared_missing": declared_seen,
        "sections_order_ok": st_metrics.get("order_ok", True),
        "classes_missing": st_metrics.get("classes_missing", []),
        "opening": st_metrics.get("opening", {}),
    })
    # словарь автора: сленг и ранги не по локализации — отказ, а не вкус
    term_hits = terminology_hits(after)
    metrics["terminology_hits"] = len(term_hits)
    if term_hits:
        shown = {}
        for h in term_hits:
            shown.setdefault(f"«{h['found']}» → «{h['preferred']}»", h["line"])
        listed = "; ".join(f"{k} (стр. {v})" for k, v in list(shown.items())[:6])
        violations.append(_item(
            "term_replace",
            f"слова не по словарю автора: {listed}",
            suggestion="заменить по словарю CLAUDE.md; ранги — по локализации",
        ))

    # форма подачи — по профилю: таблицы, коды, оценочные буквы, повторы цифр
    form_v, form_w, form_m = form_checks(after, data.get("form"))
    violations.extend(form_v)
    warnings.extend(form_w)
    metrics["form"] = {"tables": form_m["tables"], "codes": form_m["codes"],
                       "grade_labels": len(form_m["grade_labels"]),
                       "repeated_facts": len(form_m["repeated_facts"])}

    if data["min_words"] and words < data["min_words"]:
        warnings.append(_item(
            "text_below_min_words",
            f"текст короче минимума профиля ({words} из {data['min_words']} слов)",
            "review",
        ))

    # аккуратность — на просмотр
    el = elegance.measure(after)
    metrics["elegance"] = {k: el[k] for k in ("nominalization_per_100w", "same_start_runs",
                                              "concreteness_per_100w")} if el else {}
    for f in elegance.findings(after, el):
        warnings.append(_item("elegance_" + f["id"].split(".", 1)[1].replace("-", "_"),
                              f["message"], "review", suggestion=f.get("suggestion", ""),
                              line=f.get("line")))

    metrics["norms_provisional"] = bool(norms.get("provisional"))
    return violations, warnings, metrics


def main():
    ap = argparse.ArgumentParser(description="Затвор переплавки: результат против нормы автора")
    ap.add_argument("file")
    ap.add_argument("--profile", default="constructed-guide")
    ap.add_argument("--game", default="hearthstone")
    ap.add_argument("--declared-missing", default="", help="id разделов без материала, через запятую")
    ap.add_argument("--expected-classes", default="", help="классы исходника, через запятую")
    ap.add_argument("--archetype")
    ap.add_argument("--expansion")
    ap.add_argument("--format", choices=["text", "json"], default="text")
    args = ap.parse_args()
    p = Path(args.file)
    if not p.exists():
        print(f"нет файла: {p}", file=sys.stderr)
        return 2
    C.ensure_venv("pymorphy3")
    text = p.read_text(encoding="utf-8")
    declared = [x.strip() for x in args.declared_missing.split(",") if x.strip()]
    classes = [x.strip() for x in args.expected_classes.split(",") if x.strip()] or None
    violations, warnings, metrics = analyze(
        text, norms=norms_for(args.game), profile=args.profile, declared_missing=declared,
        expected_classes=classes, archetype=args.archetype, expansion=args.expansion,
    )
    if args.format == "json":
        print(json.dumps({"accepted": not violations, "violations": violations,
                          "warnings": warnings, "metrics": metrics},
                         ensure_ascii=False, indent=2))
        return 1 if violations else 0
    print("PASS" if not violations else "REJECTED")
    for item in violations:
        print(f"  [{item['kind']}] {item['message']}")
    for item in warnings:
        print(f"  [REVIEW:{item['kind']}] {item['message']}")
    print("\n  ИЗМЕРЕНО")
    for k in ("words", "voice_total", "rhythm_ratio", "markers_per_10k", "sections_present",
              "sections_missing", "sections_declared_missing", "classes_missing"):
        print(f"    {k:<26} {metrics.get(k)}")
    if metrics.get("norms_provisional"):
        print("\n  нормы заимствованы: оценки голоса и ритма — ориентир, не эталон")
    return 1 if violations else 0


if __name__ == "__main__":
    sys.exit(main())
