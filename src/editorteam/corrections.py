"""Журнал правок автора: было → стало → почему.

Каждое замечание автора должно стать правилом, а не остаться в чате.
Журнал читают три места: затвор (term и phrase отклоняются как слова не по
словарю), промпт модели (все записи показываются как «правки автора») и
эвалы. Журнал — данные, поэтому лежит в config/, рядом со словарём.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import yaml

DEFAULT_PATH = Path(__file__).resolve().parents[2] / "config" / "corrections.yaml"
KINDS = ("term", "phrase", "structure", "fact")
MAX_WORDS = 6  # длиннее — это не замена, а переписанный абзац
WORD = re.compile(r"[А-Яа-яЁёA-Za-z'’-]+")


class CorrectionsError(ValueError):
    pass


@dataclass
class Correction:
    was: str
    became: str
    kind: str = "phrase"
    reason: str = ""
    context: str = ""
    date: str = ""
    source: str = ""
    extra: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        out = {"was": self.was, "became": self.became, "kind": self.kind}
        for key in ("reason", "context", "date", "source"):
            value = getattr(self, key)
            if value:
                out[key] = value
        out.update(self.extra)
        return out


def path() -> Path:
    return Path(os.environ.get("EDITOR_CORRECTIONS", DEFAULT_PATH))


def load(file: Path | None = None) -> list[Correction]:
    p = file or path()
    if not p.exists():
        return []
    data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    out = []
    for raw in data.get("corrections") or []:
        if not isinstance(raw, dict) or not raw.get("was") or not raw.get("became"):
            continue
        kind = raw.get("kind", "phrase")
        if kind not in KINDS:
            raise CorrectionsError(f"неизвестный kind «{kind}» у правки «{raw['was']}»")
        known = {"was", "became", "kind", "reason", "context", "date", "source"}
        out.append(
            Correction(
                was=str(raw["was"]).strip(),
                became=str(raw["became"]).strip(),
                kind=kind,
                reason=str(raw.get("reason", "") or ""),
                context=str(raw.get("context", "") or ""),
                date=str(raw.get("date", "") or ""),
                source=str(raw.get("source", "") or ""),
                extra={k: v for k, v in raw.items() if k not in known},
            )
        )
    return out


def save(items: list[Correction], file: Path | None = None) -> Path:
    p = file or path()
    header = ""
    if p.exists():
        text = p.read_text(encoding="utf-8")
        header = text.split("corrections:")[0].rstrip("\n") + "\n"
    if not header.strip():
        header = "version: 1\n"
    body = yaml.safe_dump(
        {"corrections": [c.to_dict() for c in items]},
        allow_unicode=True,
        sort_keys=False,
        width=100,
    )
    p.write_text(header + body, encoding="utf-8")
    return p


def add(
    was: str,
    became: str,
    *,
    kind: str = "phrase",
    reason: str = "",
    context: str = "",
    source: str = "",
    file: Path | None = None,
) -> Correction:
    if kind not in KINDS:
        raise CorrectionsError(f"kind должен быть одним из {', '.join(KINDS)}")
    was, became = was.strip(), became.strip()
    if not was or not became or was == became:
        raise CorrectionsError("нужны «было» и «стало», и они должны различаться")
    items = load(file)
    for item in items:
        if item.was.lower() == was.lower() and item.became.lower() == became.lower():
            return item
    item = Correction(
        was=was,
        became=became,
        kind=kind,
        reason=reason,
        context=context,
        date=date.today().isoformat(),
        source=source,
    )
    items.append(item)
    save(items, file)
    return item


def proposals(before: str, after: str) -> list[dict]:
    """Замены из диффа «до → после», которые похожи на правило, а не на переписку.

    Берутся замены не длиннее MAX_WORDS слов с каждой стороны и с хотя бы
    одним буквенным словом; длинные куски — это пересобранный абзац, из него
    правило не вывести.
    """
    import difflib

    wa, wb = before.split(), after.split()
    sm = difflib.SequenceMatcher(None, wa, wb, autojunk=False)
    out = []
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag != "replace":
            continue
        was, became = " ".join(wa[i1:i2]), " ".join(wb[j1:j2])
        if not (1 <= i2 - i1 <= MAX_WORDS and 1 <= j2 - j1 <= MAX_WORDS):
            continue
        if not WORD.search(was) or not WORD.search(became):
            continue
        clean = lambda s: s.strip(" .,;:!?«»\"'()—–-")  # noqa: E731
        was, became = clean(was), clean(became)
        if not was or not became or was.lower() == became.lower():
            continue
        left = " ".join(wa[max(0, i1 - 3) : i1])
        right = " ".join(wa[i2 : i2 + 3])
        kind = "term" if len(was.split()) == 1 and len(became.split()) == 1 else "phrase"
        out.append(
            {
                "was": was,
                "became": became,
                "kind": kind,
                "context": f"{left} [{was}] {right}".strip(),
            }
        )
    seen, unique = set(), []
    for item in out:
        key = (item["was"].lower(), item["became"].lower())
        if key in seen:
            continue
        seen.add(key)
        unique.append(item)
    return unique


def for_prompt(limit: int = 40) -> list[dict]:
    """Последние правки для промпта: было → стало → почему."""
    items = load()
    return [
        {"was": c.was, "became": c.became, "kind": c.kind, "reason": c.reason}
        for c in items[-limit:]
    ]


def for_gate() -> list[dict]:
    """Правки term и phrase как правила словаря для затвора."""
    return [
        {
            "id": f"correction:{c.was}",
            "subject": c.was,
            "preferred": c.became,
            "pattern": _pattern(c.was) if len(c.was.split()) > 1 else None,
            "case_sensitive": False,
        }
        for c in load()
        if c.kind in ("term", "phrase")
    ]


def _pattern(phrase: str) -> str:
    """Образец фразы с падежами: «яичная сборка» ловит «яичные сборки»."""
    parts = []
    for word in phrase.split():
        w = re.escape(word)
        parts.append(w[:-2] + r"\w*" if len(word) > 4 else w)
    return r"\b" + r"\s+".join(parts)
