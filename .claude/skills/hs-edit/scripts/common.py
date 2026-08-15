#!/usr/bin/env python3
"""Общая основа для всех проверок.

Здесь живёт то, что иначе копируется из скрипта в скрипт и разъезжается:
пути, запуск в venv, чтение корпуса, разбиение текста, справочник карт.

Как добавить новую проверку — см. README.md рядом.
"""

import importlib.util
import json
import os
import re
import sys
from collections import Counter
from functools import lru_cache
from pathlib import Path

# ── Пути ──────────────────────────────────────────────────────────────────
# scripts -> hs-edit -> skills -> .claude -> корень проекта.
# Считается один раз здесь: раньше каждый скрипт считал сам и дважды ошибся.
SCRIPTS = Path(__file__).resolve().parent
SKILL = SCRIPTS.parent
ASSETS = SKILL / "assets"
ROOT = SCRIPTS.parents[3]
CORPUS = ROOT / "гайды"
VENV_PY = ROOT / ".venv" / "bin" / "python"


def ensure_venv(module="pymorphy3"):
    """Перезапустить себя из .venv, если нужной библиотеки нет в системном Python."""
    try:
        __import__(module)
        return
    except ImportError:
        pass
    flag = f"_REEXEC_{Path(sys.argv[0]).stem.upper()}"
    if VENV_PY.exists() and not os.environ.get(flag):
        os.environ[flag] = "1"
        script = str(Path(sys.argv[0]).resolve())
        os.execv(str(VENV_PY), [str(VENV_PY), script] + sys.argv[1:])
    print(f"нужен {module}:\n  .venv/bin/pip install pymorphy3 pymorphy3-dicts-ru",
          file=sys.stderr)
    sys.exit(2)


def sibling(name):
    """Подгрузить соседний скрипт как модуль."""
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ── Текст ─────────────────────────────────────────────────────────────────

STOP = set("""и в во не что он на я с со как а то все она так его но да ты к у же вы за бы
по только ее мне было вот от меня еще нет о из ему теперь когда даже ну вдруг ли если уже или
ни быть был него до вас нибудь опять уж вам ведь там потом себя ничего ей может они тут где
есть надо ней для мы тебя их чем была сам чтоб без будто чего раз тоже себе под будет ж тогда
кто этот того потому этого какой совсем ним здесь этом один почти мой тем чтобы нее сейчас
были куда зачем всех никогда можно при наконец два об другой хоть после над больше тот через
эти нас про всего них какая много разве три эту моя впрочем хорошо свою этой перед иногда
лучше чуть том нельзя такой им более всегда конечно всю между это очень""".split())


def sentences(text):
    return [s for s in re.split(r"(?<=[.!?…])\s+", text) if len(s.split()) > 1]


def paragraphs(text, min_words=25):
    out = []
    for p in re.split(r"\n\s*\n|\n(?=[А-ЯЁ])", text):
        p = " ".join(p.split())
        if len(p.split()) >= min_words:
            out.append(p)
    return out


def words(text, drop_stop=True):
    ws = re.findall(r"[а-яёa-z]{3,}", text.lower())
    return [w for w in ws if w not in STOP] if drop_stop else ws


def mask_protected(text):
    """Гасит код, цитаты, ссылки и коды колод, сохраняя смещения символов."""
    def blank(m):
        return re.sub(r"\S", " ", m.group(0))

    text = re.sub(r"```.*?```", blank, text, flags=re.DOTALL)
    text = re.sub(r"`[^`\n]+`", blank, text)
    text = re.sub(r"^>.*$", blank, text, flags=re.MULTILINE)
    text = re.sub(r"https?://\S+", blank, text)
    text = re.sub(r"^\s*AAECA\S+\s*$", blank, text, flags=re.MULTILINE)
    return text


# ── Корпус ────────────────────────────────────────────────────────────────

def corpus_files():
    return sorted(CORPUS.glob("*.md")) if CORPUS.exists() else []


def guide_name(path):
    return re.sub(r"^\d+_", "", Path(path).stem)


@lru_cache(maxsize=1)
def corpus_text():
    return "\n".join(f.read_text(encoding="utf-8") for f in corpus_files())


# ── Карты ─────────────────────────────────────────────────────────────────

@lru_cache(maxsize=1)
def card_db():
    """{'карты': [...], 'механики': [...]} из справочника локализации."""
    p = ASSETS / "cards-ru.json"
    if not p.exists():
        print(f"нет справочника карт: {p}\n  обновить: python3 cards.py --обновить",
              file=sys.stderr)
        sys.exit(2)
    return json.loads(p.read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def morph():
    ensure_venv("pymorphy3")
    import pymorphy3
    return pymorphy3.MorphAnalyzer()


@lru_cache(maxsize=200_000)
def lemmas(word):
    """Все разборы слова, а не только вероятнейший.

    «Бранном» — это и «бранный», и «бранн»; если брать первый разбор,
    имя карты теряется. На этом уже один раз сломалась сверка карт.
    """
    return frozenset({p.normal_form for p in morph().parse(word)} | {word.lower()})


def lemma_key(phrase):
    """Ключ фразы без падежей: «Пират Воин» и «Пирату Воину» дают одно."""
    parts = re.findall(r"[А-Яа-яЁёA-Za-z'’-]{2,}", phrase.lower())
    return tuple(sorted(lemmas(p))[0] for p in parts)


# ── Вывод ─────────────────────────────────────────────────────────────────

def head(title, count=None, note=""):
    tail = f" ({count})" if count is not None else ""
    print(f"\n── {title}{tail}{note}")


def hint(*lines):
    print()
    for l in lines:
        print(f"  {l}")


def counter_top(c, n=12):
    return c.most_common(n) if isinstance(c, Counter) else list(c)[:n]
