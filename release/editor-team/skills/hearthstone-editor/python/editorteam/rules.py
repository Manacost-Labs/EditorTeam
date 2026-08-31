"""Загрузчик редакционных правил.

Одно решение хранится в одном месте — в `config/*.yaml`. Код и документация
читают отсюда, а не повторяют таблицы у себя. Раньше правило про «винрейт»
жило одновременно в CLAUDE.md, ГОЛОС.md и в голове редактора, и они разошлись.

Виды решений:
    auto_replace — заменять автоматически, практика автора совпадает с правилом
    allowed      — оставлять как написано, слово авторское
    forbidden    — слова в корпусе нет, правило пустое
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

import yaml

CONFIG_DIR = Path(__file__).resolve().parents[2] / "config"

DECISIONS = {"auto_replace", "allowed", "forbidden"}


class ConfigError(ValueError):
    """Конфигурация не проходит валидацию."""


@dataclass
class TermRule:
    id: str
    decision: str
    slang: str | None = None
    word: str | None = None
    preferred: str | None = None
    alternative: str | None = None
    rejected_replacement: str | None = None
    corpus: dict = field(default_factory=dict)
    note: str = ""

    @property
    def subject(self) -> str:
        return self.slang or self.word or self.id

    def replacement_for(self, text_word: str) -> str | None:
        """Чем заменять — или None, если решение «оставить»."""
        if self.decision != "auto_replace" or not self.preferred:
            return None
        return self.preferred


def _load_yaml(name: str) -> dict:
    path = CONFIG_DIR / name
    if not path.exists():
        raise ConfigError(f"нет файла конфигурации: {path}")
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ConfigError(f"{name}: ожидался словарь на верхнем уровне")
    return data


@lru_cache(maxsize=1)
def terminology() -> list[TermRule]:
    data = _load_yaml("terminology.yaml")
    rules = []
    for raw in data.get("rules", []):
        if "id" not in raw:
            raise ConfigError(f"правило без id: {raw}")
        if raw.get("decision") not in DECISIONS:
            raise ConfigError(
                f"{raw['id']}: decision должен быть одним из {sorted(DECISIONS)}, "
                f"получено {raw.get('decision')!r}"
            )
        known = set(TermRule.__dataclass_fields__)
        rules.append(TermRule(**{k: v for k, v in raw.items() if k in known}))
    return rules


@lru_cache(maxsize=1)
def typography() -> dict:
    return _load_yaml("typography.yaml")


def validate() -> list[str]:
    """Проверить конфигурацию на конфликты. Возвращает список проблем."""
    problems: list[str] = []
    rules = terminology()

    seen_ids: dict[str, int] = {}
    for r in rules:
        seen_ids[r.id] = seen_ids.get(r.id, 0) + 1
    for rid, n in seen_ids.items():
        if n > 1:
            problems.append(f"повторяющийся id: {rid} ({n} раз)")

    # одно и то же слово не может одновременно заменяться и оставаться
    by_subject: dict[str, list[TermRule]] = {}
    for r in rules:
        by_subject.setdefault(r.subject.lower(), []).append(r)
    for subject, group in by_subject.items():
        decisions = {r.decision for r in group}
        if len(decisions) > 1:
            problems.append(
                f"«{subject}»: противоречивые решения {sorted(decisions)} "
                f"в правилах {', '.join(r.id for r in group)}"
            )

    # auto_replace обязан указывать, на что заменять
    for r in rules:
        if r.decision == "auto_replace" and not r.preferred:
            problems.append(f"{r.id}: auto_replace без preferred")
        if r.decision == "allowed" and r.preferred:
            problems.append(f"{r.id}: allowed с preferred — решение неоднозначно")

    # замена не должна противоречить частотам корпуса
    for r in rules:
        c = r.corpus or {}
        if r.decision == "auto_replace":
            slang_n, pref_n = c.get("slang"), c.get("preferred")
            if slang_n is not None and pref_n is not None and slang_n > pref_n:
                problems.append(
                    f"{r.id}: заменяем «{r.subject}» ({slang_n}) на «{r.preferred}» "
                    f"({pref_n}), хотя в корпусе чаще исходное слово"
                )

    problems.extend(_validate_typography())
    return problems


def _validate_typography() -> list[str]:
    problems = []
    t = typography()

    yo = t.get("yo", {})
    if yo.get("decision") not in {"remove", "keep", "as_written"}:
        problems.append(f"typography.yo.decision: неизвестное значение {yo.get('decision')!r}")

    q = t.get("quotes", {})
    if q.get("decision") not in {"straight", "guillemets", "as_written"}:
        problems.append(f"typography.quotes.decision: неизвестное значение {q.get('decision')!r}")

    arch = t.get("archetype_hyphen", {})
    if arch.get("decision") == "space" and arch.get("corpus", {}).get("с дефисом", 0) > 0:
        problems.append("archetype_hyphen: решение «без дефиса» при ненулевой частоте дефиса")

    nums = t.get("numbers", {})
    rng = nums.get("range", {})
    dashes = t.get("dashes", {})
    # диапазон через дефис и нормализация тире не должны спорить
    if rng.get("decision") == "hyphen" and "матч-ап" not in dashes.get("keep_hyphen_in", []):
        problems.append("dashes.keep_hyphen_in не покрывает случаи, где дефис обязателен")

    return problems


def as_markdown_table() -> str:
    """Таблица для документации — чтобы Markdown не расходился с конфигурацией."""
    lines = ["| Слово | Решение | Корпус |", "|---|---|---|"]
    for r in terminology():
        c = r.corpus or {}
        freq = ", ".join(f"{k}: {v}" for k, v in c.items())
        decision = {
            "auto_replace": f"→ {r.preferred}",
            "allowed": "оставлять",
            "forbidden": "нет в корпусе",
        }[r.decision]
        lines.append(f"| {r.subject} | {decision} | {freq} |")
    return "\n".join(lines)


def word_pattern(rule: TermRule) -> re.Pattern:
    """Образец для поиска слова правила во всех падежах (грубо, по основе)."""
    stem = re.escape(rule.subject[:-1] if len(rule.subject) > 4 else rule.subject)
    return re.compile(rf"\b{stem}\w*", re.IGNORECASE)
