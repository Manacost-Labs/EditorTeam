#!/usr/bin/env python3
"""Оценка текста по 10-балльной шкале — соответствие авторской норме.

    python3 author.py текст.md
    python3 author.py текст.md --подробно
    python3 author.py --калибровка          # что показывает корпус

Что это за число и чем оно не является.

Это НЕ оценка качества текста. «Хорошо написано» измерить нельзя, и любой
балл такого рода был бы выдуман. Здесь считается другое: **насколько текст
похож на твои опубликованные гайды** по шести измеримым признакам.

Из этого следуют границы:
  * высокий балл не значит «шедевр» — значит «написано в твоей манере»;
  * низкий балл не значит «плохо» — значит «не похоже на твой корпус»,
    и это может быть осознанным экспериментом;
  * балл не заменяет чтение. Он показывает, куда смотреть.

Формула открыта и лежит в WEIGHTS: каждый вклад можно проверить руками.
"""

import argparse
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common as C  # noqa: E402

# вес каждой составляющей. Голос весит больше формальностей: ритм и живое —
# это то, что делает текст твоим, а опечатка в названии карты чинится за минуту
WEIGHTS = {
    "живое": 0.30,
    "ритм": 0.25,
    "чистота": 0.15,
    "названия": 0.10,
    "согласованность": 0.10,
    "структура": 0.10,
}


_IDX = {}


def _card_index(cards):
    """Индекс карт строится один раз: на калибровке это 49 раз по секунде."""
    if "idx" not in _IDX:
        _IDX["idx"] = cards.Index(C.card_db()["карты"], C.morph())
    return _IDX["idx"]


def scale(value, good, bad, invert=False):
    """Линейная шкала 0–10 между «как в корпусе» и «совсем не так»."""
    if invert:
        value, good, bad = -value, -good, -bad
    if bad == good:
        return 10.0
    x = (value - bad) / (good - bad)
    return max(0.0, min(10.0, 10.0 * x))


def evaluate(text, tools):
    soul, rhythm, markers, cards, structure, consistency = tools
    words = max(1, len(text.split()))
    out = {}

    s, _ = soul.measure(text)
    live = sum(v["per1k"] for v in s.values()) if s else 0
    out["живое"] = (scale(live, soul.TOTAL_MED, 5.0), f"{live:.1f} сигн./1000 сл. при норме {soul.TOTAL_MED}")

    r = rhythm.measure(text)
    ratio = r["ratio"] if r else 0
    out["ритм"] = (scale(ratio, rhythm.BASE["ratio"], 0.20),
                   f"разброс/среднее {ratio:.2f} при норме {rhythm.BASE['ratio']}")

    pats = markers.load_patterns()
    m = len(markers.scan(text, pats))
    per1k = 1000 * m / words
    out["чистота"] = (scale(per1k, 0.0, 6.0, invert=True),
                      f"{m} маркеров = {per1k:.1f}/1000 сл.")

    db = C.card_db()
    idx = _card_index(cards)
    errs = (sum(cards.check_apostrophes(text, idx).values())
            + sum(cards.check_dashes(text, db["карты"]).values())
            + sum(cards.check_caps(text, db["карты"], set(db.get("механики", []))).values()))
    out["названия"] = (scale(1000 * errs / words, 0.0, 3.0, invert=True),
                       f"{errs} расхождений с локализацией")

    v = len(consistency.check_variants(C.mask_protected(text)))
    out["согласованность"] = (scale(1000 * v / words, 0.0, 4.0, invert=True),
                              f"{v} мест с разнобоем")

    found = structure.find_blocks(structure.headings(text))
    req = [n for n, _, _, r_ in structure.BLOCKS if r_]
    have = sum(1 for n in req if n in found)
    mu = structure.check_matchups(text, structure.headings(text), found)
    cover = len(mu[0]) / len(structure.CLASSES) if mu else 0
    st = 10 * (0.7 * have / len(req) + 0.3 * cover)
    out["структура"] = (st, f"{have} из {len(req)} разделов, матч-апы {int(cover*len(structure.CLASSES))}/{len(structure.CLASSES)}")

    total = sum(WEIGHTS[k] * v[0] for k, v in out.items())
    return round(total, 1), out


def load_tools():
    C.ensure_venv("pymorphy3")
    return tuple(C.sibling(n) for n in
                 ("soul", "rhythm", "markers", "cards", "structure", "consistency"))


def calibrate(tools):
    files = C.corpus_files()
    if not files:
        print("нет корпуса", file=sys.stderr)
        return 2
    scores = []
    for f in files:
        sc, _ = evaluate(f.read_text(encoding="utf-8"), tools)
        scores.append((sc, C.guide_name(f)[:44]))
    vals = sorted(s for s, _ in scores)
    print(f"\nКАЛИБРОВКА по {len(files)} опубликованным гайдам\n")
    print(f"  медиана        {statistics.median(vals):.1f}")
    print(f"  квартили       {vals[len(vals)//4]:.1f} / {vals[len(vals)//2]:.1f} / {vals[3*len(vals)//4]:.1f}")
    print(f"  мин / макс     {vals[0]:.1f} / {vals[-1]:.1f}")
    scores.sort(reverse=True)
    print("\n  лучшие:")
    for s, n in scores[:3]:
        print(f"    {s:4.1f}  {n}")
    print("  слабейшие:")
    for s, n in scores[-3:]:
        print(f"    {s:4.1f}  {n}")
    print("\n  Шкала имеет смысл только относительно этих чисел:")
    print(f"  «как обычно» — около {statistics.median(vals):.1f}, а не 10.")
    return 0


def main():
    ap = argparse.ArgumentParser(description="Соответствие текста авторской норме")
    ap.add_argument("file", nargs="?")
    ap.add_argument("--подробно", dest="verbose", action="store_true")
    ap.add_argument("--калибровка", dest="cal", action="store_true")
    args = ap.parse_args()

    tools = load_tools()
    if args.cal:
        return calibrate(tools)
    if not args.file:
        ap.error("нужен файл или --калибровка")
    p = Path(args.file)
    if not p.exists():
        print(f"нет файла: {p}", file=sys.stderr)
        return 2

    score, parts = evaluate(p.read_text(encoding="utf-8"), tools)

    print(f"\n{p.name}")
    print(f"\n  {score} / 10   — соответствие авторской норме\n")
    for k in WEIGHTS:
        v, why = parts[k]
        bar = "█" * int(round(v)) + "·" * (10 - int(round(v)))
        print(f"  {k:<16} {v:4.1f}  {bar}  {why}")

    print(f"\n  Медиана опубликованных гайдов — 9.1. Это ориентир, а не проходной балл.")
    weak = sorted(parts.items(), key=lambda kv: kv[1][0])[:2]
    if weak[0][1][0] < 6:
        print(f"\n  Слабее всего: {weak[0][0]} ({weak[0][1][0]:.1f}) и {weak[1][0]} ({weak[1][1][0]:.1f}).")
    print("\n  Это не оценка качества: балл показывает похожесть на твой корпус,")
    print("  а не то, хорошо ли написано. Низкий балл может быть осознанным выбором.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
