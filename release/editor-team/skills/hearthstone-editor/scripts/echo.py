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

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common as C  # noqa: E402

STOP = set(
    """и в во не что он на я с со как а то все она так его но да ты к у же вы за бы
по только ее мне было вот от меня еще нет о из ему теперь когда даже ну вдруг ли если уже или
ни быть был него до вас нибудь опять уж вам ведь там потом себя ничего ей может они тут где
есть надо ней для мы тебя их чем была сам чтоб без будто чего раз тоже себе под будет ж тогда
кто этот того потому этого какой совсем ним здесь этом один почти мой тем чтобы нее сейчас
были куда зачем всех никогда можно при наконец два об другой хоть после над больше тот через
эти нас про всего них какая много разве три эту моя впрочем хорошо свою этой перед иногда
лучше чуть том нельзя такой им более всегда конечно всю между это очень""".split()
)


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
        self.docs = []  # (имя, абзац, Counter, metadata)
        self.df = Counter()
        for f, meta, body in C.corpus_records():
            name = re.sub(r"^\d+_", "", f.stem)[:52]
            for p in paragraphs(body):
                tf = Counter(words(p))
                if not tf:
                    continue
                self.docs.append((name, p, tf, meta))
                self.df.update(tf.keys())
        self.n = len(self.docs)
        self.idf = {w: math.log(1 + self.n / c) for w, c in self.df.items()}
        self.index = defaultdict(list)
        for i, (_, _, tf, _) in enumerate(self.docs):
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
                score[i] += (iw**2) * min(qtf[w], 2)
                rare_shared[i] += 1
        out = []
        for i, s in score.most_common(top * 6):
            # одно общее редкое слово — случайность, нужно минимум два
            if rare_shared[i] < 2:
                continue
            name, txt, tf, meta = self.docs[i]
            norm = s / math.sqrt(sum(tf.values()))
            if skip_same and skip_same in txt:
                continue
            out.append((norm, name, txt, meta))
        out.sort(key=lambda x: -x[0])
        return out[:top]


def main():
    ap = argparse.ArgumentParser(description="Что автор писал об этом раньше")
    ap.add_argument("file", nargs="?")
    ap.add_argument("--текст", dest="text")
    ap.add_argument("--абзац", dest="para", type=int)
    ap.add_argument("-n", type=int, default=2, help="находок на абзац")
    ap.add_argument("--current-patch", help="патч нового гайда")
    ap.add_argument("--current-meta-epoch", help="meta epoch нового гайда")
    args = ap.parse_args()

    corpus_files = C.corpus_files()
    if not corpus_files:
        print("нет approved style corpus", file=sys.stderr)
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
    print(f"архив: {arch.n} абзацев из {len(corpus_files)} гайдов")
    print("STYLE ONLY — архив не подтверждает актуальную стратегию")

    shown = 0
    for k, q in enumerate(queries, 1):
        hits = arch.search(q, top=args.n)
        hits = [h for h in hits if h[0] > 3.0]
        if not hits:
            continue
        shown += 1
        head = " ".join(q.split()[:11])
        print(f"\n─── абзац {k}: «{head}…»")
        for sc, name, txt, meta in hits:
            snippet = txt if len(txt) < 420 else txt[:400] + "…"
            published = meta.get("published_at", "unknown")
            patch = meta.get("patch", "unknown")
            historical = patch == "unknown" or (args.current_patch and patch != args.current_patch)
            label = "  HISTORICAL CONTENT" if historical else ""
            print(f"\n  ▸ {name}   {published}   patch {patch}   (близость {sc:.2f}){label}")
            print(f"    {snippet}")

    if not shown:
        print("\nПохожего в архиве не нашлось — тема новая.")
    else:
        print(f"\n\nНайдено по {shown} абзацам из {len(queries)}.")
        print("Берите отсюда только стиль, терминологию и формулировку.")
        print("Фактический совет должен прийти из current evidence для текущих patch/meta epoch.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
