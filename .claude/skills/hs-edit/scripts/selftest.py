#!/usr/bin/env python3
"""Регрессия правил: корпус как тест-набор заведомо хорошего письма.

    python3 selftest.py

49 опубликованных гайдов — это 248 тысяч слов текста, который править не надо.
Если сканер маркеров начинает ругаться на них чаще порога, значит новое правило
ловит не шаблон, а авторскую манеру. Запускать после каждой правки markers.json.

Пороги взяты по факту после аудита, с запасом. Их можно поднять сознательно,
но тогда в комментарии должно быть написано — почему.
"""

import importlib.util
import pathlib
import sys
from collections import Counter

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import common as C  # noqa: E402

HERE = C.SCRIPTS

LIMIT_TOTAL = 20.0  # срабатываний маркеров на 10 000 слов (замер: 12.2)
LIMIT_ONE = 6.0  # ни одно правило не даёт больше этого на 10 000 слов
SOUL_MIN = 20.0  # живых сигналов на 1000 слов в среднем по корпусу
STRUCT_MIN = 90.0  # в скольких % гайдов опознаётся структура (замер: 96%)
CONS_MAX = 0.5  # находок разнобоя на гайд (замер: 0.12)
# затвор переплавки судит по абсолютной норме, поэтому не должен отвергать
# опубликованные гайды: допускается один известный случай из 49 — гайд с
# голосом 20.05 при границе 20.6 (2.04%)
REWRITE_GATE_MAX_PCT = 2.5
# доля гайдов, где структурная проверка находит только структурные находки —
# без замечаний к голосу и ритму (ритм у 1 гайда из 49 ниже 0.42)
REWRITE_GATE_CHECK_KINDS = ("voice_below_norm", "rhythm_below_norm", "markers_remove_present",
                            "markers_above_norm")


load = C.sibling


def main():
    records = C.corpus_records()
    if not records:
        print("нет approved style corpus", file=sys.stderr)
        return 2

    markers = load("markers")
    soul = load("soul")
    pats = markers.load_patterns()

    hits = Counter()
    words = 0
    souls = []
    for _, _, t in records:
        words += len(t.split())
        for fd in markers.scan(t, pats):
            hits[fd["name"]] += 1
        s, w = soul.measure(t)
        if s:
            souls.append(sum(v["per1k"] for v in s.values()))

    total = sum(hits.values())
    rate = 10000 * total / words
    soul_avg = sum(souls) / len(souls) if souls else 0

    print(f"корпус: {len(records)} гайдов, {words} слов, {len(pats)} правил\n")
    print(f"маркеров всего      {total}  =  {rate:.1f} на 10к слов   (порог {LIMIT_TOTAL})")

    fails = []
    if rate > LIMIT_TOTAL:
        fails.append(
            f"общая частота {rate:.1f} выше порога {LIMIT_TOTAL} — "
            f"правила ловят авторскую манеру, а не шаблон"
        )

    print("\nсамые частые правила:")
    for name, c in hits.most_common(8):
        r = 10000 * c / words
        mark = "  ← ВЫШЕ ПОРОГА" if r > LIMIT_ONE else ""
        print(f"  {name:<42}{c:>6}{r:>8.1f}{mark}")
        if r > LIMIT_ONE:
            fails.append(f"правило «{name}» даёт {r:.1f} на 10к — проверить на ложные срабатывания")

    print(f"\nживых сигналов      {soul_avg:.1f} на 1000 слов   (минимум {SOUL_MIN})")
    if soul_avg < SOUL_MIN:
        fails.append(f"детектор живого видит только {soul_avg:.1f} — сломан либо он, либо корпус")

    # структура: проверка должна опознавать разделы в подавляющем большинстве гайдов
    structure = load("structure")
    full = 0
    for _, _, t in records:
        got = structure.find_blocks(structure.headings(t))
        if not [n for n, _, _, req in structure.BLOCKS if req and n not in got]:
            full += 1
    share = 100 * full / len(records)
    print(
        f"структура опознана  {full} из {len(records)} гайдов ({share:.0f}%)   (минимум {STRUCT_MIN:.0f}%)"
    )
    if share < STRUCT_MIN:
        fails.append(
            f"структура опознаётся только в {share:.0f}% гайдов — "
            f"варианты названий разделов отстали от практики"
        )

    # согласованность: на вычитанных гайдах находок должно быть мало
    cons = load("consistency")
    cv = sum(len(cons.check_variants(C.mask_protected(t))) for _, _, t in records)
    per = cv / len(records)
    print(
        f"разнобой в текстах  {cv} на {len(records)} гайдов = {per:.2f} на гайд   "
        f"(порог {CONS_MAX})"
    )
    if per > CONS_MAX:
        fails.append(
            f"проверка согласованности даёт {per:.2f} находки на гайд — "
            f"ловит оформление, а не разнобой"
        )

    # затвор переплавки: абсолютные нормы голоса, ритма и маркеров не должны
    # отвергать самого автора. Структурные находки здесь не считаются: в
    # PDF-корпусе заголовки часто стоят только в оглавлении.
    gate = load("rewrite_gate")
    rejected = 0
    rejected_names = []
    for path, _, t in records:
        violations, _, _ = gate.analyze(t, profile="constructed-guide")
        bad = [v for v in violations if v["kind"] in REWRITE_GATE_CHECK_KINDS]
        if bad:
            rejected += 1
            rejected_names.append(f"{C.guide_name(path)[:40]}: {bad[0]['kind']}")
    gate_pct = 100 * rejected / len(records)
    print(
        f"затвор переплавки   отказов {rejected} из {len(records)} гайдов ({gate_pct:.1f}%)   "
        f"(порог {REWRITE_GATE_MAX_PCT:.0f}%)"
    )
    for name in rejected_names[:3]:
        print(f"  {name}")
    if gate_pct > REWRITE_GATE_MAX_PCT:
        fails.append(
            f"затвор переплавки отвергает {gate_pct:.1f}% опубликованных гайдов — "
            f"пороги голоса, ритма или маркеров жёстче авторской нормы"
        )

    # лексика: у автора между собственными гайдами чужих слов 2–3%; если
    # детектор видит больше, значит он считает автора чужим самому себе
    lexicon = load("lexicon")
    ratios = [r for r, _ in (lexicon.calibrate(sample=6) or [])]
    if ratios:
        import statistics as _st
        med = _st.median(ratios)
        print(f"лексика leave-one-out  медиана {med:.1f}%   (порог {lexicon.WARN_PCT:.0f}%)")
        if med > lexicon.WARN_PCT:
            fails.append(f"детектор лексики считает автора чужим: медиана {med:.1f}% выше {lexicon.WARN_PCT}%")

    print()
    if fails:
        print("ПРОВАЛ")
        for f in fails:
            print(f"  ! {f}")
        return 1
    print("ОК — правила не задевают опубликованные тексты")
    return 0


if __name__ == "__main__":
    sys.exit(main())
