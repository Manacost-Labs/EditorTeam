#!/usr/bin/env python3
"""Compare source and revised prose for deterministic fidelity regressions.

The checker is deliberately conservative. It can prove that selected exact
literals survived; it cannot prove that paraphrased facts, names, causality, or
voice remain faithful.

Usage:
    python3 check_preservation.py source.md revision.md
    python3 check_preservation.py source.md revision.md --gate
    python3 check_preservation.py source.md revision.md --gate --strict-additions
    python3 check_preservation.py --self-test
"""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from pathlib import Path
from typing import Iterable, Sequence


FENCED_CODE_RE = re.compile(r"(?ms)^(`{3,}|~{3,})[^\n]*\n.*?^\1[ \t]*$")
INLINE_CODE_RE = re.compile(r"(?<!`)`([^`\n]+)`(?!`)")
URL_RE = re.compile(r"https?://[^\s<>\]\[{}]+", re.IGNORECASE)
QUOTE_RES = (
    re.compile(r"«[^»\n]+»"),
    re.compile(r"“[^”\n]+”"),
    re.compile(r'(?<![\w=])"[^"\n]{2,}"'),
)
PATH_RE = re.compile(r"(?<![\w:])(?:\.\.?/|/)[^\s`\"'<>]+")
FLAG_RE = re.compile(r"(?<![\w-])--?[A-Za-z][A-Za-z0-9_-]*")
ASSIGNMENT_RE = re.compile(r"\b[A-Z][A-Z0-9_]{1,}=[^\s`\"']+")
NUMBER_RE = re.compile(r"(?<![\w])[-+]?\d+(?:[.,]\d+)*(?:[ \u00a0]?(?:%|‰))?(?![\w])")

UNCERTAINTY_PATTERNS: tuple[tuple[str, str], ...] = (
    ("may", r"\bmay\b"),
    ("might", r"\bmight\b"),
    ("could", r"\bcould\b"),
    ("likely", r"\blikely\b"),
    ("unlikely", r"\bunlikely\b"),
    ("possibly", r"\bpossibly\b"),
    ("probably", r"\bprobably\b"),
    ("approximately", r"\bapproximately\b"),
    ("estimated", r"\bestimat(?:e|ed|es|ing|ion)\b"),
    ("not tested", r"\bnot\s+tested\b"),
    ("not verified", r"\bnot\s+verified\b"),
    ("unknown", r"\bunknown\b"),
    ("uncertain", r"\buncertain(?:ty)?\b"),
    ("может", r"\bможет\b"),
    ("могут", r"\bмогут\b"),
    ("могло", r"\bмогло\b"),
    ("возможно", r"\bвозможно\b"),
    ("вероятно", r"\bвероятно\b"),
    ("маловероятно", r"\bмаловероятно\b"),
    ("предположительно", r"\bпредположительно\b"),
    ("примерно", r"\bпримерно\b"),
    ("около", r"\bоколо\b"),
    ("по оценке", r"\bпо\s+оценке\b"),
    ("оценочно", r"\bоценочно\b"),
    ("не проверено", r"\bне\s+проверен(?:о|а|ы)?\b"),
    ("не тестировалось", r"\bне\s+тестировал(?:ось|ась|ись)?\b"),
    ("неизвестно", r"\bнеизвестно\b"),
    ("неясно", r"\bнеясно\b"),
    ("нет данных", r"\bнет\s+данных\b"),
)


def _counter(values: Iterable[str]) -> Counter[str]:
    return Counter(value for value in values if value)


def _trim_url(value: str) -> str:
    return value.rstrip(".,;:!?)]}»”")


def extract_candidates(text: str) -> dict[str, Counter[str]]:
    """Extract exact or epistemically meaningful candidates from prose."""

    quoted: list[str] = []
    for pattern in QUOTE_RES:
        quoted.extend(match.group(0) for match in pattern.finditer(text))
    paths = [
        match.group(0).rstrip(".,;:!?)]}")
        for match in PATH_RE.finditer(text)
        if not text[max(0, match.start() - 8) : match.start()].lower().endswith(("http:/", "https:/"))
    ]
    uncertainty = {
        label: len(re.findall(pattern, text, flags=re.IGNORECASE))
        for label, pattern in UNCERTAINTY_PATTERNS
        if re.search(pattern, text, flags=re.IGNORECASE)
    }
    return {
        "fenced_code": _counter(match.group(0) for match in FENCED_CODE_RE.finditer(text)),
        "inline_code": _counter(match.group(1) for match in INLINE_CODE_RE.finditer(text)),
        "quotation": _counter(quoted),
        "url": _counter(_trim_url(match.group(0)) for match in URL_RE.finditer(text)),
        "path": _counter(paths),
        "flag": _counter(match.group(0) for match in FLAG_RE.finditer(text)),
        "assignment": _counter(match.group(0) for match in ASSIGNMENT_RE.finditer(text)),
        "number": _counter(match.group(0) for match in NUMBER_RE.finditer(text)),
        "uncertainty": Counter(uncertainty),
    }


def _actual_count(category: str, value: str, revised: str, revised_candidates: dict[str, Counter[str]]) -> int:
    if category == "uncertainty":
        pattern = dict(UNCERTAINTY_PATTERNS)[value]
        return len(re.findall(pattern, revised, flags=re.IGNORECASE))
    return revised_candidates[category][value]


def analyse_texts(source: str, revised: str, *, strict_additions: bool = False) -> dict[str, object]:
    """Return a machine-readable preservation report for two strings."""

    source_candidates = extract_candidates(source)
    revised_candidates = extract_candidates(revised)
    missing: list[dict[str, object]] = []
    for category, values in source_candidates.items():
        for value, expected_count in values.items():
            actual_count = _actual_count(category, value, revised, revised_candidates)
            if actual_count < expected_count:
                missing.append(
                    {
                        "category": category,
                        "value": value,
                        "expected_count": expected_count,
                        "actual_count": actual_count,
                    }
                )

    new_numbers: list[dict[str, object]] = []
    for value, actual_count in revised_candidates["number"].items():
        source_count = source_candidates["number"][value]
        if actual_count > source_count:
            new_numbers.append(
                {
                    "value": value,
                    "source_count": source_count,
                    "revised_count": actual_count,
                }
            )

    passed = not missing and (not strict_additions or not new_numbers)
    return {
        "passed": passed,
        "strict_additions": strict_additions,
        "missing": missing,
        "new_numbers": new_numbers,
        "summary": {
            "protected_candidates": sum(sum(values.values()) for values in source_candidates.values()),
            "missing_candidates": sum(
                int(item["expected_count"]) - int(item["actual_count"]) for item in missing
            ),
            "new_numeric_tokens": sum(
                int(item["revised_count"]) - int(item["source_count"]) for item in new_numbers
            ),
        },
        "limits": [
            "Names, paraphrased facts, causality, scope, and voice still require semantic review.",
            "Added numeric tokens are warnings unless strict additions mode is enabled.",
        ],
    }


def run_self_tests() -> dict[str, object]:
    source = (
        'The result may be 18.4% in Q3. "Not tested in production." '
        'Run `make verify` at https://example.com/a.\n\n'
        '```yaml\nMODE: staging\n```\n'
        'По предварительной оценке, задержка может составить около 12 мс.'
    )
    preserved = source.replace("The result", "The observed result")
    missing_number = preserved.replace("18.4%", "19.1%")
    missing_uncertainty = preserved.replace("может", "будет")
    added_number = preserved + "\nШаг 2."
    checks = {
        "accepts_preserved_literals": bool(analyse_texts(source, preserved)["passed"]),
        "rejects_changed_number": not bool(analyse_texts(source, missing_number)["passed"]),
        "rejects_removed_russian_uncertainty": not bool(analyse_texts(source, missing_uncertainty)["passed"]),
        "warns_on_added_number": bool(analyse_texts(source, added_number)["new_numbers"]),
        "strict_additions_rejects_added_number": not bool(
            analyse_texts(source, added_number, strict_additions=True)["passed"]
        ),
        "preserves_fenced_code": not bool(
            analyse_texts(source, preserved.replace("MODE: staging", "MODE: production"))["passed"]
        ),
    }
    return {
        "passed": all(checks.values()),
        "checks": checks,
        "summary": {
            "checks_total": len(checks),
            "checks_passed": sum(1 for passed in checks.values() if passed),
        },
    }


def _format_text(report: dict[str, object]) -> str:
    summary = report["summary"]
    assert isinstance(summary, dict)
    lines = [
        f"Preservation: {'PASS' if report['passed'] else 'FAIL'}",
        f"Protected candidates: {summary['protected_candidates']}",
        f"Missing candidates: {summary['missing_candidates']}",
        f"New numeric tokens: {summary['new_numeric_tokens']}",
    ]
    missing = report["missing"]
    assert isinstance(missing, list)
    for item in missing:
        assert isinstance(item, dict)
        lines.append(
            f"MISSING {item['category']}: {item['value']!r} "
            f"({item['actual_count']}/{item['expected_count']})"
        )
    new_numbers = report["new_numbers"]
    assert isinstance(new_numbers, list)
    for item in new_numbers:
        assert isinstance(item, dict)
        lines.append(
            f"NEW NUMBER: {item['value']!r} ({item['source_count']} -> {item['revised_count']})"
        )
    lines.append("Semantic fidelity still requires editorial review.")
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check exact-literal preservation between source and revision.")
    parser.add_argument("source", nargs="?", help="Source prose file")
    parser.add_argument("revision", nargs="?", help="Revised prose file")
    parser.add_argument("--format", choices=("text", "json"), default="text")
    parser.add_argument("--gate", action="store_true", help="Exit non-zero when the report fails")
    parser.add_argument(
        "--strict-additions",
        action="store_true",
        help="Treat numeric tokens added by the revision as failures",
    )
    parser.add_argument("--self-test", action="store_true", help="Run built-in checks")
    args = parser.parse_args(argv)

    if args.self_test:
        result = run_self_tests()
        print(json.dumps(result, ensure_ascii=False, indent=2) if args.format == "json" else _format_self_test(result))
        return 0 if result["passed"] else 1
    if not args.source or not args.revision:
        parser.error("source and revision are required unless --self-test is used")

    source = Path(args.source).read_text(encoding="utf-8")
    revised = Path(args.revision).read_text(encoding="utf-8")
    report = analyse_texts(source, revised, strict_additions=args.strict_additions)
    print(json.dumps(report, ensure_ascii=False, indent=2) if args.format == "json" else _format_text(report))
    return 1 if args.gate and not report["passed"] else 0


def _format_self_test(result: dict[str, object]) -> str:
    checks = result["checks"]
    assert isinstance(checks, dict)
    lines = [f"Self-test: {'PASS' if result['passed'] else 'FAIL'}"]
    lines.extend(f"{'PASS' if passed else 'FAIL'}: {name}" for name, passed in checks.items())
    return "\n".join(lines)


if __name__ == "__main__":
    raise SystemExit(main())
