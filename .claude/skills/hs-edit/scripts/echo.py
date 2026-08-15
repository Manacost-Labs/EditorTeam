#!/usr/bin/env python3
"""Перекличка с архивом: где автор уже писал об этом.

    python3 echo.py черновик.md              — по всему черновику
    python3 echo.py черновик.md --абзац 3    — по конкретному абзацу
    python3 echo.py --текст "муллиган против агро"

Редактор, который прочитал все 49 гайдов, помнит: «про Баку ты писал в гайде
по Нечетному Охотнику, там формулировка была другая». Этот скрипт делает то же
самое — находит места в архиве, где речь шла о том же.

Зачем при правке:
  * не переписывать заново то, что уже сформулировано лучше;
  * заметить, если новый текст противоречит старому;
  * держать одинаковые вещи названными одинаково.

Скрипт ничего не предлагает вставить. Он показывает, что было, — решает редактор.
"""

import argparse
import math
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

# scripts -> hs-edit -> skills -> .claude -> корень проекта
ROOT = Path(__file__).resolve().parents[4]
CORPUS = ROOT / "гайды"

STOP = set("""и в во не что он на я с со как а то все она так его но да ты к у же вы за бы
по только ее мне было вот от меня еще нет о из ему теперь когда даже ну вдруг ли если уже или
ни быть был него до вас нибудь опять уж вам ведь там потом себя ничего ей может они тут где
есть надо ней для мы тебя их чем была сам чтоб без будто чего раз тоже себе под будет ж тогда
кто этот того потому этого какой совсем ним здесь этом один почти мой тем чтобы нее сейчас
были куда зачем всех никогда можно при наконец два об другой хоть после над больше тот через
эти нас про всего них какая много разве три эту моя впрочем хорошо свою этой перед иногда
лучше чуть том нельзя такой им более всегда конечно всю между это очень""".split())


def words(text):
    return [w for w in re.findall(r"[а-яёa-z]{3,}", text.lower()) if w not in STOP]


def paragraphs(text, min_words=25):
    out = []
    for p in re.split(r"\n\s*\n|\n(?=[А-ЯЁ])", text):
        p = " ".join(p.split())
        if len(p.split()) >= min_words:
            out.append(p)
    return out


class Archive:
    """Обратный индекс по абзацам архива с весами IDF: редкие слова важнее частых."""

    def __init__(self):
        self.docs = []            # (имя гайда, текст абзаца, Counter слов)
        self.df = Counter()
        for f in sorted(CORPUS.glob("*.md")):
            name = re.sub(r"^\d+_", "", f.stem)[:52]
            for p in paragraphs(f.read_text(encoding="utf-8")):
                tf = Counter(words(p))
                if not tf:
                    continue
                self.docs.append((name, p, tf))
                self.df.update(tf.keys())
        self.n = len(self.docs)
        self.idf = {w: math.log(1 + self.n / c) for w, c in self.df.items()}
        self.index = defaultdict(list)
        for i, (_, _, tf) in enumerate(self.docs):
            for w in tf:
                self.index[w].append(i)

    def search(self, query, top=3, skip_same=None):
        qtf = Counter(words(query))
        if not qtf:
            return []
        # редкие слова запроса решают: названия карт и архетипов, а не «колода»
        terms = sorted(qtf, key=lambda w: -self.idf.get(w, 0))[:18]
        score = Counter()
        rare_shared = Counter()
        for w in terms:
            iw = self.idf.get(w)
            # слово должно быть действительно редким: не чаще чем в 5% абзацев,
            # иначе совпадают «небольшое количество» и «колода», а не суть
            if not iw or iw < 3.0:
                continue
            for i in self.index.get(w, ()):
                score[i] += (iw ** 2) * min(qtf[w], 2)
                rare_shared[i] += 1
        out = []
        for i, s in score.most_common(top * 6):
            # одно общее редкое слово — случайность, нужно минимум два
            if rare_shared[i] < 2:
                continue
            name, txt, tf = self.docs[i]
            norm = s / math.sqrt(sum(tf.values()))
            if skip_same and skip_same in txt:
                continue
            out.append((norm, name, txt))
        out.sort(key=lambda x: -x[0])
        return out[:top]


def main():
    ap = argparse.ArgumentParser(description="Что автор писал об этом раньше")
    ap.add_argument("file", nargs="?")
    ap.add_argument("--текст", dest="text")
    ap.add_argument("--абзац", dest="para", type=int)
    ap.add_argument("-n", type=int, default=2, help="находок на абзац")
    args = ap.parse_args()

    if not CORPUS.exists():
        print(f"нет корпуса: {CORPUS}", file=sys.stderr)
        return 2

    if args.text:
        queries = [args.text]
    elif args.file:
        p = Path(args.file)
        if not p.exists():
            print(f"нет файла: {p}", file=sys.stderr)
            return 2
        queries = paragraphs(p.read_text(encoding="utf-8"), min_words=12)
        if args.para is not None:
            if args.para < 1 or args.para > len(queries):
                print(f"в тексте {len(queries)} абзацев", file=sys.stderr)
                return 2
            queries = [queries[args.para - 1]]
    else:
        ap.error("нужен файл или --текст")

    arch = Archive()
    print(f"архив: {arch.n} абзацев из {len(list(CORPUS.glob('*.md')))} гайдов")

    shown = 0
    for k, q in enumerate(queries, 1):
        hits = arch.search(q, top=args.n)
        hits = [h for h in hits if h[0] > 3.0]
        if not hits:
            continue
        shown += 1
        head = " ".join(q.split()[:11])
        print(f"\n─── абзац {k}: «{head}…»")
        for sc, name, txt in hits:
            snippet = txt if len(txt) < 420 else txt[:400] + "…"
            print(f"\n  ▸ {name}   (близость {sc:.2f})")
            print(f"    {snippet}")

    if not shown:
        print("\nПохожего в архиве не нашлось — тема новая.")
    else:
        print(f"\n\nНайдено по {shown} абзацам из {len(queries)}.")
        print("Это не образец для копирования: смотреть, не противоречит ли новый текст старому")
        print("и одинаково ли названы одинаковые вещи.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
