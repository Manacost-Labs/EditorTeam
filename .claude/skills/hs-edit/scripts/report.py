#!/usr/bin/env python3
"""Отчёт о правке: что изменилось и на сколько.

    python3 report.py до.md после.md

Считает только то, что действительно измеримо: объём правки, маркеры,
ритм, длину. Оценку «стало лучше на N%» не выдаёт — такого числа нет.
"""

import argparse
import difflib
import importlib.util
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent


def _load(name):
    spec = importlib.util.spec_from_file_location(name, HERE / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


markers = _load("markers")
rhythm = _load("rhythm")


def words(text):
    return re.findall(r"\S+", text)


def diff_stats(a, b):
    wa, wb = words(a), words(b)
    sm = difflib.SequenceMatcher(None, wa, wb, autojunk=False)
    changed = replaced = inserted = deleted = 0
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "replace":
            replaced += max(i2 - i1, j2 - j1)
        elif tag == "insert":
            inserted += j2 - j1
        elif tag == "delete":
            deleted += i2 - i1
    changed = replaced + inserted + deleted
    return {
        "total": len(wa),
        "changed": changed,
        "pct": 100 * changed / len(wa) if wa else 0,
        "replaced": replaced,
        "inserted": inserted,
        "deleted": deleted,
        "len_pct": 100 * (len(b) - len(a)) / len(a) if a else 0,
    }


def edits(a, b, limit=40):
    """Список конкретных замен «было → стало» на уровне слов."""
    wa, wb = words(a), words(b)
    sm = difflib.SequenceMatcher(None, wa, wb, autojunk=False)
    out = []
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            continue
        was = " ".join(wa[i1:i2]) or "—"
        now = " ".join(wb[j1:j2]) or "—"
        out.append((tag, was[:70], now[:70]))
    return out[:limit]


def count_markers(text):
    pats = markers.load_patterns()
    found = markers.scan(text, pats)
    by = {"remove": 0, "rewrite": 0, "review": 0}
    for f in found:
        by[f["action"]] += 1
    return len(found), by


def main():
    ap = argparse.ArgumentParser(description="Отчёт о правке")
    ap.add_argument("before")
    ap.add_argument("after")
    ap.add_argument("--edits", type=int, default=12, help="сколько правок показать")
    args = ap.parse_args()

    pa, pb = Path(args.before), Path(args.after)
    for p in (pa, pb):
        if not p.exists():
            print(f"нет файла: {p}", file=sys.stderr)
            return 2

    a = pa.read_text(encoding="utf-8")
    b = pb.read_text(encoding="utf-8")

    d = diff_stats(a, b)
    ma, by_a = count_markers(a)
    mb, by_b = count_markers(b)
    ra, rb = rhythm.measure(a), rhythm.measure(b)

    print("\nПРАВКИ")
    lst = edits(a, b, args.edits)
    if not lst:
        print("  ничего не изменилось")
    for tag, was, now in lst:
        sign = {"replace": "→", "insert": "+", "delete": "−"}[tag]
        print(f"  {was}  {sign}  {now}" if tag == "replace"
              else f"  {sign} {now if tag == 'insert' else was}")

    print("\nСКОЛЬКО")
    print(f"  затронуто          {d['pct']:.1f}% текста ({d['changed']} из {d['total']} слов)")
    print(f"  из них замен       {d['replaced']}, вставок {d['inserted']}, удалений {d['deleted']}")
    print(f"  длина              {d['len_pct']:+.1f}%  (порог −5%)")

    print("\nМАРКЕРЫ")
    print(f"  всего              {ma} → {mb}")
    for k, label in (("remove", "убрать"), ("rewrite", "переписать"), ("review", "смотреть")):
        if by_a[k] or by_b[k]:
            print(f"  {label:<18} {by_a[k]} → {by_b[k]}")

    if ra and rb:
        print("\nРИТМ")
        print(f"  разброс/среднее    {ra['ratio']:.2f} → {rb['ratio']:.2f}  (эталон {rhythm.BASE['ratio']})")
        print(f"  среднее, слов      {ra['mean']:.1f} → {rb['mean']:.1f}")

    print("\nВЕРДИКТ")
    flags = []
    if d["len_pct"] < -5:
        flags.append(f"текст усох на {abs(d['len_pct']):.0f}% — проверить, не пропали ли мысли")
    if rb and ra and rb["ratio"] < ra["ratio"] - 0.03:
        flags.append(f"ритм выровнен: {ra['ratio']:.2f} → {rb['ratio']:.2f}")
    if mb > ma:
        flags.append("маркеров стало больше — правка внесла шаблон")
    if d["pct"] > 25:
        flags.append(f"затронуто {d['pct']:.0f}% текста — это уже не вычитка, а переписывание")
    if flags:
        for f in flags:
            print(f"  ! {f}")
    else:
        print("  правка в границах: длина, ритм и маркеры не ухудшились")
    print("\n  Оценки «стало лучше на N%» здесь нет: такой величины не существует.")
    print("  Выше — то, что измеряется. Качество остаётся суждением редактора.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
