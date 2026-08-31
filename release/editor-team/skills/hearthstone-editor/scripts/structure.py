#!/usr/bin/env python3
"""Проверка структуры гайда по архетипу.

    python3 structure.py черновик.md

Состав разделов снят с корпуса «гайды/» — 49 опубликованных гайдов.
Порядок и обязательность не выдуманы, рядом с каждым блоком стоит частота.

Скрипт ничего не переставляет: он показывает, чего не хватает и что стоит
не на своём месте. Порядок разделов — авторское решение.
"""

import argparse
import re
import sys
from pathlib import Path

# блок -> (варианты названия, доля гайдов, обязателен ли)
BLOCKS = [
    ("Сборки",     ["сборки архетипа", "сборки", "топовые сборки", "списки колод"], 98, True),
    ("Декбилдинг", ["вопросы декбилдинга", "как собрать колоду", "основа колоды",
                    "замены", "опциональные и технические карты"], 98, True),
    ("Муллиган",   ["муллиган", "общие правила муллигана",
                    "муллиган против каждого класса"], 100, True),
    ("Стратегия",  ["стратегия игры", "основы геймплея", "как работает колода",
                    "тонкости геймплея", "способы победы", "полезные советы"], 96, True),
    ("Матч-апы",   ["матч-апы", "матчапы", "матч-апы колоды"], 100, True),
    ("Заключение", ["заключение"], 27, False),
]

# Составные имена идут первыми и «съедают» свои вхождения: иначе «Охотник на
# демонов» засчитывается как обычный Охотник, а сам он теряется. Для каждого
# класса — образец, допускающий падежные окончания.
CLASSES = [
    "Охотник на демонов",
    "Рыцарь смерти",
    "Воин", "Шаман", "Разбойник", "Паладин", "Охотник",
    "Друид", "Чернокнижник", "Маг", "Жрец",
]

# окончание первого слова свободное: «Охотника на демонов», «Рыцарю смерти»
CLASS_PATTERNS = {
    "Охотник на демонов": r"Охотник\w*\s+на\s+демонов",
    "Рыцарь смерти": r"Рыцар\w+\s+смерти",
    "Воин": r"Воин\w*",
    "Шаман": r"Шаман\w*",
    "Разбойник": r"Разбойник\w*",
    "Паладин": r"Паладин\w*",
    "Охотник": r"Охотник\w*",
    "Друид": r"Друид\w*",
    "Чернокнижник": r"Чернокнижник\w*",
    "Маг": r"Маг\w*",
    "Жрец": r"(?:Жрец|Жреца|Жрецу|Жрецом|Жреце|Жрецы|Жрецов|Жрецам|Жрецами)",
}


def headings(text):
    """Заголовки: markdown-решётки и короткие самостоятельные строки."""
    out = []
    for i, raw in enumerate(text.split("\n")):
        l = raw.strip()
        if not l:
            continue
        if l.startswith("#"):
            out.append((i, l.lstrip("# ").strip()))
        elif (3 <= len(l) <= 40 and 1 <= len(l.split()) <= 5
              and l[0].isupper() and not l.endswith((".", "!", "?", ",", ":"))):
            out.append((i, l))
    return out


def find_blocks(heads):
    found = {}
    for i, h in heads:
        low = h.lower().strip()
        for name, variants, _, _ in BLOCKS:
            if low in variants and name not in found:
                found[name] = (i, h)
    return found


def check_matchups(text, heads, found):
    """Матч-апы должны покрывать классы. Берём хвост текста от заголовка раздела."""
    if "Матч-апы" not in found:
        return None
    lines = text.split("\n")
    start = found["Матч-апы"][0]
    tail = "\n".join(lines[start:])
    if len(tail.split()) < 120:                 # заголовок нашёлся только в оглавлении
        tail = text
    seen, missing = [], []
    rest = tail
    for c in CLASSES:                       # составные первыми — см. комментарий у CLASSES
        pat = CLASS_PATTERNS[c]
        if re.search(rf"\b{pat}\b", rest, re.I):
            seen.append(c)
            # вычёркиваем найденное, чтобы «Охотник на демонов» не дал ещё и «Охотник»
            rest = re.sub(rf"\b{pat}\b", " ", rest, flags=re.I)
        else:
            missing.append(c)
    return seen, missing


def main():
    ap = argparse.ArgumentParser(description="Проверка структуры гайда")
    ap.add_argument("file")
    args = ap.parse_args()
    p = Path(args.file)
    if not p.exists():
        print(f"нет файла: {p}", file=sys.stderr)
        return 2

    text = p.read_text(encoding="utf-8")
    heads = headings(text)
    found = find_blocks(heads)

    print(f"\n{p.name}\nзаголовков найдено: {len(heads)}\n")
    print(f"{'блок':<14}{'':<6}{'в корпусе':>11}")
    print("-" * 40)
    missing_req = []
    for name, _, freq, required in BLOCKS:
        if name in found:
            mark, note = "есть", ""
        elif required:
            mark, note = "НЕТ", "  ← обязательный"
            missing_req.append(name)
        else:
            mark, note = "нет", "  (необязательный)"
        print(f"{name:<14}{mark:<6}{freq:>10}%{note}")

    order_now = [n for n in (b[0] for b in BLOCKS) if n in found]
    order_by_pos = sorted(order_now, key=lambda n: found[n][0])
    if order_now != order_by_pos:
        print(f"\nПОРЯДОК отличается от обычного")
        print(f"  в корпусе: {' → '.join(b[0] for b in BLOCKS)}")
        print(f"  здесь:     {' → '.join(order_by_pos)}")

    mu = check_matchups(text, heads, found)
    if mu:
        seen, missing = mu
        print(f"\nМАТЧ-АПЫ: разобрано классов {len(seen)} из {len(CLASSES)}")
        if missing:
            print(f"  не упомянуты: {', '.join(missing)}")

    print("\nИТОГ")
    if missing_req:
        print(f"  ! Нет обязательных разделов: {', '.join(missing_req)}")
        print("    В корпусе они есть почти везде — проверить, черновик это или так задумано.")
    elif mu and mu[1]:
        print("  Разделы на месте, но матч-апы покрывают не все классы.")
    else:
        print("  Структура совпадает с обычной.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
