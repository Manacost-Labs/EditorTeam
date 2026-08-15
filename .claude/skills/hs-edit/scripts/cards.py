#!/usr/bin/env python3
"""Сверка названий карт с официальной русской локализацией — с морфологией.

    python3 cards.py текст.md
    python3 cards.py текст.md --формы        # показать распознанные короткие имена
    python3 cards.py --обновить              # перекачать справочник карт

Правило «названия карт не трогать» защищает от догадок, но не от опечаток.
Здесь третий путь: сверка с официальным списком плюс морфологический разбор,
чтобы падежи и короткие имена не считались ошибками.

Что понимает:
  * падежные формы — «Балинды», «Бранном», «Рисковым»;
  * короткие имена — «Балинда» вместо «Балинда Каменный Очаг»,
    «Монетка» вместо «Фальшивая монетка», «Самуро» вместо «Мастер клинка Самуро»;
  * сложные имена с апострофом и дефисом — «Зул'джин», «Алдор-служительница».

Что проверяет:
  1. АПОСТРОФ  — «Зул’джин» и «КелТузад» вместо «Зул'джин» и «Кел'Тузад»
  2. ТИРЕ      — дефис вместо тире внутри имени
  3. РЕГИСТР   — в многословных именах
  4. ОПЕЧАТКА  — слово похоже на карту, но не совпадает ни с одной формой

Ничего не исправляет само: показывает расхождения, решает редактор.
"""

import argparse
import difflib
import json
import os
import re
import subprocess
import sys
from collections import Counter, defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
ASSET = HERE.parent / "assets" / "cards-ru.json"
CORPUS = ROOT / "гайды"
SOURCE = "https://api.hearthstonejson.com/v1/latest/ruRU/cards.collectible.json"

# слова классов, типов существ и архетипов: пишутся с заглавной, но картами не являются
NOT_CARDS = {
    "разбойник", "чернокнижник", "охотник", "маг", "жрец", "друид", "паладин", "воин",
    "шаман", "рыцарь", "смерть", "демон", "фейс", "рамп", "бист", "андед", "зоолок",
    "хант", "квест", "мидрейндж", "агро", "контроль", "комбо", "прист", "рога", "лок",
    "мурлок", "пират", "механизм", "элементаль", "нежить", "дракон", "гнолл", "мех",
    "стандарт", "легенда", "арена", "потасовка", "поле", "битва", "таверна",
}


def ensure_pymorphy():
    """pymorphy3 живёт в .venv проекта — при нужде перезапускаемся оттуда."""
    try:
        import pymorphy3  # noqa: F401
        return
    except ImportError:
        pass
    venv = ROOT / ".venv" / "bin" / "python"
    if venv.exists() and not os.environ.get("_CARDS_REEXEC"):
        os.environ["_CARDS_REEXEC"] = "1"
        os.execv(str(venv), [str(venv), str(Path(__file__).resolve())] + sys.argv[1:])
    print("нужен pymorphy3:\n  .venv/bin/pip install pymorphy3 pymorphy3-dicts-ru",
          file=sys.stderr)
    sys.exit(2)


def update():
    proc = subprocess.run(["curl", "-sS", "-m", "60", SOURCE], capture_output=True, text=True)
    if proc.returncode != 0 or not proc.stdout.strip():
        print("не удалось скачать справочник", file=sys.stderr)
        return 1
    cards = json.loads(proc.stdout)
    d = json.loads(ASSET.read_text(encoding="utf-8")) if ASSET.exists() else {}
    d["карты"] = sorted({c["name"] for c in cards if c.get("name")})
    d["_источник"] = SOURCE
    ASSET.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
    print(f"обновлено: {len(d['карты'])} названий")
    return 0


def norm_apo(s):
    """Все апострофы к одному виду, и версия совсем без них."""
    one = re.sub(r"[''`’ʼ]", "'", s)
    return one, one.replace("'", "")


class Index:
    def __init__(self, names, morph):
        self.names = names
        self.morph = morph
        self._cache = {}
        self.by_lemma = defaultdict(set)
        self.apo = {}
        for n in names:
            for w in re.findall(r"[А-Яа-яЁёA-Za-z'’-]{3,}", n):
                for l in self.lemmas(w):
                    self.by_lemma[l].add(n)
            one, bare = norm_apo(n)
            if "'" in one:
                self.apo.setdefault(bare.lower(), n)
                self.apo.setdefault(one.lower(), n)

    def lemmas(self, w):
        """Все разборы слова, а не только вероятнейший: «Бранном» — и «бранный», и «бранн»."""
        if w not in self._cache:
            self._cache[w] = {p.normal_form for p in self.morph.parse(w)} | {w.lower()}
        return self._cache[w]


def corpus_common(idx):
    """Леммы, которые часто встречаются со строчной буквы, — обычные слова, не имена."""
    cnt = Counter()
    if not CORPUS.exists():
        return cnt
    for f in CORPUS.glob("*.md"):
        for w in re.findall(r"\b[а-яё]{3,}", f.read_text(encoding="utf-8")):
            cnt.update(idx.lemmas(w))
    return cnt


def check_apostrophes(text, idx):
    """Имя написано кривым апострофом или без него. Проверка точная, без догадок."""
    out = Counter()
    for m in re.finditer(r"\b[А-ЯЁA-Z][А-Яа-яЁёA-Za-z''`’ʼ-]{3,}\b", text):
        got = m.group(0)
        one, bare = norm_apo(got)
        official = idx.apo.get(bare.lower()) or idx.apo.get(one.lower())
        if official and got != official:
            # падежная форма официального имени — не ошибка, если апостроф на месте
            if "'" in got and one == got:
                continue
            out[(got, official)] += 1
    return out


def check_dashes(text, names):
    out = Counter()
    for n in names:
        if not re.search(r"[–—-]", n):
            continue
        pat = re.sub(r"\\?[–—-]", r"\\s*[–—-]\\s*", re.escape(n))
        for m in re.finditer(pat, text):
            if m.group(0) != n:
                out[(" ".join(m.group(0).split()), n)] += 1
    return out


def check_caps(text, names, mech):
    out = Counter()
    for n in names:
        if len(n.split()) < 2 or len(n) < 11:
            continue
        for m in re.finditer(re.escape(n), text, re.I):
            if m.group(0) != n:
                out[(m.group(0), n, m.group(0).lower() in mech)] += 1
    return out


def scan_words(text, idx, common):
    """Заглавные слова: что распозналось как карта, что похоже на неё, что мимо."""
    short, typos = Counter(), Counter()
    for m in re.finditer(r"(?<![.!?…]\s)(?<!\n)\b([А-ЯЁ][а-яёА-ЯЁ''’-]{2,})\b", text):
        w = m.group(1)
        ls = idx.lemmas(w)
        if ls & NOT_CARDS:
            continue
        hit = [l for l in ls if l in idx.by_lemma]
        if hit:
            cards = idx.by_lemma[hit[0]]
            if len(cards) == 1:
                full = sorted(cards)[0]
                if w.lower() not in full.lower():
                    short[(w, full)] += 1
            continue
        if any(common.get(l, 0) >= 5 for l in ls):
            continue
        for l in ls:
            near = [c for c in idx.by_lemma
                    if c[:3] == l[:3] and common.get(c, 0) < 5]   # общий корень обязателен
            g = difflib.get_close_matches(l, near, n=1, cutoff=0.86)
            if g:
                typos[(w, sorted(idx.by_lemma[g[0]])[0])] += 1
                break
    return short, typos


def main():
    ap = argparse.ArgumentParser(description="Сверка названий карт с локализацией")
    ap.add_argument("file", nargs="?")
    ap.add_argument("--обновить", dest="upd", action="store_true")
    ap.add_argument("--формы", dest="forms", action="store_true",
                    help="показать распознанные короткие имена")
    args = ap.parse_args()

    if args.upd:
        return update()
    if not args.file:
        ap.error("нужен файл или --обновить")
    p = Path(args.file)
    if not p.exists():
        print(f"нет файла: {p}", file=sys.stderr)
        return 2

    ensure_pymorphy()
    import pymorphy3

    d = json.loads(ASSET.read_text(encoding="utf-8"))
    names, mech = d["карты"], set(d.get("механики", []))
    idx = Index(names, pymorphy3.MorphAnalyzer())
    common = corpus_common(idx)

    text = p.read_text(encoding="utf-8")
    apo = check_apostrophes(text, idx)
    dashes = check_dashes(text, names)
    caps = check_caps(text, names, mech)
    short, typos = scan_words(text, idx, common)
    # апостроф уже отчитан точной проверкой — не показывать то же самое как догадку
    reported = {was for was, _ in apo}
    typos = Counter({k: v for k, v in typos.items() if k[0] not in reported})

    found = False
    for title, data, fmt in (
        ("АПОСТРОФ В НАЗВАНИИ", apo, None),
        ("ТИРЕ В НАЗВАНИИ", dashes, None),
    ):
        if data:
            found = True
            print(f"\n{title} ({sum(data.values())})")
            for (was, off), c in data.most_common():
                print(f"  «{was}» → «{off}»" + (f"  ×{c}" if c > 1 else ""))

    if caps:
        found = True
        print(f"\nРЕГИСТР В НАЗВАНИИ ({sum(caps.values())})")
        for (was, off, is_mech), c in caps.most_common():
            tail = f"  ×{c}" if c > 1 else ""
            if is_mech:
                tail += "   ← может быть механика, а не карта: строчная тогда верна"
            print(f"  «{was}» → «{off}»{tail}")

    if typos:
        found = True
        print(f"\nПОХОЖЕ НА ОПЕЧАТКУ ({sum(typos.values())})")
        for (was, off), c in typos.most_common():
            print(f"  «{was}» ≈ «{off}»" + (f"  ×{c}" if c > 1 else ""))
        print("  Сверка нечёткая — проверить глазами.")

    if args.forms and short:
        print(f"\nКОРОТКИЕ ИМЕНА — распознаны, это не ошибка ({sum(short.values())})")
        for (w, full), c in short.most_common(25):
            print(f"  «{w}» → «{full}»" + (f"  ×{c}" if c > 1 else ""))

    if not found:
        print(f"{p.name}: названия карт совпадают с локализацией.")
        if short:
            print(f"  распознано коротких имён: {sum(short.values())}"
                  f" (показать: --формы)")
        return 0

    print("\n  Это расхождения с локализацией, а не приговор.")
    print("  Названо так сознательно — оставить и не возвращаться к вопросу.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
