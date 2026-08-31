#!/usr/bin/env python3
"""Keep editorial certainty at or below the evidence contract."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

LEVELS = (
    (3, "HIGH", r"\b(?:всегда|обязательно|нужно|должн(?:ы|а|о)?|always|must|universally)\b"),
    (
        2,
        "MEDIUM",
        r"\b(?:как\s+правило|обычно|чаще\s+всего|часто|стоит|usually|often|should)\b",
    ),
    (1, "LOW", r"\b(?:иногда|порой|можно|может|sometimes|can|may)\b"),
)
CONFIDENCE = {"LOW": 1, "MEDIUM": 2, "HIGH": 3}


def certainty(text: str) -> tuple[int, str | None, str | None]:
    for rank, label, pattern in LEVELS:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return rank, label, match.group(0)
    return 0, None, None


def _sentences(text: str) -> list[str]:
    return [item.strip() for item in re.split(r"(?<=[.!?…])\s+|\n+", text) if item.strip()]


def _claim_sentence(text: str, claim: dict) -> str:
    meaning = claim.get("meaning") if isinstance(claim.get("meaning"), dict) else {}
    card = str(meaning.get("card", "")).strip()
    if card:
        for sentence in _sentences(text):
            if card.casefold() in sentence.casefold():
                return sentence
    return text


def scan(before: str, after: str, claims: list[dict] | None = None) -> list[dict]:
    issues: list[dict] = []
    before_rank, before_label, before_marker = certainty(before)
    after_rank, after_label, after_marker = certainty(after)
    if before_rank and after_rank > before_rank:
        issues.append(
            {
                "kind": "CERTAINTY_DRIFT",
                "message": f"уверенность усилена: {before_marker} -> {after_marker}",
                "before": before_label,
                "after": after_label,
                "severity": "error",
            }
        )
    for claim in claims or []:
        confidence = str(claim.get("confidence", "")).upper()
        allowed = CONFIDENCE.get(confidence)
        if not allowed:
            continue
        sentence = _claim_sentence(after, claim)
        rank, label, marker = certainty(sentence)
        if rank > allowed:
            issues.append(
                {
                    "kind": "CERTAINTY_DRIFT",
                    "claim_id": claim.get("claim_id"),
                    "message": f"{confidence} claim стал {label}: {marker}",
                    "before": confidence,
                    "after": label,
                    "severity": "error",
                }
            )
    return issues


def main() -> int:
    parser = argparse.ArgumentParser(description="Detect certainty escalation after editing")
    parser.add_argument("before", type=Path)
    parser.add_argument("after", type=Path)
    parser.add_argument("--claims", type=Path)
    args = parser.parse_args()
    try:
        before = args.before.read_text(encoding="utf-8")
        after = args.after.read_text(encoding="utf-8")
        claims = json.loads(args.claims.read_text(encoding="utf-8")) if args.claims else None
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    issues = scan(before, after, claims)
    print(json.dumps({"accepted": not issues, "violations": issues}, ensure_ascii=False, indent=2))
    return 1 if issues else 0


if __name__ == "__main__":
    raise SystemExit(main())
