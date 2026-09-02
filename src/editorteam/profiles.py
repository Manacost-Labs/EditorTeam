"""Жанровые профили: чего ждать от материала в зависимости от его вида.

Гайд по Полям сражений нельзя мерить требованиями гайда по колоде: там нет
ни муллигана, ни декбилдинга, ни матч-апов по одиннадцати классам. Профиль
решает, какие разделы обязательны, какие анализаторы включены и с какими
весами считается балл.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
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
    # назначение раздела одной строкой и нижняя граница его тела: это данные
    # для скелета переплавки и глубокой проверки структуры, а не проза в коде
    purpose: str = ""
    min_words: int | None = None


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
    # opening: {requires: [archetype, expansion], formula: "…", promise: bool}
    # closing: {signature: "…"} — авторские приметы входа и выхода из корпуса
    opening: dict = field(default_factory=dict)
    closing: dict = field(default_factory=dict)
    # form: {tables_max, codes_max, repeated_facts_max, grade_labels} — форма подачи,
    # снятая с корпуса: у автора нет таблиц и кодов в тексте, цифры не повторяются
    form: dict = field(default_factory=dict)

    @property
    def required_sections(self) -> list[Section]:
        return [s for s in self.sections if s.required]

    def enabled(self, analyzer: str) -> bool:
        return bool(self.analyzers.get(analyzer, True))

    def skeleton(self) -> list[dict]:
        """Разделы в порядке профиля — данными, для промпта и проверок."""
        return [
            {
                "id": s.id,
                "title": s.title,
                "variants": list(s.variants),
                "required": s.required,
                "purpose": s.purpose,
                "min_words": s.min_words,
            }
            for s in self.sections
        ]


def _section(raw: dict, required: bool) -> Section:
    return Section(
        id=raw["id"],
        title=raw.get("title", raw["id"]),
        variants=[v.lower() for v in raw.get("variants", [])] or [raw.get("title", "").lower()],
        required=required,
        corpus_share=raw.get("corpus_share"),
        purpose=str(raw.get("purpose", "") or ""),
        min_words=int(raw["min_words"]) if raw.get("min_words") is not None else None,
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
        opening=d.get("opening") or {},
        closing=d.get("closing") or {},
        form=d.get("form") or {},
    )


def available() -> list[str]:
    return sorted(p.stem for p in PROFILES_DIR.glob("*.yaml"))


# слова-приметы жанра. Автоопределение показывает профиль и уверенность,
# но никогда не подменяет явный выбор пользователя
HINTS = {
    "anti-guide": [
        r"\bанти[- ]?гайд",
        r"\bконтрколод\w*",
        r"\bзаконтрить\b",
        r"\bконтр(?:а|ы|ой|ить)\b",
        r"\bколод\w*\s+против\b",
        r"\bпобед\w+\s+против\b",
    ],
    "battlegrounds-guide": [
        r"поля\s+сражений",
        r"\bбаттлграунд",
        r"\bтаверн",
        r"\bтройк\w+\s+существ",
        r"\bгерой\s+таверны",
        r"\bаксессуар",
        r"\bлобб",
    ],
    "battlegrounds-article": [
        r"\bпочему\b",
        r"\bчто\s+(?:изменилось|поменялось|происходит)\b",
        r"\bкак\s+(?:это|изменения|новые\s+эффекты)\s+(?:влияет|меняет)\b",
        r"\bпочему\s+это\s+(?:плохо|важно)\b",
        r"\bпотолок\s+силы\b",
    ],
    "analytics-article": [
        r"\bобзор\w*\s+(?:патч\w*|изменен\w*|карт\w*)",
        r"\bожидаем\w*\s+карт",
        r"\bротац\w*\s+(?:стандарт\w*|колод\w*)",
        r"\bкрафт\w*\b",
        r"\bпочему\b",
        r"\bчто\s+(?:изменилось|поменялось|произойдет)\b",
    ],
    "meta-report": [
        r"тир-лист",
        r"мета-отчёт",
        r"мета-отчет",
        r"расстановк\w+\s+сил",
        r"\bпроцент\w*\s+побед",
        r"\d+\s*%",
    ],
    "news": [r"патчноут", r"патч\s*\d+\.\d+", r"разработчик\w+\s+объяв", r"анонс\w*"],
    "constructed-guide": [
        r"муллиган",
        r"декбилдинг",
        r"матч-ап",
        r"сборк\w+\s+архетипа",
        r"колод\w+",
    ],
}

STRONG_ANTI_HINTS = (
    r"(?im)^categories:\s*.*\bанти[- ]?гайды\b",
    r"\bанти[- ]?гайд\b",
    r"\bконтрколод\w*\b",
    r"\bкак\s+(?:победить|законтрить)\b",
    r"\b\d+\s+колод\w*\s+(?:против|для\s+(?:побед|борьб|контр))",
)


def detect(text: str) -> tuple[str, float]:
    """Угадать профиль. Возвращает (имя, уверенность 0–1).

    Уверенность показывается пользователю: при низкой лучше указать профиль явно.
    """
    scores = {}
    for name, pats in HINTS.items():
        hits = sum(len(re.findall(p, text, re.I)) for p in pats)
        scores[name] = hits
    # Аналитическая статья о Полях сражений обычно содержит вопрос о причинах
    # и последствиях, но не имеет обязательного плана по ходам или списка
    # ключевых существ. Учитываем это только как мягкую подсказку авто выбора.
    article_cues = scores.get("battlegrounds-article", 0)
    guide_cues = scores.get("battlegrounds-guide", 0)
    if article_cues and re.search(r"поля\s+сражений|баттлграунд", text, re.I):
        if not re.search(r"ключевые\s+существа|план\s+по\s+ходам", text, re.I):
            # Термины BG встречаются и в аналитике. Если практической
            # структуры нет, переносим вес этих общих сигналов в профиль
            # статьи, чтобы автору не приходилось указывать профиль вручную.
            scores["battlegrounds-article"] += guide_cues + 2
    preamble = text[:1200]
    strong_anti = sum(len(re.findall(pattern, preamble, re.I)) for pattern in STRONG_ANTI_HINTS)
    if strong_anti:
        # Явный антигайд всё равно много раз употребляет слово «колода», поэтому
        # жанровый сигнал должен перевешивать общий constructed-признак.
        scores["anti-guide"] += 2 * sum(scores.values()) + 5 * strong_anti
    if not any(scores.values()):
        return DEFAULT, 0.0
    # При равном числе мягких сигналов предпочитаем аналитическую статью:
    # «обзор патча» и «почему карта заиграет» часто одновременно похожи на
    # новость или общий профиль Полей, но требуют именно читательской аналитики.
    best = max(scores, key=lambda name: (scores[name], name == "analytics-article"))
    total = sum(scores.values())
    return best, round(scores[best] / total, 2)
