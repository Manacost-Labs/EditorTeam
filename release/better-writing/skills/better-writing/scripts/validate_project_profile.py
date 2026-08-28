#!/usr/bin/env python3
"""Validate a reusable Better Writing project profile using the standard library."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Sequence


ALLOWED_INTERVENTIONS = frozenset({"minimal", "standard", "deep"})
ALLOWED_MODES = frozenset({"clean", "annotated", "review-only", "side-by-side"})


def nonempty_string(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def validate_string_list(value: object, location: str, errors: list[str]) -> list[str]:
    if not isinstance(value, list):
        errors.append(f"{location} must be a list")
        return []
    items: list[str] = []
    seen: set[str] = set()
    for index, item in enumerate(value):
        if not nonempty_string(item):
            errors.append(f"{location}[{index}] must be a non-empty string")
            continue
        assert isinstance(item, str)
        folded = item.strip().casefold()
        if folded in seen:
            errors.append(f"{location}[{index}] duplicates an earlier value")
            continue
        seen.add(folded)
        items.append(item.strip())
    return items


def validate_profile(payload: object) -> dict[str, object]:
    errors: list[str] = []
    warnings: list[str] = []
    if not isinstance(payload, dict):
        return {"valid": False, "errors": ["profile must contain a JSON object"], "warnings": [], "summary": {}}
    if payload.get("schema_version") != 1:
        errors.append("schema_version must equal 1")

    project = payload.get("project")
    if not isinstance(project, dict):
        errors.append("project must be an object")
        project = {}
    if not nonempty_string(project.get("name")):
        errors.append("project.name must be a non-empty string")
    if not nonempty_string(project.get("default_locale")):
        errors.append("project.default_locale must be a non-empty string")

    audiences = payload.get("audiences", [])
    audience_ids: list[str] = []
    if not isinstance(audiences, list):
        errors.append("audiences must be a list")
    else:
        seen_audiences: set[str] = set()
        for index, audience in enumerate(audiences):
            location = f"audiences[{index}]"
            if not isinstance(audience, dict):
                errors.append(f"{location} must be an object")
                continue
            audience_id = audience.get("id")
            if not nonempty_string(audience_id):
                errors.append(f"{location}.id must be a non-empty string")
            else:
                assert isinstance(audience_id, str)
                folded = audience_id.strip().casefold()
                if folded in seen_audiences:
                    errors.append(f"{location}.id duplicates an earlier audience")
                else:
                    seen_audiences.add(folded)
                    audience_ids.append(audience_id.strip())
            for key in ("needs", "avoid"):
                if key in audience:
                    validate_string_list(audience[key], f"{location}.{key}", errors)
    if isinstance(audiences, list) and not audiences:
        warnings.append("profile has no named audiences")

    deliverables = payload.get("deliverables", {})
    allowed_deliverables: list[str] = []
    if not isinstance(deliverables, dict):
        errors.append("deliverables must be an object")
    else:
        allowed_deliverables = validate_string_list(deliverables.get("allowed", []), "deliverables.allowed", errors)
        default_deliverable = deliverables.get("default")
        if not nonempty_string(default_deliverable):
            errors.append("deliverables.default must be a non-empty string")
        elif allowed_deliverables and str(default_deliverable).casefold() not in {
            item.casefold() for item in allowed_deliverables
        }:
            errors.append("deliverables.default must appear in deliverables.allowed")

    terminology = payload.get("terminology", {})
    protected_terms: list[str] = []
    avoided_terms: list[str] = []
    preferred_count = 0
    if not isinstance(terminology, dict):
        errors.append("terminology must be an object")
    else:
        preferred = terminology.get("preferred", {})
        if not isinstance(preferred, dict):
            errors.append("terminology.preferred must be an object")
        else:
            for source, target in preferred.items():
                if not nonempty_string(source) or not nonempty_string(target):
                    errors.append("terminology.preferred keys and values must be non-empty strings")
                    continue
                preferred_count += 1
        protected_terms = validate_string_list(terminology.get("protected", []), "terminology.protected", errors)
        avoided_terms = validate_string_list(terminology.get("avoid", []), "terminology.avoid", errors)
        overlap = sorted({item.casefold() for item in protected_terms} & {item.casefold() for item in avoided_terms})
        if overlap:
            warnings.append("terms appear in both protected and avoid lists: " + ", ".join(overlap))

    authority = payload.get("authority", {})
    approved_fact_count = 0
    if not isinstance(authority, dict):
        errors.append("authority must be an object")
    else:
        validate_string_list(authority.get("source_precedence", []), "authority.source_precedence", errors)
        validate_string_list(authority.get("restricted_claims", []), "authority.restricted_claims", errors)
        for key in ("citation_style", "uncertainty_policy"):
            if not nonempty_string(authority.get(key)):
                errors.append(f"authority.{key} must be a non-empty string")
        facts = authority.get("approved_facts", [])
        if not isinstance(facts, list):
            errors.append("authority.approved_facts must be a list")
        else:
            for index, fact in enumerate(facts):
                location = f"authority.approved_facts[{index}]"
                if not isinstance(fact, dict):
                    errors.append(f"{location} must be an object")
                    continue
                if not nonempty_string(fact.get("text")):
                    errors.append(f"{location}.text must be a non-empty string")
                if not nonempty_string(fact.get("source_locator")):
                    errors.append(f"{location}.source_locator must be a non-empty string")
                approved_fact_count += 1

    voice = payload.get("voice", {})
    if not isinstance(voice, dict):
        errors.append("voice must be an object")
    else:
        validate_string_list(voice.get("traits", []), "voice.traits", errors)
        validate_string_list(voice.get("avoid", []), "voice.avoid", errors)

    output = payload.get("output", {})
    if not isinstance(output, dict):
        errors.append("output must be an object")
    else:
        intervention = output.get("default_intervention")
        mode = output.get("default_mode")
        if intervention not in ALLOWED_INTERVENTIONS:
            errors.append("output.default_intervention must be minimal, standard, or deep")
        if mode not in ALLOWED_MODES:
            errors.append("output.default_mode must be clean, annotated, review-only, or side-by-side")

    privacy = payload.get("privacy", {})
    if not isinstance(privacy, dict):
        errors.append("privacy must be an object")
    else:
        validate_string_list(privacy.get("sensitive_categories", []), "privacy.sensitive_categories", errors)
        if not nonempty_string(privacy.get("publication_rule")):
            errors.append("privacy.publication_rule must be a non-empty string")

    return {
        "valid": not errors,
        "errors": errors,
        "warnings": warnings,
        "summary": {
            "audiences": len(audience_ids),
            "deliverables": len(allowed_deliverables),
            "preferred_terms": preferred_count,
            "protected_terms": len(protected_terms),
            "approved_facts": approved_fact_count,
        },
    }


def run_self_tests() -> dict[str, object]:
    valid = {
        "schema_version": 1,
        "project": {"name": "Test", "default_locale": "ru-RU"},
        "audiences": [{"id": "reader", "needs": ["facts"], "avoid": []}],
        "deliverables": {"default": "report", "allowed": ["report"]},
        "terminology": {"preferred": {"old": "new"}, "protected": ["API"], "avoid": []},
        "authority": {
            "source_precedence": ["primary"],
            "approved_facts": [{"text": "Observed", "source_locator": "S1#p=1"}],
            "restricted_claims": [],
            "citation_style": "source-page",
            "uncertainty_policy": "preserve",
        },
        "voice": {"traits": ["direct"], "avoid": []},
        "output": {"default_intervention": "standard", "default_mode": "clean"},
        "privacy": {"sensitive_categories": ["credentials"], "publication_rule": "request approval"},
    }
    checks = {
        "accepts_valid_profile": validate_profile(valid)["valid"] is True,
        "rejects_missing_source_locator": validate_profile({
            **valid,
            "authority": {**valid["authority"], "approved_facts": [{"text": "Observed"}]},
        })["valid"] is False,
        "rejects_invalid_output_mode": validate_profile({
            **valid,
            "output": {"default_intervention": "standard", "default_mode": "verbose"},
        })["valid"] is False,
        "warns_on_term_conflict": bool(validate_profile({
            **valid,
            "terminology": {"preferred": {}, "protected": ["API"], "avoid": ["api"]},
        })["warnings"]),
    }
    return {"passed": all(checks.values()), "checks": checks}


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate a Better Writing project profile.")
    parser.add_argument("profile", nargs="?", type=Path, help="Path to project-profile.json")
    parser.add_argument("--gate", action="store_true", help="Exit non-zero when the profile is invalid")
    parser.add_argument("--self-test", action="store_true", help="Run built-in tests")
    parser.add_argument("--format", choices=("text", "json"), default="text")
    args = parser.parse_args(argv)
    if args.self_test:
        result = run_self_tests()
    else:
        if args.profile is None:
            parser.error("profile is required unless --self-test is used")
        try:
            payload = json.loads(args.profile.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            result = {"valid": False, "errors": [str(exc)], "warnings": [], "summary": {}}
        else:
            result = validate_profile(payload)
    if args.format == "json" or args.self_test:
        print(json.dumps(result, indent=2, ensure_ascii=False, sort_keys=True))
    else:
        print("Project profile: PASS" if result["valid"] else "Project profile: FAIL")
        for warning in result.get("warnings", []):
            print(f"WARNING: {warning}")
        for error in result.get("errors", []):
            print(f"ERROR: {error}")
    return 1 if args.gate and not result.get("valid") else 0


if __name__ == "__main__":
    raise SystemExit(main())
