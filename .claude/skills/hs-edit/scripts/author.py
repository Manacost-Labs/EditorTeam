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


def _required_sections(structure, profile):
    """Обязательные разделы профиля; без YAML — прежний список BLOCKS."""
    return [s for s in structure.load_profile_sections(profile) if s["required"]]


def _sections_found(structure, text, sections):
    heads = [h.lower().strip() for _, h in structure.headings(text)]
    return [s for s in sections if any(h in s["variants"] for h in heads)]


def evaluate(text, tools, profile="constructed-guide"):
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

    # разделы берутся из профиля (config/profiles), формула прежняя:
    # 0,7 — присутствие обязательных разделов, 0,3 — охват классов
    req = _required_sections(structure, profile)
    have = len(_sections_found(structure, text, req))
    found = structure.find_blocks(structure.headings(text))
    mu = structure.check_matchups(text, structure.headings(text), found)
    cover = len(mu[0]) / len(structure.CLASSES) if mu else 0
    st = 10 * (0.7 * have / max(1, len(req)) + 0.3 * cover) if req else 10.0
    out["структура"] = (st, f"{have} из {len(req)} разделов, матч-апы {int(cover*len(structure.CLASSES))}/{len(structure.CLASSES)}")

    total = sum(WEIGHTS[k] * v[0] for k, v in out.items())
    return round(total, 1), out


IMPERSONAL = r"\b(стоит|не стоит|важно|нужно|необходимо|следует|рекомендуется|"
IMPERSONAL += r"выгоднее|лучше всего|приходится|требуется)\b"


def line_of(text, pos):
    return text.count("\n", 0, pos) + 1


def quote(text, m, width=58):
    a = max(0, m.start() - 18)
    b = min(len(text), m.start() + width)
    return " ".join(text[a:b].split())


def advise(text, parts, tools, profile="constructed-guide"):
    """Короткие рекомендации. Каждая — из замера и с адресом в тексте.

    Никаких «пишите живее»: совет либо указывает на конкретное место,
    либо не выдаётся вовсе.
    """
    import re
    soul, rhythm, markers, cards, structure, consistency = tools
    tips = []

    # ЖИВОЕ: где стоит безличная конструкция вместо совета читателю
    if parts["живое"][0] < 6:
        s, _ = soul.measure(text)
        imp = list(re.finditer(IMPERSONAL, text, re.I))
        if s["императив читателю"]["n"] == 0 and imp:
            first = imp[0]
            tips.append(("живое",
                         f"ни одного совета глаголом. Безличных оборотов {len(imp)}, "
                         f"первый — стр. {line_of(text, first.start())}: «{quote(text, first, 40)}…»",
                         "в твоих гайдах в таких местах стоит «оставляйте», «держите», «не спешите»"))
        if s["обращение к читателю"]["per1k"] < 4:
            n = s["обращение к читателю"]["n"]
            tips.append(("живое",
                         f"обращений к читателю {n} на весь текст при норме 11 на 1000 слов",
                         "читатель ни разу не назван — текст описывает колоду, а не объясняет её ему"))
        if s["скобка с пояснением"]["n"] == 0:
            tips.append(("живое", "нет ни одного пояснения в скобках",
                         "в корпусе 6,3 на гайд — это места, где ты отвлекаешься и уточняешь"))

    # РИТМ: какие предложения тянут среднее вверх
    if parts["ритм"][0] < 6:
        sents = [(len(x.split()), x) for x in C.sentences(text)]
        short = sum(1 for n, _ in sents if n < 8)
        longest = sorted(sents, reverse=True)[:2]
        tips.append(("ритм",
                     f"коротких фраз {short} из {len(sents)}, при норме — каждая восьмая",
                     "рядом с длинным периодом обычно стоит рубленая фраза; здесь её нет"))
        if longest:
            n, s_ = longest[0]
            tips.append(("ритм", f"самое длинное — {n} слов: «{' '.join(s_.split()[:9])}…»",
                         "разрубить его надвое дешевле, чем удлинять остальные"))

    # ЧИСТОТА
    if parts["чистота"][0] < 8:
        found = markers.scan(text, markers.load_patterns())
        by = {}
        for f in found:
            by.setdefault(f["name"], []).append(f)
        for name, items in sorted(by.items(), key=lambda kv: -len(kv[1]))[:2]:
            tips.append(("чистота", f"{name} — {len(items)}, стр. {items[0]['line']}: «{items[0]['text'][:44]}»",
                         items[0]["fix"][:78]))

    # НАЗВАНИЯ
    if parts["названия"][0] < 10:
        db = C.card_db()
        idx = _card_index(cards)
        errs = list(cards.check_apostrophes(text, idx).items())
        errs += [((k[0], k[1]), v) for k, v in cards.check_caps(
            text, db["карты"], set(db.get("механики", []))).items()]
        for (was, off), c in errs[:2]:
            tips.append(("названия", f"«{was}» → «{off}»" + (f", {c} раза" if c > 1 else ""),
                         "сверено с официальной локализацией"))

    # СОГЛАСОВАННОСТЬ
    if parts["согласованность"][0] < 10:
        for label, forms in consistency.check_variants(C.mask_protected(text))[:2]:
            shown = " и ".join(f"«{f}»" for f, _ in forms[:2])
            tips.append(("согласованность", f"{label}: {shown}", "выбрать одно написание"))

    # СТРУКТУРА
    if parts["структура"][0] < 9:
        req = _required_sections(structure, profile)
        have = {s["id"] for s in _sections_found(structure, text, req)}
        lack = [s["title"] for s in req if s["id"] not in have]
        found = structure.find_blocks(structure.headings(text))
        if lack:
            tips.append(("структура", f"нет разделов: {', '.join(lack)}",
                         "в корпусе они есть в 96–100% гайдов"))
        mu = structure.check_matchups(text, structure.headings(text), found)
        if mu and mu[1]:
            tips.append(("структура", f"матч-апы без {len(mu[1])} классов: {', '.join(mu[1][:4])}",
                         "гайд обычно проходит по всем"))

    return tips


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
        sc, _ = evaluate(C.body(f), tools)
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
    ap.add_argument("--profile", default="constructed-guide", help="разделы берутся из профиля")
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

    text = p.read_text(encoding="utf-8")
    score, parts = evaluate(text, tools, args.profile)

    print(f"\n{p.name}")
    print(f"\n  {score} / 10   — соответствие авторской норме\n")
    for k in WEIGHTS:
        v, why = parts[k]
        bar = "█" * int(round(v)) + "·" * (10 - int(round(v)))
        print(f"  {k:<16} {v:4.1f}  {bar}  {why}")

    print(f"\n  медиана опубликованных гайдов — 9.1")

    tips = advise(text, parts, tools, args.profile)
    if tips:
        print("\n  ЧТО ПОДТЯНУТЬ")
        last = None
        for comp, what, why in tips:
            head = f"{comp}" if comp != last else ""
            print(f"\n  {head:<16} {what}")
            print(f"  {'':<16} └ {why}")
            last = comp
        print("\n  Это адреса, а не готовые формулировки: править — тебе.")
        print("  Вписывать за тебя обращения и советы нельзя, выйдет чужой голос.")
    else:
        print("\n  Подтягивать нечего: все составляющие в пределах нормы.")

    print("\n  Балл — похожесть на твой корпус, а не оценка качества.")
    print("  Низкий может быть осознанным выбором: другой жанр, короткий материал.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
