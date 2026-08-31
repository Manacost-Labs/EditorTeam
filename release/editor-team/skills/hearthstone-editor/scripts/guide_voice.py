#!/usr/bin/env python3
"""Guide Voice Layer: evidence stays backstage in ordinary guides.

The analyzer does not remove useful numbers. It only flags research narration
when the editorial mode is GUIDE and the author did not explicitly request an
evidence-facing result.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

MODES = ("GUIDE", "ANALYSIS", "REPORT")


@dataclass(frozen=True)
class Leak:
    id: str
    evidence: str
    suggestion: str
    line: int
    start: int
    end: int
    severity: str = "likely"


PATTERNS = (
    ("replay-data", r"\bпо\s+данным\s+(?:реплеев|replays?)\b"),
    ("analysed-games", r"\bв\s+анализируемых\s+(?:партиях|матчах)\b"),
    ("sample", r"\bв\s+выборке\b"),
    ("players-mostly", r"\bигроки\s+чаще\s+всего\b"),
    ("strong-players", r"\bсильные\s+игроки\s+(?:делают|играют|оставляют)\b"),
    ("statistics", r"\bпо\s+статистике\b"),
    ("hsguru", r"\bпо\s+данным\s+hs\s*guru\b"),
    ("reddit", r"\bна\s+reddit\s+считают\b"),
    ("community", r"\bсообщество\s+считает\b"),
    ("player-opinion", r"\bпо\s+мнению\s+игроков\b"),
    ("chinese-sources", r"\bв\s+китайских\s+источниках\b"),
    ("analysis-shows", r"\b(?:анализ|исследование)\s+показывает\b"),
    ("we-analysed", r"\bмы\s+(?:обнаружили|проанализировали)\b"),
    (
        "percentage-cases",
        r"\bв\s+\d+(?:[.,]\d+)?\s*%\s+(?:случаев|реплеев|replays?)\b",
    ),
)

KEEP_REPLAY = re.compile(
    r"\bв\s+(?P<pct>\d+(?:[.,]\d+)?)\s*%\s+(?:реплеев|replays?)\s+"
    r"игроки\s+(?:оставляют|держат)\s+(?P<card>[^.!?\n]+)",
    re.IGNORECASE,
)


def normalize_mode(mode: str | None) -> str:
    value = (mode or "GUIDE").upper()
    if value not in MODES:
        raise ValueError(f"editorial mode must be one of {', '.join(MODES)}")
    return value


def _line(text: str, pos: int) -> int:
    return text.count("\n", 0, pos) + 1


def direct_suggestion(sentence: str) -> str:
    """Build a direct guide suggestion for the common replay keep statement."""

    match = KEEP_REPLAY.search(sentence)
    if not match:
        return "Перескажите как прямой игровой совет; источники оставьте в evidence-слое."
    pct = float(match.group("pct").replace(",", "."))
    card = match.group("card").strip(' «»"')
    if pct >= 80:
        return f"{card} почти всегда стоит оставлять."
    if pct >= 55:
        return f"Чаще всего {card} стоит оставлять."
    return f"{card} можно оставить в подходящих матч-апах."


def scan(text: str, mode: str = "GUIDE", evidence_requested: bool = False) -> list[dict]:
    mode = normalize_mode(mode)
    if mode != "GUIDE" or evidence_requested:
        return []
    leaks: list[Leak] = []
    occupied: list[tuple[int, int]] = []
    for rule_id, pattern in PATTERNS:
        for match in re.finditer(pattern, text, re.IGNORECASE):
            if any(a <= match.start() < b for a, b in occupied):
                continue
            sentence_start = (
                max(text.rfind(".", 0, match.start()), text.rfind("\n", 0, match.start())) + 1
            )
            stops = [p for p in (text.find(mark, match.end()) for mark in ".!?\n") if p >= 0]
            sentence_end = min(stops) + 1 if stops else len(text)
            sentence = text[sentence_start:sentence_end].strip()
            leaks.append(
                Leak(
                    id=rule_id,
                    evidence=match.group(0),
                    suggestion=direct_suggestion(sentence),
                    line=_line(text, match.start()),
                    start=match.start(),
                    end=match.end(),
                )
            )
            occupied.append((match.start(), match.end()))
    return [asdict(item) for item in leaks]


def mode_rules(mode: str = "GUIDE") -> dict:
    mode = normalize_mode(mode)
    return {
        "mode": mode,
        "evidence_hidden": mode == "GUIDE",
        "research_narration_allowed": mode in {"ANALYSIS", "REPORT"},
        "source_breakdown_allowed": mode == "REPORT",
        "principle": "Evidence determines what to say; editorial rules determine how to say it.",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Find research-report narration in guide prose")
    parser.add_argument("file", type=Path)
    parser.add_argument("--mode", choices=MODES, default="GUIDE")
    parser.add_argument("--evidence-requested", action="store_true")
    parser.add_argument("--format", choices=("text", "json"), default="text")
    args = parser.parse_args()
    try:
        text = args.file.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    result = scan(text, args.mode, args.evidence_requested)
    if args.format == "json":
        print(json.dumps({"mode": args.mode, "findings": result}, ensure_ascii=False, indent=2))
    else:
        for item in result:
            print(f"[стр. {item['line']}] {item['evidence']}\n  -> {item['suggestion']}")
        if not result:
            print("Исследовательский тон для этого режима не найден или допустим.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
