"""Единая модель находки и стабильный JSON.

Анализаторы возвращают данные, печать отделена. Это нужно, чтобы одну и ту же
находку можно было показать человеку, отдать в CI и сравнить с эталоном.

Ключевое различие, которое модель обязана удерживать:

    error   — точная ошибка: сверено со справочником или с правилом
    likely  — вероятная ошибка: эвристика с хорошей точностью
    review  — сигнал редактору: решает человек, машина не берётся судить

Редакторские сигналы не должны ронять CI, поэтому severity и exit code
разведены: по умолчанию падение только на `error`.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field

SCHEMA_VERSION = "1.0"

SEVERITIES = ("error", "likely", "review", "info")
_ORDER = {s: i for i, s in enumerate(SEVERITIES)}


@dataclass
class Finding:
    """Одна находка анализатора.

    evidence — то, что реально стоит в тексте; suggestion — чем заменить,
    если замена известна. Находка без адреса бесполезна, поэтому line
    заполняется всегда, когда позиция определима.
    """

    id: str
    analyzer: str
    category: str
    severity: str
    message: str
    confidence: float = 1.0
    evidence: str = ""
    suggestion: str = ""
    line: int | None = None
    column: int | None = None
    start: int | None = None
    end: int | None = None
    profile: str | None = None
    meta: dict = field(default_factory=dict)

    def __post_init__(self):
        if self.severity not in SEVERITIES:
            raise ValueError(f"severity должен быть из {SEVERITIES}, получено {self.severity!r}")
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError(f"confidence вне [0,1]: {self.confidence}")

    def to_dict(self) -> dict:
        return {k: v for k, v in asdict(self).items() if v not in (None, "", {}) or k == "severity"}


@dataclass
class Report:
    """Результат прогона: находки плюс измеренные величины."""

    document: str
    profile: str
    findings: list[Finding] = field(default_factory=list)
    metrics: dict = field(default_factory=dict)
    skipped: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def add(self, finding: Finding) -> None:
        self.findings.append(finding)

    def worst(self) -> str | None:
        if not self.findings:
            return None
        return min((f.severity for f in self.findings), key=lambda s: _ORDER[s])

    def count(self, severity: str) -> int:
        return sum(1 for f in self.findings if f.severity == severity)

    def to_dict(self) -> dict:
        return {
            "schema_version": SCHEMA_VERSION,
            "document": self.document,
            "profile": self.profile,
            "summary": {s: self.count(s) for s in SEVERITIES},
            "metrics": self.metrics,
            "findings": [f.to_dict() for f in
                         sorted(self.findings, key=lambda f: (_ORDER[f.severity],
                                                              f.line or 0, f.id))],
            "analyzers_skipped": self.skipped,
            "notes": self.notes,
        }

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=indent, sort_keys=False)


def exit_code(report: Report, fail_on: str = "error") -> int:
    """0 — можно продолжать, 1 — есть находки не легче порога.

    По умолчанию редакторские сигналы (`review`) CI не ломают: это
    приглашение посмотреть, а не дефект.
    """
    if fail_on not in SEVERITIES:
        raise ValueError(f"fail_on должен быть из {SEVERITIES}")
    threshold = _ORDER[fail_on]
    return 1 if any(_ORDER[f.severity] <= threshold for f in report.findings) else 0
