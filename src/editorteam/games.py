"""Игровые паки: что в системе зависит от игры, а что нет.

Прозаический слой — маркеры шаблона, живые сигналы, ритм, согласованность —
про русский язык, а не про игру, и переносится целиком. От игры зависят три
вещи: справочник имён, защищённые слова и нормы.

Про нормы отдельно. Они снимаются с корпуса конкретного автора в конкретной
игре. Заимствованные нормы помечаются `provisional`, и всё, что на них
опирается, обязано это показывать: иначе система выдаёт чужую мерку за свою.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

import yaml

GAMES_DIR = Path(__file__).resolve().parents[2] / "config" / "games"
DEFAULT = "hearthstone"


class GameError(ValueError):
    pass


@dataclass
class Norms:
    provisional: bool
    source: str
    rhythm_ratio: float | None = None
    rhythm_alarm: float | None = None
    sentence_mean: float | None = None
    paragraph_sentences: float | None = None
    voice_per_1k: float | None = None
    voice_low: float | None = None
    markers_per_10k: float | None = None
    author_median: float | None = None

    def caveat(self) -> str | None:
        """Оговорка для отчёта. None — нормы свои, оговорка не нужна."""
        if not self.provisional:
            return None
        return (
            f"нормы предварительные: {self.source}. "
            f"Оценки ритма и живого — ориентир, а не эталон, пока не набран "
            f"корпус текстов в этой игре"
        )


@dataclass
class Game:
    id: str
    title: str
    locale: str
    names_asset: str | None
    names_kind: str
    names_note: str
    protected: list[str]
    profiles: list[str]
    norms: Norms
    updater: str | None = None
    extra: dict = field(default_factory=dict)

    @property
    def has_name_check(self) -> bool:
        return bool(self.names_asset)

    def skip_reason(self, analyzer: str) -> str | None:
        """Почему анализатор отключён для этой игры."""
        if analyzer == "cards" and not self.has_name_check:
            return self.names_note or f"нет справочника имён для {self.title}"
        return None


@lru_cache(maxsize=None)
def load(name: str = DEFAULT) -> Game:
    path = GAMES_DIR / f"{name}.yaml"
    if not path.exists():
        raise GameError(f"неизвестная игра: {name}. Доступны: {', '.join(available())}")
    d = yaml.safe_load(path.read_text(encoding="utf-8"))
    names = d.get("names") or {}
    n = d.get("norms") or {}
    return Game(
        id=d["id"],
        title=d.get("title", d["id"]),
        locale=d.get("locale", "ru"),
        names_asset=names.get("asset"),
        names_kind=names.get("kind", "имена"),
        names_note=names.get("note", ""),
        updater=names.get("updater"),
        protected=list(d.get("protected") or []),
        profiles=list(d.get("profiles") or []),
        norms=Norms(
            provisional=bool(n.get("provisional", True)),
            source=n.get("source", "неизвестно"),
            **{
                k: n.get(k)
                for k in (
                    "rhythm_ratio",
                    "rhythm_alarm",
                    "sentence_mean",
                    "paragraph_sentences",
                    "voice_per_1k",
                    "voice_low",
                    "markers_per_10k",
                    "author_median",
                )
            },
        ),
    )


def available() -> list[str]:
    return sorted(p.stem for p in GAMES_DIR.glob("*.yaml"))
