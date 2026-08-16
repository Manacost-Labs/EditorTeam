#!/usr/bin/env python3
"""Внутренняя согласованность текста: не спорит ли он сам с собой.

    python3 consistency.py черновик.md
    python3 consistency.py черновик.md --строго    # показать и слабые сигналы

Проверки идут только внутри одного текста. Это не сверка с архивом
(для неё есть echo.py) и не сверка с базой карт (для неё cards.py).

Что смотрит:
  1. РАЗНОБОЙ    — одно и то же названо по-разному: «Пират Воин» и «Пират воин»,
                   «матч-ап» и «матчап». Падежи разнобоем не считаются.
  2. СОВЕТЫ      — карту в одном месте советуют оставлять, в другом сбрасывать.
  3. ЧИСЛА       — заявленное число карт против фактического перечисления.

Первая проверка надёжная, вторая — подсказка без вердикта: разбирать смысл
советов автоматически нельзя, там легко наврать. Третья срабатывает редко.
"""

import argparse
import re
import sys
from collections import Counter, defaultdict
from functools import lru_cache
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common as C  # noqa: E402

# семьи терминов: разное написание одного и того же
TERM_FAMILIES = [
    ("матч-ап", [r"матч-?\s?ап\w*"]),
    ("муллиган", [r"мул+иган\w*", r"малиган\w*"]),
    ("декбилдинг", [r"дек-?\s?билдинг\w*", r"дэкбилдинг\w*"]),
    ("топдек", [r"топ-?\s?дек\w*"]),
    ("лейтгейм", [r"лейт-?\s?гейм\w*", r"лэйтгейм\w*"]),
    ("мидгейм", [r"мид-?\s?гейм\w*"]),
    ("хайлендер", [r"хай[лл]?[еэа]ндер\w*"]),
    ("ремувал", [r"[рp]e?[еи]мувал\w*"]),
    ("винрейт", [r"вин-?\s?р[еэ]йт\w*"]),
    ("ОТК", [r"\bОТ[КC]\b", r"\bотк\b"]),
]

ARCHETYPE = (r"(Бомб|Квест|Агро|Биг|Миракл|Контроль|Рамп|Токен|Фейс|Спелл|Секрет|Мех|"
             r"Мурлок|Пират|Зоо|Раш|Темпо|Андед|Фрост|Анхоли|Элем|Шадоу|Нечетн\w+|Нечётн\w+)"
             r"[\s-]+"
             r"(Воин\w*|Маг\w*|Жрец\w*|Жреца|Друид\w*|Паладин\w*|Разбойник\w*|Шаман\w*|"
             r"Охотник\w*|Чернокнижник\w*|ДК|Хант\w*|Лок\w*|Прист\w*|Рог[аеиу])")

KEEP = r"(оставля\w+|оставить|оставь\w*|держ\w+|ищ[еи]\w+|берит[еь]|нужн\w+\s+в\s+руке)"
DROP = r"(сбрас\w+|скид\w+|не\s+оставля\w+|не\s+держ\w+|избега\w+|не\s+берит[еь]|не\s+нужн\w+)"


def norm_space(s):
    return " ".join(s.split())


@lru_cache(maxsize=1)
def _corpus_lower():
    """Текст архива строчными — чтобы отличить имя карты от обычной фразы."""
    return C.corpus_text().lower() if C.corpus_files() else ""


@lru_cache(maxsize=20_000)
def is_common_phrase(name):
    """«Из глубин» — и карта, и обычный оборот речи.

    Если строчное написание часто встречается в архиве, разнобой регистра
    здесь ничего не значит: это не имя, а слова.
    """
    low = _corpus_lower()
    return low.count(name.lower()) >= 5 if low else False


@lru_cache(maxsize=1)
def section_names():
    """Названия разделов гайда: их регистр задан структурой, не прозой."""
    try:
        st = C.sibling("structure")
    except Exception:
        return frozenset()
    return frozenset(v for _, variants, _, _ in st.BLOCKS for v in variants)


def mask_quoted(text):
    """Гасит содержимое кавычек.

    «Дополнены разделы "Муллиган", "Матч-апы"» — это цитируемые названия
    разделов, их регистр задан оформлением, а не написанием в прозе.
    """
    return re.sub(r'["«“][^"»”\n]{2,40}["»”]',
                  lambda m: " " * len(m.group(0)), text)


def mask_headings(text):
    """Гасит заголовки разделов.

    В заголовке заглавная — правило оформления, а не выбор написания:
    иначе раздел «Матч-апы» считается разнобоем с «матч-апах» в тексте.
    """
    def blank(m):
        line = m.group(0)
        body = line.strip()
        if (body.startswith("#")
                or (3 <= len(body) <= 40 and 1 <= len(body.split()) <= 5
                    and body[:1].isupper() and not body.endswith((".", "!", "?", ",")))):
            return " " * len(line)
        return line

    return re.sub(r"^.*$", blank, text, flags=re.MULTILINE)


def sentence_starts(text):
    """Смещения, с которых начинается предложение.

    Заглавная буква там — правило языка, а не авторский выбор написания.
    Без этого «Матч-апы» в начале фразы считались разнобоем с «матч-апах».
    """
    out = {0}
    for m in re.finditer(r"(?:[.!?…:]|\n)\s*", text):
        out.add(m.end())
    return out


def collect(text, pattern, starts):
    """Написания и то, стояло ли слово в начале предложения.

    В начале фразы заглавная — правило языка, а не выбор автора, поэтому
    регистр первого слова там ничего не доказывает.
    """
    mid, head_ = Counter(), Counter()
    for m in re.finditer(pattern, text, re.I):
        s = norm_space(m.group(0)).replace("- ", "-")   # склейка переноса строки
        (head_ if m.start() in starts else mid)[s] += 1
    return mid, head_


def shape(form):
    """«Отпечаток» написания без падежа: дефис, пробел, регистр каждого слова.

    «матч-апах» и «матч-апы» дают один отпечаток — это падежи, не разнобой.
    «матч-ап» и «матчап» — разные, это уже разнобой.
    """
    seps = tuple(re.findall(r"[\s-]", form))
    caps = tuple(w[:1].isupper() for w in re.split(r"[\s-]+", form) if w)
    return seps, caps


def group_by_lemma(forms):
    """{лемма: Counter(написание)} — падежи одного слова попадают в одну группу."""
    groups = defaultdict(Counter)
    for form, c in forms.items():
        groups[C.lemma_key(form)][form] += c
    return groups


def variants_in(forms):
    """Разные отпечатки написания внутри одной леммы = разнобой."""
    by_shape = defaultdict(Counter)
    for form, c in forms.items():
        by_shape[shape(form)][form] += c
    return by_shape if len(by_shape) > 1 else None


def report_shapes(label, mid, head_, found, strict=False):
    """Разнобой засчитывается по написаниям в середине фразы.

    Написание в начале предложения учитывается, только если отличается
    не первым словом — иначе это обычная заглавная после точки.
    """
    by_shape = defaultdict(Counter)
    for form, c in mid.items():
        by_shape[shape(form)][form] += c

    if len(by_shape) <= 1 and head_:
        base = next(iter(by_shape), None)
        for form, c in head_.items():
            sh = shape(form)
            if base is None:
                continue
            differs = sh[0] != base[0] or sh[1][1:] != base[1][1:]
            if differs:
                by_shape[sh][form] += c

    if len(by_shape) > 1:
        top = Counter({fs.most_common(1)[0][0]: sum(fs.values())
                       for fs in by_shape.values()})
        variants = top.most_common(4)
        # слабый сигнал: каждый вариант встретился ровно раз — вероятна описка,
        # а не разное написание. В обычном режиме такие не показываем
        weak = all(c == 1 for _, c in variants)
        if weak and not strict:
            return
        found.append((label, variants))


def check_variants(text, strict=False):
    """Одно и то же написано по-разному. Падежи разнобоем не считаются.

    strict=True добавляет слабые сигналы: одиночные расхождения, где вариант
    встретился один раз и мог быть опиской, а не системой. По умолчанию они
    молчат, чтобы обычный прогон не тонул в шуме.
    """
    found = []
    text = mask_headings(mask_quoted(text))
    starts = sentence_starts(text)

    # 1. Термины: «матч-ап» против «матчап»
    for canon, pats in TERM_FAMILIES:
        mid, head_ = Counter(), Counter()
        for p in pats:
            m2, h2 = collect(text, p, starts)
            mid.update(m2)
            head_.update(h2)
        for bucket in (mid, head_):
            for f in [x for x in bucket if x.lower() in
                      {s.lower() for s in section_names()}]:
                del bucket[f]
        report_shapes(canon, mid, head_, found, strict)

    # 2. Архетипы: «Пират Воин» против «Пират воин».
    #    Ищем без учёта регистра — иначе строчный вариант не находится вовсе.
    mid, head_ = collect(text, ARCHETYPE, starts)
    for group in (mid, head_):
        for k in [f for f in group if "\n" in f]:     # перенос строки — след вёрстки
            del group[k]
    lem_mid, lem_head = group_by_lemma(mid), group_by_lemma(head_)
    for key, group in lem_mid.items():
        report_shapes("архетип", group, lem_head.get(key, Counter()), found, strict)

    # 3. Карты: одно имя, разное написание в пределах текста
    for n in C.card_db()["карты"]:
        if len(n) < 8 or is_common_phrase(n):
            continue
        mid, head_ = collect(text, re.escape(n), starts)
        if mid or head_:
            report_shapes(f"карта «{n}»", mid, head_, found, strict)

    return found


def lemma_set(s):
    out = set()
    for w in re.findall(r"[А-Яа-яЁёA-Za-z'’-]{3,}", s):
        out |= C.lemmas(w.lower())
    return out


def check_advice(text):
    """Карта попала и в «оставлять», и в «сбрасывать». Только подсказка.

    Совпадение по леммам, а не по строке: «оставляйте Мастера брони»
    должно находить карту «Мастер брони».
    """
    doc = lemma_set(text)
    # карты, все значимые слова которых вообще есть в тексте
    cand = []
    for n in C.card_db()["карты"]:
        # однословные имена совпадают со случайными словами: «К оружию!» ловит
        # любое упоминание оружия. Нужно минимум два значимых слова.
        if len(n) < 8 or len(re.findall(r"[А-Яа-яЁёA-Za-z'’-]{3,}", n)) < 2:
            continue
        need = {sorted(C.lemmas(w.lower()))[0]
                for w in re.findall(r"[А-Яа-яЁёA-Za-z'’-]{3,}", n)}
        if need and all(any(l in doc for l in C.lemmas(w)) for w in need):
            cand.append((n, need))
    if not cand:
        return {}

    keep_re, drop_re = re.compile(KEEP, re.I), re.compile(DROP, re.I)
    stance = defaultdict(lambda: {"оставлять": [], "сбрасывать": []})
    for s in C.sentences(text):
        has_drop = bool(drop_re.search(s))
        if not (has_drop or keep_re.search(s)):
            continue
        sl = lemma_set(s)
        for n, need in cand:
            if all(any(l in sl for l in C.lemmas(w)) for w in need):
                bucket = "сбрасывать" if has_drop else "оставлять"
                stance[n][bucket].append(norm_space(s)[:130])
    return {n: v for n, v in stance.items() if v["оставлять"] and v["сбрасывать"]}


def check_counts(text):
    """«В основе останется 19 карт» — сверить с числом перечисленных."""
    out = []
    for m in re.finditer(r"(основ\w+|остан\w+|составля\w+)[^.]{0,40}?(\d{1,2})\s+карт", text, re.I):
        claim = int(m.group(2))
        tail = text[m.end():m.end() + 1500]
        listed = len({c for c in C.card_db()["карты"] if len(c) > 6 and c in tail})
        if listed and abs(listed - claim) > 2:
            out.append((norm_space(m.group(0)), claim, listed))
    return out


def main():
    ap = argparse.ArgumentParser(description="Согласованность внутри текста")
    ap.add_argument("file")
    ap.add_argument("--строго", dest="strict", action="store_true",
                    help="показать и слабые сигналы: одиночные расхождения")
    args = ap.parse_args()

    p = Path(args.file)
    if not p.exists():
        print(f"нет файла: {p}", file=sys.stderr)
        return 2
    text = C.mask_protected(p.read_text(encoding="utf-8"))

    variants = check_variants(text, args.strict)
    advice = check_advice(text)
    counts = check_counts(text)

    print(f"\n{p.name}")

    if variants:
        C.head("РАЗНОБОЙ В НАПИСАНИИ", len(variants),
               "   — надёжно, чинить")
        for what, forms in variants:
            shown = ", ".join(f"«{f}» ×{c}" for f, c in forms)
            print(f"  {what}: {shown}")

    if advice:
        C.head("СПОРНЫЕ СОВЕТЫ", len(advice), "   — подсказка, не вердикт")
        for n, v in list(advice.items())[:6]:
            print(f"  {n}")
            print(f"    оставлять:  …{v['оставлять'][0]}…")
            print(f"    сбрасывать: …{v['сбрасывать'][0]}…")

    if counts:
        C.head("ЧИСЛО НЕ СХОДИТСЯ", len(counts))
        for claim, said, listed in counts:
            print(f"  «{claim}» — а перечислено {listed}")

    if not (variants or advice or counts):
        print("\nПроверяемых расхождений не найдено.")
        print("Проверены три типа: написание, советы по картам, число карт.")
        print("Автоматическая проверка не заменяет смысловую вычитку.")
        return 0

    C.hint("Разнобой — механическая правка, её можно вносить сразу.",
           "Спорные советы автоматически не разбираются: возможно, речь о разных",
           "матч-апах или стадиях игры. Смотреть глазами.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
