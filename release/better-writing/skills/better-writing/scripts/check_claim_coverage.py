#!/usr/bin/env python3
"""Audit a JSON or JSONL claim ledger for traceable draft coverage."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Sequence


ALLOWED_KINDS = frozenset({"source_claim", "editorial_synthesis"})
ALLOWED_STATUSES = frozenset({"supported", "conflicted", "unresolved", "excluded"})
ALLOWED_CONFIDENCE = frozenset({"high", "medium", "low", "unknown"})


def load_claims(path: Path) -> tuple[list[object], list[str]]:
    errors: list[str] = []
    try:
        content = path.read_text(encoding="utf-8")
    except OSError as exc:
        return [], [str(exc)]
    if path.suffix.lower() == ".json":
        try:
            payload = json.loads(content)
        except json.JSONDecodeError as exc:
            return [], [f"invalid JSON: {exc}"]
        if isinstance(payload, dict):
            payload = payload.get("claims")
        if not isinstance(payload, list):
            return [], ["JSON claim ledger must be a list or an object with a claims list"]
        return payload, []
    claims: list[object] = []
    for line_number, line in enumerate(content.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            claims.append(json.loads(line))
        except json.JSONDecodeError as exc:
            errors.append(f"line {line_number}: invalid JSON: {exc}")
    return claims, errors


def audit_claims(claims: list[object], strict_confidence: bool = False) -> dict[str, object]:
    errors: list[str] = []
    warnings: list[str] = []
    seen_ids: set[str] = set()
    counters = {
        "claims": 0,
        "material": 0,
        "used": 0,
        "supported": 0,
        "conflicted": 0,
        "unresolved": 0,
        "excluded": 0,
        "tracked_used_material": 0,
        "used_material": 0,
    }
    for index, claim in enumerate(claims):
        location = f"claims[{index}]"
        if not isinstance(claim, dict):
            errors.append(f"{location} must be an object")
            continue
        counters["claims"] += 1
        claim_id = claim.get("claim_id")
        if not isinstance(claim_id, str) or not claim_id.strip():
            errors.append(f"{location}.claim_id must be a non-empty string")
            claim_label = location
        else:
            claim_label = claim_id.strip()
            if claim_label in seen_ids:
                errors.append(f"{location}.claim_id duplicates {claim_label}")
            seen_ids.add(claim_label)
        if not isinstance(claim.get("text"), str) or not str(claim.get("text", "")).strip():
            errors.append(f"{claim_label}: text must be a non-empty string")
        kind = claim.get("kind")
        status = claim.get("status")
        confidence = claim.get("confidence")
        if kind not in ALLOWED_KINDS:
            errors.append(f"{claim_label}: kind must be source_claim or editorial_synthesis")
        if status not in ALLOWED_STATUSES:
            errors.append(f"{claim_label}: invalid status")
        else:
            counters[str(status)] += 1
        if confidence not in ALLOWED_CONFIDENCE:
            errors.append(f"{claim_label}: invalid confidence")
        material = claim.get("material")
        used = claim.get("used_in_draft")
        if not isinstance(material, bool):
            errors.append(f"{claim_label}: material must be a boolean")
            material = False
        if not isinstance(used, bool):
            errors.append(f"{claim_label}: used_in_draft must be a boolean")
            used = False
        if material:
            counters["material"] += 1
        if used:
            counters["used"] += 1
        if material and used:
            counters["used_material"] += 1
        locators = claim.get("source_locators")
        valid_locators = isinstance(locators, list) and all(
            isinstance(item, str) and bool(item.strip()) for item in locators
        )
        if not valid_locators:
            errors.append(f"{claim_label}: source_locators must be a list of non-empty strings")
            locator_count = 0
        else:
            assert isinstance(locators, list)
            locator_count = len(set(item.strip() for item in locators))
            if locator_count != len(locators):
                warnings.append(f"{claim_label}: duplicate source locators")

        tracked = False
        if kind == "source_claim":
            if status == "supported" and used and locator_count == 0:
                errors.append(f"{claim_label}: a used supported source claim needs a locator")
            if status == "conflicted":
                if locator_count < 2:
                    errors.append(f"{claim_label}: a conflicted claim needs at least two locators")
                if used and claim.get("conflict_disclosed") is not True:
                    errors.append(f"{claim_label}: a used conflict must set conflict_disclosed to true")
            tracked = status in {"supported", "conflicted"} and locator_count > 0
        elif kind == "editorial_synthesis":
            if used and claim.get("labelled") is not True:
                errors.append(f"{claim_label}: used editorial synthesis must set labelled to true")
            tracked = status == "supported" and (not used or claim.get("labelled") is True)

        if status in {"unresolved", "excluded"} and used:
            errors.append(f"{claim_label}: {status} claim cannot be used in the draft")
        if material and used and confidence in {"low", "unknown"}:
            message = f"{claim_label}: used material claim has {confidence} confidence"
            if strict_confidence:
                errors.append(message)
            else:
                warnings.append(message)
        if material and used and tracked:
            counters["tracked_used_material"] += 1

    denominator = counters["used_material"]
    coverage = 100.0 if denominator == 0 else round(100 * counters["tracked_used_material"] / denominator, 1)
    summary: dict[str, object] = {**counters, "coverage_percent": coverage}
    return {"passed": not errors, "errors": errors, "warnings": warnings, "summary": summary}


def audit_path(path: Path, strict_confidence: bool = False) -> dict[str, object]:
    claims, load_errors = load_claims(path)
    result = audit_claims(claims, strict_confidence=strict_confidence)
    if load_errors:
        errors = [*load_errors, *result["errors"]]
        result["errors"] = errors
        result["passed"] = False
    return result


def run_self_tests() -> dict[str, object]:
    supported = {
        "claim_id": "C1",
        "text": "Observed result",
        "kind": "source_claim",
        "status": "supported",
        "confidence": "high",
        "source_locators": ["S1#p=1"],
        "material": True,
        "used_in_draft": True,
    }
    conflicted = {
        **supported,
        "claim_id": "C2",
        "status": "conflicted",
        "confidence": "medium",
        "source_locators": ["S1#p=1", "S2#p=4"],
        "conflict_disclosed": True,
    }
    synthesis = {
        **supported,
        "claim_id": "C3",
        "kind": "editorial_synthesis",
        "source_locators": [],
        "labelled": True,
    }
    checks = {
        "accepts_tracked_claims": audit_claims([supported, conflicted, synthesis])["passed"] is True,
        "reports_full_coverage": audit_claims([supported, conflicted, synthesis])["summary"]["coverage_percent"] == 100.0,
        "rejects_unsourced_used_claim": audit_claims([{**supported, "source_locators": []}])["passed"] is False,
        "rejects_undisclosed_conflict": audit_claims([{**conflicted, "conflict_disclosed": False}])["passed"] is False,
        "rejects_unlabelled_synthesis": audit_claims([{**synthesis, "labelled": False}])["passed"] is False,
        "strict_mode_rejects_low_confidence": audit_claims(
            [{**supported, "confidence": "low"}], strict_confidence=True
        )["passed"] is False,
    }
    return {"passed": all(checks.values()), "checks": checks}


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Audit claim-ledger coverage for a source-backed draft.")
    parser.add_argument("ledger", nargs="?", type=Path, help="JSONL or JSON claim ledger")
    parser.add_argument("--strict-confidence", action="store_true", help="Fail on low or unknown used material")
    parser.add_argument("--gate", action="store_true", help="Exit non-zero when the audit fails")
    parser.add_argument("--self-test", action="store_true", help="Run built-in tests")
    parser.add_argument("--format", choices=("text", "json"), default="text")
    args = parser.parse_args(argv)
    if args.self_test:
        result = run_self_tests()
    else:
        if args.ledger is None:
            parser.error("ledger is required unless --self-test is used")
        result = audit_path(args.ledger, strict_confidence=args.strict_confidence)
    if args.format == "json" or args.self_test:
        print(json.dumps(result, indent=2, ensure_ascii=False, sort_keys=True))
    else:
        summary = result.get("summary", {})
        print("Claim coverage: PASS" if result.get("passed") else "Claim coverage: FAIL")
        if isinstance(summary, dict):
            print(
                f"Coverage {summary.get('coverage_percent', 0)}%; "
                f"used material {summary.get('used_material', 0)}; "
                f"tracked {summary.get('tracked_used_material', 0)}"
            )
        for warning in result.get("warnings", []):
            print(f"WARNING: {warning}")
        for error in result.get("errors", []):
            print(f"ERROR: {error}")
    return 1 if args.gate and not result.get("passed") else 0


if __name__ == "__main__":
    raise SystemExit(main())
