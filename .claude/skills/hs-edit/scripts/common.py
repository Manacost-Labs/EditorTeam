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


def _find_root():
    """Корень — ближайший каталог вверх, где лежит корпус или конфигурация.

    В репозитории это .claude/skills/hs-edit/scripts -> четыре уровня вверх,
    в собранном скилле — hearthstone-editor/scripts -> один. Считать уровни
    нельзя: раскладки разные, и на этом уже ломались пути.
    """
    for candidate in (SKILL, *SCRIPTS.parents):
        if any((candidate / marker).exists() for marker in ("гайды", "corpus", "config")):
            return candidate
    return SKILL


ROOT = _find_root()
CORPUS = ROOT / "гайды"
MANAGED_CORPUS = ROOT / "corpus" / "guides"
CORPUS_MANIFEST = ROOT / "corpus" / "manifest.json"
VENDOR = ROOT / "vendor"
if VENDOR.exists() and str(VENDOR) not in sys.path:
    sys.path.insert(0, str(VENDOR))


def venv_python():
    """Интерпретатор из .venv проекта — или None.

    На Windows он лежит в Scripts\\python.exe, на POSIX — в bin/python.
    Раньше путь был зашит как bin/python, и на Windows бутстрап молча
    не срабатывал.
    """
    for rel in (Path("bin") / "python", Path("Scripts") / "python.exe", Path("bin") / "python3"):
        candidate = ROOT / ".venv" / rel
        if candidate.exists():
            return candidate
    return None


VENV_PY = venv_python()


def ensure_venv(module="pymorphy3"):
    """Перезапустить себя из .venv, если нужной библиотеки нет в текущем Python."""
    try:
        __import__(module)
        return
    except ImportError:
        pass

    flag = f"_REEXEC_{Path(sys.argv[0]).stem.upper()}"
    py = venv_python()
    # Сверять пути интерпретаторов нельзя: python внутри venv — симлинк на
    # системный, и resolve() делает их равными. Смотрим на префикс окружения.
    already_inside = Path(sys.prefix).resolve() == (ROOT / ".venv").resolve()
    if py and not os.environ.get(flag) and not already_inside:
        os.environ[flag] = "1"
        script = str(Path(sys.argv[0]).resolve())
        os.execv(str(py), [str(py), script] + sys.argv[1:])

    hint = ".venv\\Scripts\\pip" if os.name == "nt" else ".venv/bin/pip"
    print(f"нужен {module}:\n  {hint} install pymorphy3 pymorphy3-dicts-ru", file=sys.stderr)
    sys.exit(2)


def sibling(name):
    """Подгрузить соседний скрипт как модуль."""
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    # dataclasses и ряд introspection API ищут модуль в sys.modules
    # уже во время exec_module (Python 3.13 требует это строго).
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


# ── Текст ─────────────────────────────────────────────────────────────────

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


_TABLE_ROW = re.compile(r"^\s*\|.*\|\s*$")
_DECK_CODE_LINE = re.compile(r"^\s*AAECA\S+\s*$")
_HEADING = re.compile(r"^\s*#{1,6}\s")


def prose_only(text):
    """Только проза: без блоков кода, таблиц, кодов колод и markdown-заголовков.

    Ритм, голос и лексика считаются по этому тексту. Таблица на 270 слов —
    не предложение, а код колоды — не слово: без маскирования ритм статьи
    с таблицей показывал 1,23 при 0,46 по прозе. Списки и цитаты остаются:
    в корпусе автора они есть, и нормы сняты с ними.
    """
    text = re.sub(r"```.*?```", "", text, flags=re.DOTALL)
    kept = []
    for line in text.split("\n"):
        if _TABLE_ROW.match(line) or _DECK_CODE_LINE.match(line) or _HEADING.match(line):
            continue
        kept.append(line)
    return "\n".join(kept)


# ── Корпус ────────────────────────────────────────────────────────────────


def guide_name(path):
    return re.sub(r"^\d+_", "", Path(path).stem)


FRONT_MATTER = re.compile(r"\A---\r?\n(.*?)\r?\n---\r?\n", re.DOTALL)


def split_front_matter(text):
    """Отделить YAML-метаданные от текста.

    Возвращает (метаданные, тело). Метаданные не должны попадать ни в один
    замер: иначе нормы корпуса поедут от служебных строк.
    """
    m = FRONT_MATTER.match(text)
    if not m:
        return {}, text
    meta = {}
    for line in m.group(1).splitlines():
        line = line.strip()
        if not line or line.startswith("#") or ":" not in line:
            continue
        key, _, value = line.partition(":")
        meta[key.strip()] = value.strip().strip('"').strip("'")
    return meta, text[m.end() :]


def read_document(path):
    """Прочитать файл корпуса: (метаданные, тело без фронт-маттера)."""
    return split_front_matter(Path(path).read_text(encoding="utf-8"))


def corpus_manifest():
    """Активное состояние версионируемого корпуса.

    Старые сборки без manifest.json остаются рабочими: в них
    весь каталог `гайды/` считается approved style corpus.
    """
    selected = Path(os.environ.get("EDITOR_CORPUS_MANIFEST", CORPUS_MANIFEST))
    if not selected.exists():
        return {"current_version": "legacy-v1", "guides": [], "excluded_legacy_ids": []}
    try:
        return json.loads(selected.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"current_version": "invalid", "guides": [], "excluded_legacy_ids": []}


def corpus_records():
    """Одобренные тексты, которые имеют право менять стилевую норму."""
    manifest = corpus_manifest()
    excluded = set(manifest.get("excluded_legacy_ids", []))
    records = []
    if CORPUS.exists():
        for path in sorted(CORPUS.glob("*.md")):
            meta, text = read_document(path)
            if meta.get("id", path.stem) not in excluded:
                meta = {**meta, "corpus_status": "approved", "corpus_source": "legacy"}
                records.append((path, meta, text))
    for item in manifest.get("guides", []):
        if item.get("status") != "approved":
            continue
        path = ROOT / item["path"]
        if not path.exists():
            continue
        meta, text = read_document(path)
        records.append((path, {**meta, **item, "corpus_status": "approved"}, text))
    return records


def corpus_files():
    return [path for path, _, _ in corpus_records()]


def body(path):
    """Только текст — то, что видят анализаторы."""
    return read_document(path)[1]


@lru_cache(maxsize=1)
def corpus_text():
    return "\n".join(body(f) for f in corpus_files())


# ── Карты ─────────────────────────────────────────────────────────────────


@lru_cache(maxsize=1)
def card_db():
    """{'карты': [...], 'механики': [...]} из справочника локализации."""
    p = ASSETS / "cards-ru.json"
    if not p.exists():
        print(
            f"нет справочника карт: {p}\n  обновить: python3 cards.py --обновить", file=sys.stderr
        )
        sys.exit(2)
    return json.loads(p.read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def morph():
    """Анализатор. Путь к словарю передаётся явно.

    Во вложенной поставке (vendor/) у пакета нет метаданных, и pymorphy3
    не находит словарь сам — падает с «Can't find a dictionary for ru».
    """
    ensure_venv("pymorphy3")
    import pymorphy3

    try:
        import pymorphy3_dicts_ru

        return pymorphy3.MorphAnalyzer(path=pymorphy3_dicts_ru.get_path())
    except (ImportError, AttributeError):
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
