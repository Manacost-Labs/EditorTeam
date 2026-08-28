#!/usr/bin/env python3
"""Run the better-writing package's portable validation and focused self-tests."""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence


SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import probe_better_writing
import scan_aiisms
import check_preservation
import build_corpus_manifest
import check_claim_coverage
import run_forward_evals
import validate
import validate_project_profile


@dataclass(frozen=True)
class TestSummary:
    package_valid: bool
    probe_passed: bool
    scanner_passed: bool
    preservation_passed: bool
    corpus_builder_passed: bool
    project_profile_passed: bool
    claim_audit_passed: bool
    forward_harness_passed: bool
    errors: tuple[str, ...]
    warnings: tuple[str, ...]

    @property
    def passed(self) -> bool:
        return (
            self.package_valid
            and self.probe_passed
            and self.scanner_passed
            and self.preservation_passed
            and self.corpus_builder_passed
            and self.project_profile_passed
            and self.claim_audit_passed
            and self.forward_harness_passed
            and not self.errors
        )


def run_tests(skill_path: str) -> dict[str, object]:
    """Exercise the package without invoking a network or non-standard dependency."""

    root = Path(skill_path).resolve()
    validation = validate.validate_skill(str(root))
    errors = [str(error) for error in validation["errors"]]
    warnings = [str(warning) for warning in validation["warnings"]]
    probe_suite = probe_better_writing.run_suite()
    scanner_suite = scan_aiisms.run_self_tests()
    preservation_suite = check_preservation.run_self_tests()
    corpus_builder_suite = build_corpus_manifest.run_self_tests()
    project_profile_suite = validate_project_profile.run_self_tests()
    claim_audit_suite = check_claim_coverage.run_self_tests()
    forward_harness_suite = run_forward_evals.run_self_tests()
    if not probe_suite["passed"]:
        errors.append("Probe suite failed")
    if not scanner_suite["passed"]:
        errors.append("Scanner self-test failed")
    if not preservation_suite["passed"]:
        errors.append("Preservation checker self-test failed")
    if not corpus_builder_suite["passed"]:
        errors.append("Corpus builder self-test failed")
    if not project_profile_suite["passed"]:
        errors.append("Project-profile validator self-test failed")
    if not claim_audit_suite["passed"]:
        errors.append("Claim auditor self-test failed")
    if not forward_harness_suite["passed"]:
        errors.append("Forward eval harness self-test failed")
    summary = TestSummary(
        package_valid=bool(validation["valid"]),
        probe_passed=bool(probe_suite["passed"]),
        scanner_passed=bool(scanner_suite["passed"]),
        preservation_passed=bool(preservation_suite["passed"]),
        corpus_builder_passed=bool(corpus_builder_suite["passed"]),
        project_profile_passed=bool(project_profile_suite["passed"]),
        claim_audit_passed=bool(claim_audit_suite["passed"]),
        forward_harness_passed=bool(forward_harness_suite["passed"]),
        errors=tuple(errors),
        warnings=tuple(warnings),
    )
    return {
        "skill_name": root.name,
        "passed": summary.passed,
        "package_validation": validation,
        "probe_suite": probe_suite,
        "scanner_suite": scanner_suite,
        "preservation_suite": preservation_suite,
        "corpus_builder_suite": corpus_builder_suite,
        "project_profile_suite": project_profile_suite,
        "claim_audit_suite": claim_audit_suite,
        "forward_harness_suite": forward_harness_suite,
        "errors": list(summary.errors),
        "warnings": list(summary.warnings),
    }


def main(argv: Sequence[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 1:
        print("Usage: python3 test_skill.py <skill-path>", file=sys.stderr)
        return 1
    result = run_tests(args[0])
    validation = result["package_validation"]
    assert isinstance(validation, dict)
    probe = result["probe_suite"]
    scanner = result["scanner_suite"]
    preservation = result["preservation_suite"]
    corpus_builder = result["corpus_builder_suite"]
    project_profile = result["project_profile_suite"]
    claim_audit = result["claim_audit_suite"]
    forward_harness = result["forward_harness_suite"]
    assert all(
        isinstance(item, dict)
        for item in (probe, scanner, preservation, corpus_builder, project_profile, claim_audit, forward_harness)
    )
    print(f"Skill: {result['skill_name']}")
    print(f"Package validation: {'PASS' if validation['valid'] else 'FAIL'}")
    metrics = validation["metrics"]
    assert isinstance(metrics, dict)
    manifest_checks = metrics.get("manifest_checks")
    manifest_checks_passed = metrics.get("manifest_checks_passed")
    assert isinstance(manifest_checks, int) and isinstance(manifest_checks_passed, int)
    print(f"Manifest checks: {manifest_checks_passed}/{manifest_checks} passed")
    eval_schema_checks = metrics.get("eval_schema_checks")
    eval_schema_checks_passed = metrics.get("eval_schema_checks_passed")
    assert isinstance(eval_schema_checks, int) and isinstance(eval_schema_checks_passed, int)
    print(f"Eval schema checks: {eval_schema_checks_passed}/{eval_schema_checks} passed")
    eval_count = metrics.get("eval_count")
    eval_route_checks = metrics.get("eval_route_checks")
    trigger_eval_count = metrics.get("trigger_eval_count")
    trigger_route_checks = metrics.get("trigger_route_checks")
    assert isinstance(eval_count, int) and isinstance(eval_route_checks, int)
    assert isinstance(trigger_eval_count, int) and isinstance(trigger_route_checks, int)
    print(f"Eval route polarity: {eval_route_checks}/{eval_count} matched")
    print(f"Trigger route polarity: {trigger_route_checks}/{trigger_eval_count} matched")
    package_path_checks = metrics.get("package_path_checks")
    package_path_checks_passed = metrics.get("package_path_checks_passed")
    assert isinstance(package_path_checks, int) and isinstance(package_path_checks_passed, int)
    print(f"Package path checks: {package_path_checks_passed}/{package_path_checks} passed")
    print(f"Probe checks: {probe['summary']['checks_passed']}/{probe['summary']['checks_total']} passed")
    scanner_checks = scanner["checks"]
    assert isinstance(scanner_checks, dict)
    print(f"Scanner checks: {sum(1 for passed in scanner_checks.values() if passed)}/{len(scanner_checks)} passed")
    preservation_checks = preservation["checks"]
    assert isinstance(preservation_checks, dict)
    print(
        "Preservation checks: "
        f"{sum(1 for passed in preservation_checks.values() if passed)}/{len(preservation_checks)} passed"
    )
    corpus_checks = corpus_builder["checks"]
    assert isinstance(corpus_checks, dict)
    print(f"Corpus checks: {sum(1 for passed in corpus_checks.values() if passed)}/{len(corpus_checks)} passed")
    project_profile_checks = project_profile["checks"]
    assert isinstance(project_profile_checks, dict)
    print(
        "Project-profile checks: "
        f"{sum(1 for passed in project_profile_checks.values() if passed)}/{len(project_profile_checks)} passed"
    )
    claim_audit_checks = claim_audit["checks"]
    assert isinstance(claim_audit_checks, dict)
    print(
        "Claim-audit checks: "
        f"{sum(1 for passed in claim_audit_checks.values() if passed)}/{len(claim_audit_checks)} passed"
    )
    forward_checks = forward_harness["checks"]
    assert isinstance(forward_checks, dict)
    print(f"Forward harness checks: {sum(1 for passed in forward_checks.values() if passed)}/{len(forward_checks)} passed")
    print(f"Forward behavior cases available: {metrics.get('forward_eval_count', 0)}")
    print(
        "Forward baselines: "
        f"{metrics.get('forward_baseline_cases_passed', 0)}/{metrics.get('forward_baseline_cases', 0)} passed"
    )
    if result["warnings"]:
        print("\nWarnings:")
        for warning in result["warnings"]:
            print(f"- {warning}")
    if result["errors"]:
        print("\nIssues:")
        for error in result["errors"]:
            print(f"- {error}")
    print("\nPASS: all checks passed" if result["passed"] else "\nFAIL: one or more checks failed")
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
