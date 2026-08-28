#!/usr/bin/env python3
"""Run or grade observable better-writing model behavior.

The runner command receives one complete evaluation prompt on stdin and must
return the model response on stdout. Without a runner, existing `<case-id>.md`
files can be graded from a directory.

Examples:
    python3 run_forward_evals.py <skill-path> --list
    python3 run_forward_evals.py <skill-path> --grade-dir /tmp/bw-outputs --gate
    python3 run_forward_evals.py <skill-path> --output-dir /tmp/bw-outputs \
        --runner my-model-command --stdin
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path
from typing import Sequence


SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import check_preservation


@dataclass(frozen=True)
class CaseResult:
    case_id: str
    passed: bool
    failures: tuple[str, ...]
    warnings: tuple[str, ...]
    metrics: dict[str, object]


def load_manifest(root: Path) -> dict[str, object]:
    path = root / "evals" / "forward-evals.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or payload.get("skill_name") != root.name:
        raise ValueError("forward eval skill_name must match the skill directory")
    cases = payload.get("cases")
    if not isinstance(cases, list) or not cases:
        raise ValueError("forward eval manifest must contain cases")
    return payload


def source_text(root: Path, case: dict[str, object]) -> str:
    files = case.get("files", [])
    assert isinstance(files, list)
    sections: list[str] = []
    for relative in files:
        if not isinstance(relative, str):
            raise ValueError("case file paths must be strings")
        path = (root / relative).resolve()
        if root not in path.parents or not path.is_file():
            raise ValueError(f"case file is missing or escapes the skill: {relative}")
        sections.append(path.read_text(encoding="utf-8"))
    return "\n\n".join(sections)


def build_prompt(root: Path, case: dict[str, object]) -> str:
    prompt = case.get("prompt")
    if not isinstance(prompt, str) or not prompt.strip():
        raise ValueError("case prompt must be a non-empty string")
    fixture = source_text(root, case)
    return (
        f"Use the better-writing skill at {root}.\n\n"
        f"User request:\n{prompt.strip()}\n\n"
        f"Source material:\n{fixture}\n"
    )


def _paragraph_count(text: str) -> int:
    return len([part for part in re.split(r"\n\s*\n", text.strip()) if part.strip()])


def _word_count(text: str) -> int:
    return len(re.findall(r"\b[\wЁё-]+\b", text, flags=re.UNICODE))


def grade_case(root: Path, case: dict[str, object], output: str) -> CaseResult:
    case_id = case.get("id")
    checks = case.get("checks")
    if not isinstance(case_id, str) or not isinstance(checks, dict):
        raise ValueError("each case needs string id and object checks")
    fixture = source_text(root, case)
    failures: list[str] = []
    warnings: list[str] = []
    metrics: dict[str, object] = {
        "words": _word_count(output),
        "paragraphs": _paragraph_count(output),
    }

    if checks.get("preserve_all") is True:
        preservation = check_preservation.analyse_texts(
            fixture,
            output,
            strict_additions=checks.get("strict_additions") is True,
        )
        metrics["preservation"] = preservation["summary"]
        if not preservation["passed"]:
            missing = preservation["missing"]
            assert isinstance(missing, list)
            labels = [f"{item['category']}={item['value']!r}" for item in missing if isinstance(item, dict)]
            if checks.get("strict_additions") is True and preservation["new_numbers"]:
                labels.append("new numeric tokens")
            failures.append("preservation failed: " + ", ".join(labels))
        elif preservation["new_numbers"]:
            warnings.append("revision contains new numeric tokens")

    output_folded = output.casefold()
    for value in checks.get("must_contain", []):
        if isinstance(value, str) and value.casefold() not in output_folded:
            failures.append(f"missing required text: {value!r}")
    must_contain_any = checks.get("must_contain_any", [])
    if isinstance(must_contain_any, list) and must_contain_any:
        if not any(isinstance(value, str) and value.casefold() in output_folded for value in must_contain_any):
            failures.append("none of must_contain_any values appeared")
    for value in checks.get("must_not_contain", []):
        if isinstance(value, str) and value.casefold() in output_folded:
            failures.append(f"forbidden text appeared: {value!r}")
    for value in checks.get("must_not_contain_case_sensitive", []):
        if isinstance(value, str) and value in output:
            failures.append(f"case-sensitive forbidden text appeared: {value!r}")
    for pattern in checks.get("required_regex", []):
        if isinstance(pattern, str) and not re.search(pattern, output):
            failures.append(f"required regex did not match: {pattern!r}")
    for pattern in checks.get("forbidden_regex", []):
        if isinstance(pattern, str) and re.search(pattern, output):
            failures.append(f"forbidden regex matched: {pattern!r}")

    maximum_words = checks.get("maximum_words")
    if isinstance(maximum_words, int) and metrics["words"] > maximum_words:
        failures.append(f"word count {metrics['words']} exceeds {maximum_words}")
    minimum_paragraphs = checks.get("minimum_paragraphs")
    if isinstance(minimum_paragraphs, int) and metrics["paragraphs"] < minimum_paragraphs:
        failures.append(f"paragraph count {metrics['paragraphs']} is below {minimum_paragraphs}")
    minimum_similarity = checks.get("minimum_similarity")
    if isinstance(minimum_similarity, (int, float)) and not isinstance(minimum_similarity, bool):
        similarity = SequenceMatcher(None, fixture, output).ratio()
        metrics["similarity"] = round(similarity, 4)
        if similarity < float(minimum_similarity):
            failures.append(f"similarity {similarity:.3f} is below {float(minimum_similarity):.3f}")

    return CaseResult(case_id, not failures, tuple(failures), tuple(warnings), metrics)


def run_command(command: list[str], prompt: str, timeout: int) -> str:
    completed = subprocess.run(
        command,
        input=prompt,
        text=True,
        capture_output=True,
        timeout=timeout,
        check=False,
    )
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip() or "no error output"
        raise RuntimeError(f"runner exited {completed.returncode}: {detail}")
    if not completed.stdout.strip():
        raise RuntimeError("runner returned empty stdout")
    return completed.stdout


def validate_manifest(root: Path, payload: dict[str, object]) -> list[str]:
    errors: list[str] = []
    baseline_dir = payload.get("baseline_dir")
    if not isinstance(baseline_dir, str) or not baseline_dir.strip():
        errors.append("baseline_dir must be a non-empty string")
    else:
        resolved_baseline = (root / baseline_dir).resolve()
        if root not in resolved_baseline.parents or not resolved_baseline.is_dir():
            errors.append("baseline_dir is missing or escapes the skill")
    seen: set[str] = set()
    cases = payload.get("cases")
    assert isinstance(cases, list)
    allowed_checks = {
        "preserve_all",
        "strict_additions",
        "must_contain",
        "must_contain_any",
        "must_not_contain",
        "must_not_contain_case_sensitive",
        "required_regex",
        "forbidden_regex",
        "maximum_words",
        "minimum_paragraphs",
        "minimum_similarity",
    }
    for index, raw_case in enumerate(cases):
        location = f"cases[{index}]"
        if not isinstance(raw_case, dict):
            errors.append(f"{location} must be an object")
            continue
        case_id = raw_case.get("id")
        if not isinstance(case_id, str) or not re.fullmatch(r"[a-z0-9-]+", case_id):
            errors.append(f"{location}.id must use lowercase hyphen-case")
        elif case_id in seen:
            errors.append(f"{location}.id is duplicated")
        else:
            seen.add(case_id)
        if not isinstance(raw_case.get("prompt"), str) or not raw_case["prompt"].strip():
            errors.append(f"{location}.prompt must be non-empty")
        checks = raw_case.get("checks")
        if not isinstance(checks, dict) or not checks:
            errors.append(f"{location}.checks must be a non-empty object")
        else:
            unknown = sorted(set(checks) - allowed_checks)
            if unknown:
                errors.append(f"{location}.checks has unknown keys: {', '.join(unknown)}")
            for key in (
                "must_contain",
                "must_contain_any",
                "must_not_contain",
                "must_not_contain_case_sensitive",
                "required_regex",
                "forbidden_regex",
            ):
                value = checks.get(key, [])
                if not isinstance(value, list) or not all(isinstance(item, str) and item for item in value):
                    errors.append(f"{location}.checks.{key} must be a list of strings")
            for key in ("required_regex", "forbidden_regex"):
                for pattern in checks.get(key, []):
                    try:
                        re.compile(pattern)
                    except re.error as exc:
                        errors.append(f"{location}.checks.{key} has invalid regex: {exc}")
        try:
            source_text(root, raw_case)
        except (AssertionError, OSError, ValueError) as exc:
            errors.append(f"{location}: {exc}")
    return errors


def run_self_tests() -> dict[str, object]:
    fake_root = Path(__file__).resolve().parents[1]
    case = {
        "id": "self-test",
        "prompt": "Edit minimally.",
        "files": ["evals/files/ru-side-by-side.md"],
        "checks": {
            "preserve_all": True,
            "must_contain": ["42"],
            "must_not_contain": ["guaranteed"],
            "maximum_words": 80,
        },
    }
    fixture = source_text(fake_root, case)
    good = grade_case(fake_root, case, fixture)
    bad = grade_case(fake_root, case, fixture.replace("42", "43"))
    payload = load_manifest(fake_root)
    checks = {
        "accepts_valid_manifest": not validate_manifest(fake_root, payload),
        "accepts_passing_output": good.passed,
        "rejects_preservation_failure": not bad.passed,
    }
    return {
        "passed": all(checks.values()),
        "checks": checks,
        "summary": {
            "checks_total": len(checks),
            "checks_passed": sum(1 for passed in checks.values() if passed),
        },
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run or grade better-writing forward evaluations.")
    parser.add_argument("skill_path", nargs="?", help="Path to the better-writing skill")
    parser.add_argument("--list", action="store_true", help="List forward-eval case IDs")
    parser.add_argument("--case", action="append", dest="case_ids", help="Run or grade only this case ID")
    parser.add_argument("--grade-dir", help="Directory containing <case-id>.md model outputs")
    parser.add_argument("--output-dir", help="Directory where runner outputs will be written")
    parser.add_argument("--timeout", type=int, default=180, help="Per-case runner timeout in seconds")
    parser.add_argument("--format", choices=("text", "json"), default="text")
    parser.add_argument("--gate", action="store_true", help="Exit non-zero when a case fails")
    parser.add_argument("--self-test", action="store_true", help="Run built-in harness checks")
    parser.add_argument("--runner", nargs=argparse.REMAINDER, help="Command that reads a prompt from stdin")
    args = parser.parse_args(argv)

    if args.self_test:
        result = run_self_tests()
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result["passed"] else 1
    if not args.skill_path:
        parser.error("skill_path is required unless --self-test is used")
    root = Path(args.skill_path).resolve()
    payload = load_manifest(root)
    errors = validate_manifest(root, payload)
    if errors:
        print(json.dumps({"passed": False, "errors": errors}, ensure_ascii=False, indent=2))
        return 1
    cases = payload["cases"]
    assert isinstance(cases, list)
    selected = [case for case in cases if isinstance(case, dict) and (not args.case_ids or case.get("id") in args.case_ids)]
    if args.case_ids and len(selected) != len(set(args.case_ids)):
        parser.error("one or more --case IDs do not exist")
    if args.list:
        for case in selected:
            print(case["id"])
        return 0

    outputs: dict[str, str] = {}
    if args.runner:
        if not args.output_dir:
            parser.error("--output-dir is required with --runner")
        destination = Path(args.output_dir).resolve()
        destination.mkdir(parents=True, exist_ok=True)
        for case in selected:
            case_id = case["id"]
            assert isinstance(case_id, str)
            output = run_command(args.runner, build_prompt(root, case), args.timeout)
            (destination / f"{case_id}.md").write_text(output, encoding="utf-8")
            outputs[case_id] = output
    elif args.grade_dir:
        source_dir = Path(args.grade_dir).resolve()
        for case in selected:
            case_id = case["id"]
            assert isinstance(case_id, str)
            output_path = source_dir / f"{case_id}.md"
            if not output_path.is_file():
                outputs[case_id] = ""
            else:
                outputs[case_id] = output_path.read_text(encoding="utf-8")
    else:
        parser.error("pass --runner with --output-dir, --grade-dir, or --list")

    results: list[CaseResult] = []
    for case in selected:
        case_id = case["id"]
        assert isinstance(case_id, str)
        output = outputs.get(case_id, "")
        if not output.strip():
            results.append(CaseResult(case_id, False, ("missing or empty output",), (), {}))
        else:
            results.append(grade_case(root, case, output))
    report = {
        "passed": all(result.passed for result in results),
        "cases": [
            {
                "id": result.case_id,
                "passed": result.passed,
                "failures": list(result.failures),
                "warnings": list(result.warnings),
                "metrics": result.metrics,
            }
            for result in results
        ],
        "summary": {
            "cases_total": len(results),
            "cases_passed": sum(1 for result in results if result.passed),
        },
    }
    if args.format == "json":
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(f"Forward evals: {report['summary']['cases_passed']}/{report['summary']['cases_total']} passed")
        for result in results:
            print(f"{'PASS' if result.passed else 'FAIL'}: {result.case_id}")
            for failure in result.failures:
                print(f"  - {failure}")
            for warning in result.warnings:
                print(f"  - warning: {warning}")
    return 1 if args.gate and not report["passed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
