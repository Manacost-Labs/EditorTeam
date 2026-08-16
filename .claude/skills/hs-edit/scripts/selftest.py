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

HERE, CORPUS = C.SCRIPTS, C.CORPUS

LIMIT_TOTAL = 20.0        # срабатываний маркеров на 10 000 слов (замер: 12.2)
LIMIT_ONE = 6.0           # ни одно правило не даёт больше этого на 10 000 слов
SOUL_MIN = 20.0           # живых сигналов на 1000 слов в среднем по корпусу
STRUCT_MIN = 90.0         # в скольких % гайдов опознаётся структура (замер: 96%)
CONS_MAX = 0.5           # находок разнобоя на гайд (замер: 0.12)


load = C.sibling


def main():
    if not CORPUS.exists():
        print(f"нет корпуса: {CORPUS}", file=sys.stderr)
        return 2

    markers = load("markers")
    soul = load("soul")
    pats = markers.load_patterns()

    hits = Counter()
    words = 0
    souls = []
    files = sorted(CORPUS.glob("*.md"))
    for f in files:
        t = C.body(f)
        words += len(t.split())
        for fd in markers.scan(t, pats):
            hits[fd["name"]] += 1
        s, w = soul.measure(t)
        if s:
            souls.append(sum(v["per1k"] for v in s.values()))

    total = sum(hits.values())
    rate = 10000 * total / words
    soul_avg = sum(souls) / len(souls) if souls else 0

    print(f"корпус: {len(files)} гайдов, {words} слов, {len(pats)} правил\n")
    print(f"маркеров всего      {total}  =  {rate:.1f} на 10к слов   (порог {LIMIT_TOTAL})")

    fails = []
    if rate > LIMIT_TOTAL:
        fails.append(f"общая частота {rate:.1f} выше порога {LIMIT_TOTAL} — "
                     f"правила ловят авторскую манеру, а не шаблон")

    print("\nсамые частые правила:")
    for name, c in hits.most_common(8):
        r = 10000 * c / words
        mark = "  ← ВЫШЕ ПОРОГА" if r > LIMIT_ONE else ""
        print(f"  {name:<42}{c:>6}{r:>8.1f}{mark}")
        if r > LIMIT_ONE:
            fails.append(f"правило «{name}» даёт {r:.1f} на 10к — проверить на ложные срабатывания")

    print(f"\nживых сигналов      {soul_avg:.1f} на 1000 слов   (минимум {SOUL_MIN})")
    if soul_avg < SOUL_MIN:
        fails.append(f"детектор живого видит только {soul_avg:.1f} — "
                     f"сломан либо он, либо корпус")

    # структура: проверка должна опознавать разделы в подавляющем большинстве гайдов
    structure = load("structure")
    full = 0
    for f in files:
        t = C.body(f)
        got = structure.find_blocks(structure.headings(t))
        if not [n for n, _, _, req in structure.BLOCKS if req and n not in got]:
            full += 1
    share = 100 * full / len(files)
    print(f"структура опознана  {full} из {len(files)} гайдов ({share:.0f}%)   (минимум {STRUCT_MIN:.0f}%)")
    if share < STRUCT_MIN:
        fails.append(f"структура опознаётся только в {share:.0f}% гайдов — "
                     f"варианты названий разделов отстали от практики")

    # согласованность: на вычитанных гайдах находок должно быть мало
    cons = load("consistency")
    cv = sum(len(cons.check_variants(C.mask_protected(C.body(f))))
             for f in files)
    per = cv / len(files)
    print(f"разнобой в текстах  {cv} на {len(files)} гайдов = {per:.2f} на гайд   "
          f"(порог {CONS_MAX})")
    if per > CONS_MAX:
        fails.append(f"проверка согласованности даёт {per:.2f} находки на гайд — "
                     f"ловит оформление, а не разнобой")

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
