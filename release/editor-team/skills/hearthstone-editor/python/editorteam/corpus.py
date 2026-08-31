"""Корпус: метаданные, разбиение на выборки и валидация.

Корпус — тест-набор заведомо хорошего письма и источник всех норм. Поэтому
его целостность важнее удобства: пустой файл, дубль идентификатора или
незамеченный след извлечения из PDF тихо портят калибровку.

Тексты извлечены из PDF, и часть артефактов вычищена не полностью. Валидатор
их показывает, но **не правит**: корпус не редактируется ради прохождения
проверок.
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from pathlib import Path

from editorteam import profiles as P

ROOT = Path(__file__).resolve().parents[2]
REPO_SKILL_SCRIPTS = ROOT / ".claude" / "skills" / "hs-edit" / "scripts"
SKILL_SCRIPTS = REPO_SKILL_SCRIPTS if REPO_SKILL_SCRIPTS.exists() else ROOT / "scripts"

REQUIRED_FIELDS = (
    "id",
    "title",
    "published_at",
    "updated_at",
    "game_mode",
    "genre",
    "patch",
    "source_url",
    "extraction_source",
    "clean_status",
)

GAME_MODES = {"standard", "wild", "battlegrounds", "arena", "duels", "unknown"}
CLEAN_STATUSES = {"raw", "cleaned", "reviewed", "unknown"}

# следы извлечения из PDF: колонтитулы, разрывы имён, интерфейс сайта
ARTIFACTS = {
    "колонтитул печати": r"ПРОДЛИТЬ ПОДПИСКУ|\d{1,2}/\d{1,2}/\d{4},?\s*\d{1,2}:\d{2}",
    "разрыв слова переносом": r"[А-Яа-яЁё]-\s*\n\s*[а-яё]",
    "блок комментариев": r"^\s*Ответить\s*$|Администратор\s*-\s*vasili",
    "сайдбар": r"ПОСЛЕДНИЕ\s+СТАТЬИ|Другие\s+статьи|НАПИСАТЬНАПИСАТЬ",
    "форма загрузки": r"Выбрать\s+файл",
}


def _scripts():
    if str(SKILL_SCRIPTS) not in sys.path:
        sys.path.insert(0, str(SKILL_SCRIPTS))
    import common as C

    return C


@dataclass
class Problem:
    document: str
    kind: str
    message: str
    severity: str = "likely"


def documents() -> list[tuple[Path, dict, str]]:
    C = _scripts()
    return C.corpus_records()


def validate() -> list[Problem]:
    problems: list[Problem] = []
    known_profiles = set(P.available())
    seen_ids: dict[str, str] = {}

    docs = documents()
    if not docs:
        return [Problem("гайды/", "empty-corpus", "корпус пуст", "error")]

    for path, meta, text in docs:
        name = path.name

        if not text.strip():
            problems.append(Problem(name, "empty-file", "файл без текста", "error"))
            continue

        for field in REQUIRED_FIELDS:
            if field not in meta:
                problems.append(Problem(name, "missing-field", f"нет поля {field}", "likely"))

        doc_id = meta.get("id")
        if doc_id:
            if doc_id in seen_ids:
                problems.append(
                    Problem(
                        name,
                        "duplicate-id",
                        f"идентификатор {doc_id} уже занят файлом {seen_ids[doc_id]}",
                        "error",
                    )
                )
            seen_ids[doc_id] = name

        genre = meta.get("genre")
        if genre and genre not in known_profiles:
            problems.append(
                Problem(
                    name,
                    "unknown-genre",
                    f"жанр {genre!r} не соответствует ни одному профилю "
                    f"({', '.join(sorted(known_profiles))})",
                    "error",
                )
            )

        mode = meta.get("game_mode")
        if mode and mode not in GAME_MODES:
            problems.append(Problem(name, "unknown-mode", f"режим {mode!r} неизвестен", "likely"))

        status = meta.get("clean_status")
        if status and status not in CLEAN_STATUSES:
            problems.append(
                Problem(name, "unknown-status", f"статус {status!r} неизвестен", "likely")
            )

        for label, pattern in ARTIFACTS.items():
            hits = len(re.findall(pattern, text, re.I | re.M))
            if hits:
                problems.append(
                    Problem(
                        name,
                        "extraction-artifact",
                        f"{label}: {hits} шт. — след извлечения, не авторский текст",
                        "review",
                    )
                )

    return problems


def stats() -> dict:
    docs = documents()
    words = sum(len(t.split()) for _, _, t in docs)
    by_genre: dict[str, int] = {}
    by_mode: dict[str, int] = {}
    unknown_fields = 0
    for _, meta, _ in docs:
        by_genre[meta.get("genre", "—")] = by_genre.get(meta.get("genre", "—"), 0) + 1
        by_mode[meta.get("game_mode", "—")] = by_mode.get(meta.get("game_mode", "—"), 0) + 1
        unknown_fields += sum(1 for f in REQUIRED_FIELDS if meta.get(f) == "unknown")
    return {
        "documents": len(docs),
        "words": words,
        "by_genre": by_genre,
        "by_mode": by_mode,
        "unknown_values": unknown_fields,
    }


def split(holdout_share: float = 0.2) -> dict[str, list[str]]:
    """Разделить корпус на калибровку и holdout.

    Разбиение детерминированное — по идентификатору, а не случайное: иначе
    калибровка меняется от запуска к запуску и числа перестают сходиться.
    Holdout не участвует в настройке порогов.
    """
    docs = documents()
    ids = sorted(meta.get("id", path.stem) for path, meta, _ in docs)
    step = max(2, int(1 / holdout_share)) if holdout_share else 0
    holdout = [d for i, d in enumerate(ids) if step and i % step == 0]
    calibration = [d for d in ids if d not in set(holdout)]
    return {"calibration": calibration, "holdout": holdout}
