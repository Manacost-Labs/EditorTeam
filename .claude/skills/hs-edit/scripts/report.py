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
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent


def _load(name):
    spec = importlib.util.spec_from_file_location(name, HERE / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


markers = _load("markers")
rhythm = _load("rhythm")
soul = _load("soul")


def protected_lost(a, b):
    """Защищённые элементы, исчезнувшие при правке.

    Числа, статы и коды колод править нельзя. Если они пропали, это
    важнее любой стилистики, поэтому проверяется отдельно.
    """
    pats = {
        "числа": r"\b\d+(?:[.,]\d+)?%?\b",
        "статы": r"\b\d{1,2}/\d{1,2}\b",
        "коды колод": r"\bAAECA\S{10,}",
        "ОТК": r"\bОТК\b",
        "возвещение": r"\bвозвещени\w*",
    }
    lost = {}
    for name, pat in pats.items():
        was = Counter(re.findall(pat, a, re.I))
        now = Counter(re.findall(pat, b, re.I))
        gone = was - now
        if gone:
            lost[name] = gone
    return lost


def words(text):
    return re.findall(r"\S+", text)


MATCH_JACCARD = 0.5     # ниже — это другой абзац, а не тот же после правки
MOVE_MIN_WORDS = 6      # короткие строки (заголовки, подписи) перестановкой не считаются


def paragraphs(text):
    """Абзацы по пустым строкам; каждый — список слов."""
    return [words(p) for p in re.split(r"\n\s*\n", text) if p.strip()]


def _jaccard(x, y):
    sx, sy = {w.lower() for w in x}, {w.lower() for w in y}
    return len(sx & sy) / len(sx | sy) if sx or sy else 1.0


def align_paragraphs(pa, pb):
    """Пары (i, j) «тот же абзац до и после» плюс непарные с обеих сторон.

    Сначала по составу слов, чтобы переставленный абзац нашёл сам себя;
    остаток спаривается по порядку, как это сделал бы обычный дифф, — так
    сильно переписанный абзац считается правкой, а не удалением с вставкой.
    """
    pairs, used_a, used_b = [], set(), set()
    for j, y in enumerate(pb):
        best, score = None, MATCH_JACCARD
        for i, x in enumerate(pa):
            if i in used_a:
                continue
            sc = _jaccard(x, y)
            if sc > score:
                best, score = i, sc
        if best is not None:
            pairs.append((best, j))
            used_a.add(best)
            used_b.add(j)
    rest_a = [i for i in range(len(pa)) if i not in used_a]
    rest_b = [j for j in range(len(pb)) if j not in used_b]
    for i, j in zip(rest_a, rest_b):
        pairs.append((i, j))
    rest_a, rest_b = rest_a[len(rest_b):], rest_b[len(rest_a):]
    return sorted(pairs, key=lambda ij: ij[1]), rest_a, rest_b


def _moved(pairs, pa):
    """Сколько пар стоит не по порядку исходника: всё, что не попало в самую
    длинную возрастающую подпоследовательность индексов «до»."""
    seq = [i for i, _ in pairs if len(pa[i]) >= MOVE_MIN_WORDS]
    if len(seq) < 2:
        return 0
    tails = []
    for x in seq:                       # длина LIS за n log n
        lo, hi = 0, len(tails)
        while lo < hi:
            mid = (lo + hi) // 2
            if tails[mid] < x:
                lo = mid + 1
            else:
                hi = mid
        if lo == len(tails):
            tails.append(x)
        else:
            tails[lo] = x
    return len(seq) - len(tails)


def diff_stats(a, b):
    """Объём правки по абзацам: переставленный абзац — не переписанный."""
    wa = words(a)
    pa, pb = paragraphs(a), paragraphs(b)
    pairs, rest_a, rest_b = align_paragraphs(pa, pb)
    replaced = inserted = deleted = 0
    for i, j in pairs:
        sm = difflib.SequenceMatcher(None, pa[i], pb[j], autojunk=False)
        for tag, i1, i2, j1, j2 in sm.get_opcodes():
            if tag == "replace":
                replaced += max(i2 - i1, j2 - j1)
            elif tag == "insert":
                inserted += j2 - j1
            elif tag == "delete":
                deleted += i2 - i1
    deleted += sum(len(pa[i]) for i in rest_a)
    inserted += sum(len(pb[j]) for j in rest_b)
    changed = replaced + inserted + deleted
    return {
        "total": len(wa),
        "changed": changed,
        "pct": 100 * changed / len(wa) if wa else 0,
        "replaced": replaced,
        "inserted": inserted,
        "deleted": deleted,
        "moved": _moved(pairs, pa),
        "paragraphs_removed": len(rest_a),
        "paragraphs_added": len(rest_b),
        "len_pct": 100 * (len(b) - len(a)) / len(a) if a else 0,
    }


def edits(a, b, limit=40):
    """Список правок «было → стало» по словам внутри абзацев; перестановка
    абзаца — одна строка «move», а не десятки замен."""
    pa, pb = paragraphs(a), paragraphs(b)
    pairs, rest_a, rest_b = align_paragraphs(pa, pb)
    out = []
    order = [i for i, _ in pairs]
    for k, (i, j) in enumerate(pairs):
        if k and order[k] < max(order[:k]) and len(pa[i]) >= MOVE_MIN_WORDS:
            out.append(("move", " ".join(pa[i][:8])[:70], ""))
        sm = difflib.SequenceMatcher(None, pa[i], pb[j], autojunk=False)
        for tag, i1, i2, j1, j2 in sm.get_opcodes():
            if tag == "equal":
                continue
            was = " ".join(pa[i][i1:i2]) or "—"
            now = " ".join(pb[j][j1:j2]) or "—"
            out.append((tag, was[:70], now[:70]))
    for i in rest_a:
        out.append(("delete", " ".join(pa[i][:8])[:70] + " …", "—"))
    for j in rest_b:
        out.append(("insert", "—", " ".join(pb[j][:8])[:70] + " …"))
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
        if tag == "move":
            print(f"  ↕ абзац переставлен: «{was}…»")
            continue
        sign = {"replace": "→", "insert": "+", "delete": "−"}[tag]
        print(f"  {was}  {sign}  {now}" if tag == "replace"
              else f"  {sign} {now if tag == 'insert' else was}")

    print("\nСКОЛЬКО")
    print(f"  затронуто          {d['pct']:.1f}% текста ({d['changed']} из {d['total']} слов)")
    print(f"  из них замен       {d['replaced']}, вставок {d['inserted']}, удалений {d['deleted']}")
    if d["moved"] or d["paragraphs_added"] or d["paragraphs_removed"]:
        print(f"  абзацев            переставлено {d['moved']}, добавлено {d['paragraphs_added']}, "
              f"удалено {d['paragraphs_removed']}  (перестановка в «затронуто» не входит)")
    print(f"  длина              {d['len_pct']:+.1f}%  (аудит удалений от −5%)")

    print("\nМАРКЕРЫ")
    print(f"  всего              {ma} → {mb}")
    for k, label in (("remove", "убрать"), ("rewrite", "переписать"), ("review", "смотреть")):
        if by_a[k] or by_b[k]:
            print(f"  {label:<18} {by_a[k]} → {by_b[k]}")

    sa, wa_ = soul.measure(a)
    sb, wb_ = soul.measure(b)
    if sa and sb:
        print("\nЖИВЫЕ СИГНАЛЫ")
        total_a = sum(v["per1k"] for v in sa.values())
        total_b = sum(v["per1k"] for v in sb.values())
        print(f"  всего              {total_a:.1f} → {total_b:.1f} на 1000 сл. "
              f"(норма {soul.TOTAL_MED})")
        for name in soul.SIGNALS:
            state = soul.classify(sa[name], sb[name], wa_, wb_)
            if state == "сигналы удалены":
                print(f"  ! {name:<18} {sa[name]['n']} → {sb[name]['n']} мест — УДАЛЕНЫ")

    gone = protected_lost(a, b)
    if gone:
        print("\nЗАЩИЩЁННОЕ ПРОПАЛО")
        for name, items in gone.items():
            shown = ", ".join(f"«{k}»×{v}" if v > 1 else f"«{k}»" for k, v in list(items.items())[:6])
            print(f"  ! {name}: {shown}")

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
    if sa and sb:
        removed = [n for n in soul.SIGNALS
                   if soul.classify(sa[n], sb[n], wa_, wb_) == "сигналы удалены"]
        if removed:
            flags.append(f"вычищено живое: {', '.join(removed)}")
    if gone:
        flags.append(f"пропали защищённые элементы: {', '.join(gone)}")
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
