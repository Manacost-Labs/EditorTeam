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
import datetime as _dt
import difflib
import json
import os
import re
import subprocess
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common as C  # noqa: E402

HERE, ROOT = C.SCRIPTS, C.ROOT
ASSET = C.ASSETS / "cards-ru.json"
CORPUS = C.CORPUS
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
    """Единый бутстрап .venv живёт в common."""
    C.ensure_venv("pymorphy3")


def update():
    proc = subprocess.run(["curl", "-sS", "-m", "60", SOURCE], capture_output=True, text=True)
    if proc.returncode != 0 or not proc.stdout.strip():
        print("не удалось скачать справочник", file=sys.stderr)
        return 1
    cards = json.loads(proc.stdout)
    d = json.loads(ASSET.read_text(encoding="utf-8")) if ASSET.exists() else {}
    d["карты"] = sorted({c["name"] for c in cards if c.get("name")})
    d["_источник"] = SOURCE
    d["_снято"] = _dt.date.today().isoformat()
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
        for w in re.findall(r"\b[а-яё]{3,}", C.body(f)):
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


def _text_words(text):
    """Множество слов текста — чтобы не гонять regex по всем 6602 названиям."""
    return {w.lower() for w in re.findall(r"[А-Яа-яЁёA-Za-z]{3,}", text)}


def _maybe_present(name, tw):
    first = re.findall(r"[А-Яа-яЁёA-Za-z]{3,}", name)
    return not first or first[0].lower() in tw


def check_dashes(text, names, tw=None):
    out = Counter()
    tw = tw if tw is not None else _text_words(text)
    for n in names:
        if not re.search(r"[–—-]", n) or not _maybe_present(n, tw):
            continue
        pat = re.sub(r"\\?[–—-]", r"\\s*[–—-]\\s*", re.escape(n))
        for m in re.finditer(pat, text):
            if m.group(0) != n:
                out[(" ".join(m.group(0).split()), n)] += 1
    return out


def check_caps(text, names, mech, tw=None):
    out = Counter()
    tw = tw if tw is not None else _text_words(text)
    for n in names:
        if len(n.split()) < 2 or len(n) < 11 or not _maybe_present(n, tw):
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
        # обычное слово не становится ссылкой на карту оттого, что его лемма
        # совпала с леммой слова из названия: «Играем» и «Играющая на воздухе»
        # обе дают «играть», но карта тут ни при чём
        if any(common.get(l, 0) >= 5 for l in ls):
            continue
        hit = [l for l in ls if l in idx.by_lemma]
        if hit:
            cards = idx.by_lemma[hit[0]]
            if len(cards) == 1:
                full = sorted(cards)[0]
                if w.lower() not in full.lower():
                    short[(w, full)] += 1
            continue
        for l in ls:
            near = [c for c in idx.by_lemma
                    if c[:3] == l[:3] and common.get(c, 0) < 5]   # общий корень обязателен
            g = difflib.get_close_matches(l, near, n=1, cutoff=0.86)
            if g:
                typos[(w, sorted(idx.by_lemma[g[0]])[0])] += 1
                break
    return short, typos


# Русская карта посреди предложения — это одно заглавное слово и хвост из
# строчных: «Главный канонир», «Притягивающий крюк», «Мастер брони». Имя
# кончается на служебном слове или глаголе: «Мастера брони против агро».
_TOKEN = re.compile(r"[А-Яа-яЁё'’-]+|[^\sА-Яа-яЁё'’-]+")
_STOP_TAGS = {"VERB", "INFN", "PRED", "CONJ", "PREP", "PRCL", "NPRO", "ADVB", "INTJ", "GRND", "COMP",
              "ADJS", "PRTS"}
_STOP_WORDS = {"это", "который", "которая", "которые", "весь", "каждый", "любой", "другой",
               "такой", "сам", "свой", "наш", "ваш", "его", "её", "их", "тот", "этот", "один",
               "и", "а", "но", "в", "во", "на", "с", "со", "к", "ко", "по", "за", "от", "для",
               "из", "о", "об", "у", "не", "ни", "что", "как", "или", "же", "ли", "бы", "то",
               "при", "до", "без", "под", "над", "про", "через", "уже", "ещё", "еще", "только",
               "даже", "тоже", "также", "если", "чтобы", "когда", "где", "там", "тут", "здесь",
               "очень", "так", "вот", "все", "всё", "чем", "тем", "после", "перед", "между",
               "против", "вместо", "кроме", "среди", "около", "вокруг"}
UNKNOWN_SKIP = {"стандарт", "стандартный", "легенда", "алмаз", "платина", "золото", "серебро",
                "бронза", "дикий", "классический", "формат", "поле", "сражение", "потасовка",
                "арена", "дуэль", "реддит", "ютуб", "патч", "дополнение", "мета", "топ",
                "герой", "гайд", "колода", "сборка", "раздел"}
MAX_TAIL = 2


def _is_stop(idx, word):
    """Служебное слово или глагол: на нём имя карты кончается. Берётся
    вероятнейший разбор — «и» pymorphy разбирает ещё и как аббревиатуру."""
    lw = word.lower()
    if lw in _STOP_WORDS or any(l in _STOP_WORDS for l in idx.lemmas(word)):
        return True
    parses = idx.morph.parse(word)
    return bool(parses) and parses[0].tag.POS in _STOP_TAGS


_PROPER_RE = re.compile(r"(?<![.!?…:»\"]\s)(?<!\n)(?<!^)\b([А-ЯЁ][а-яё'’-]{2,})\b(?:\s+([А-Яа-яЁё'’-]{2,}))?", re.M)
PROPER_STRONG = 30   # слово, которое автор пишет с заглавной так часто, — архетип или дополнение


def corpus_proper(idx):
    """Имена, которые автор сам пишет с заглавной посреди предложения:
    архетипы («Токен», «Бомб»), дополнения («Ярмарка безумия»), люди.
    Ключи — леммы слова и пары (лемма, лемма следующего слова). Это имена,
    которые автор знает, — новой картой они быть не могут."""
    cnt = Counter()
    if not CORPUS.exists():
        return cnt
    for f in CORPUS.glob("*.md"):
        for m in _PROPER_RE.finditer(C.body(f)):
            head = idx.lemmas(m.group(1))
            cnt.update(head)
            if m.group(2):
                tail = idx.lemmas(m.group(2))
                cnt.update((h, t) for h in head for t in tail)
    return cnt


def _known_to_author(proper, sets):
    """Одиночное слово — автор писал его с заглавной хотя бы дважды; связка —
    автор писал эту пару, или её первое слово у него частое имя (архетип)."""
    head = sets[0]
    if len(sets) == 1:
        return any(proper.get(l, 0) >= 2 for l in head)
    if any(proper.get(l, 0) >= PROPER_STRONG for l in head):
        return True
    return any(proper.get((h, t), 0) >= 2 for h in head for t in sets[1])


def unknown_names(text, idx, common, proper=None):
    """Возможно, новые карты: имена посреди предложения, которых нет ни в
    справочнике, ни среди имён, которые автор пишет с заглавной в корпусе.
    Возвращает Counter({фраза: упоминаний}).

    Это не приговор, а адрес: справочник снят на дату из `_снято`, и карта из
    свежего дополнения в нём отсутствует. Такие имена claims.py всё равно
    считает утверждениями источника, чтобы переплавка их не потеряла.
    Классы, ранги, форматы, архетипы и слова из NOT_CARDS не считаются."""
    structure = C.sibling("structure")
    classes = {w.lower() for c in structure.CLASSES for w in re.findall(r"[А-Яа-яЁё]{3,}", c)}
    proper = corpus_proper(idx) if proper is None else proper
    out = Counter()
    for line in C.prose_only(text).split("\n"):
        for sent in C.sentences(line):
            toks = [t for t in _TOKEN.findall(" ".join(sent.split()))]
            words_only = [t for t in toks if t[0].isalpha()]
            i = 0
            while i < len(toks):
                t = toks[i]
                i += 1
                if not (re.match(r"[А-ЯЁ]", t) and len(t) >= 3):
                    continue
                if sum(ch.isupper() for ch in t) >= 2:
                    continue                                    # ОТК, АоЕ, К-КЛ — аббревиатуры
                if words_only and t == words_only[0]:
                    continue                                        # первое слово предложения
                prev = toks[i - 2] if i >= 2 else ""
                if prev and not prev[0].isalpha() and prev[-1] in ".!?…:»\"—–(":
                    continue                                        # после точки, кавычки, тире, скобки
                ls = idx.lemmas(t)
                if ls & NOT_CARDS or ls & UNKNOWN_SKIP or ls & classes:
                    continue
                phrase, sets = [t], [ls]
                j = i
                while j < len(toks) and len(phrase) < 1 + MAX_TAIL:
                    nxt = toks[j]
                    if not nxt[0].isalpha() or nxt[0].isupper() or _is_stop(idx, nxt):
                        break
                    phrase.append(nxt)
                    sets.append(idx.lemmas(nxt))
                    j += 1
                # следующее заглавное слово: класс — значит, перед нами архетип
                # («Бомб Воин»), не карта; после строчного хвоста — продолжение
                # имени («Крестный отец Казакус»); сразу после головы — соседнее
                # имя, его разберёт следующий шаг
                if j < len(toks) and toks[j][0].isalpha() and toks[j][0].isupper():
                    nls = idx.lemmas(toks[j])
                    if nls & classes or nls & NOT_CARDS:
                        i = j + 1
                        continue
                    if 1 < len(phrase) < 1 + MAX_TAIL:
                        phrase.append(toks[j])
                        sets.append(nls)
                        j += 1
                # слова сложились в карту справочника — целиком или с начала
                # («Стэно коллекции»: карта «Стэно», а «коллекции» — уже фраза)
                known = None
                hits = [set().union(*(idx.by_lemma.get(l, set()) for l in st)) for st in sets]
                for k in range(len(hits), 0, -1):
                    if any(w[0].isupper() for w in phrase[k:]):
                        continue                                # «Крестного отца Казакуса» ≠ «Крестный отец Лор'темар»
                    inter = set.intersection(*hits[:k])
                    if k == 1 and len(phrase) > 1:
                        inter = {n for n in inter if len(n.split()) == 1}   # «Стэно коллекции», не «Крестный ход»
                    if inter:
                        known = inter
                        break
                if known:
                    i = j
                    continue
                if len(phrase) == 1 and any(common.get(l, 0) >= 5 for l in ls):
                    continue                                        # одиночное обычное слово с заглавной
                if _known_to_author(proper, sets):
                    i = j
                    continue                                        # автор так пишет и без новых карт
                out[" ".join(phrase)] += 1
                i = j
    return out


def snapshot_date():
    try:
        return json.loads(ASSET.read_text(encoding="utf-8")).get("_снято", "неизвестно")
    except (OSError, json.JSONDecodeError):
        return "неизвестно"


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
    idx = Index(names, C.morph())
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

    unknown = unknown_names(text, idx, common, corpus_proper(idx))
    if unknown:
        found = True
        print(f"\nВОЗМОЖНО, НОВАЯ КАРТА — нет в справочнике, снят {d.get('_снято', 'неизвестно')} "
              f"({sum(unknown.values())})")
        for name, c in unknown.most_common():
            print(f"  «{name}»" + (f"  ×{c}" if c > 1 else ""))
        print("  Название не трогать. Обновить справочник: python3 update_cards.py")

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
