"""Жанровые профили: чего ждать от материала в зависимости от его вида.

Гайд по Полям сражений нельзя мерить требованиями гайда по колоде: там нет
ни муллигана, ни декбилдинга, ни матч-апов по одиннадцати классам. Профиль
решает, какие разделы обязательны, какие анализаторы включены и с какими
весами считается балл.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import yaml

PROFILES_DIR = Path(__file__).resolve().parents[2] / "config" / "profiles"
DEFAULT = "constructed-guide"


class ProfileError(ValueError):
    pass


@dataclass
class Section:
    id: str
    title: str
    variants: list[str]
    required: bool
    corpus_share: int | None = None


@dataclass
class Profile:
    id: str
    title: str
    description: str
    min_words: int
    sections: list[Section]
    require_classes: bool
    analyzers: dict
    weights: dict
    note: str = ""

    @property
    def required_sections(self) -> list[Section]:
        return [s for s in self.sections if s.required]

    def enabled(self, analyzer: str) -> bool:
        return bool(self.analyzers.get(analyzer, True))


def _section(raw: dict, required: bool) -> Section:
    return Section(
        id=raw["id"],
        title=raw.get("title", raw["id"]),
        variants=[v.lower() for v in raw.get("variants", [])] or [raw.get("title", "").lower()],
        required=required,
        corpus_share=raw.get("corpus_share"),
    )


@lru_cache(maxsize=None)
def load(name: str = DEFAULT) -> Profile:
    path = PROFILES_DIR / f"{name}.yaml"
    if not path.exists():
        raise ProfileError(f"неизвестный профиль: {name}. Доступны: {', '.join(available())}")
    d = yaml.safe_load(path.read_text(encoding="utf-8"))
    secs = d.get("sections") or {}
    sections = [_section(s, True) for s in (secs.get("required") or [])]
    sections += [_section(s, False) for s in (secs.get("optional") or [])]
    return Profile(
        id=d["id"],
        title=d.get("title", d["id"]),
        description=d.get("description", ""),
        min_words=int(d.get("min_words", 0)),
        sections=sections,
        require_classes=bool((d.get("matchups") or {}).get("require_classes", False)),
        analyzers=d.get("analyzers") or {},
        weights=d.get("weights") or {},
        note=d.get("note", ""),
    )


def available() -> list[str]:
    return sorted(p.stem for p in PROFILES_DIR.glob("*.yaml"))


# слова-приметы жанра. Автоопределение показывает профиль и уверенность,
# но никогда не подменяет явный выбор пользователя
HINTS = {
    "battlegrounds-guide": [r"поля\s+сражений", r"\bбаттлграунд", r"\bтаверн", r"\bтройк\w+\s+существ",
                            r"\bгерой\s+таверны", r"\bаксессуар", r"\bлобб"],
    "meta-report": [r"тир-лист", r"мета-отчёт", r"мета-отчет", r"расстановк\w+\s+сил",
                    r"\bпроцент\w*\s+побед", r"\d+\s*%"],
    "news": [r"патчноут", r"патч\s*\d+\.\d+", r"разработчик\w+\s+объяв", r"анонс\w*"],
    "constructed-guide": [r"муллиган", r"декбилдинг", r"матч-ап", r"сборк\w+\s+архетипа", r"колод\w+"],
}


def detect(text: str) -> tuple[str, float]:
    """Угадать профиль. Возвращает (имя, уверенность 0–1).

    Уверенность показывается пользователю: при низкой лучше указать профиль явно.
    """
    scores = {}
    for name, pats in HINTS.items():
        hits = sum(len(re.findall(p, text, re.I)) for p in pats)
        scores[name] = hits
    if not any(scores.values()):
        return DEFAULT, 0.0
    best = max(scores, key=scores.get)
    total = sum(scores.values())
    return best, round(scores[best] / total, 2)
