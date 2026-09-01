"""Набор эвалов переплавки: детерминированная часть.

Модель здесь не вызывается. Проверяется, что кейсы описаны честно (карты,
числа и классы буквально есть во входе), что входы-слоп ПРОВАЛИВАЮТ пороги
(иначе затвор не отличает слоп от автора), а контрольный текст в манере
автора их ПРОХОДИТ.
"""

import json
from functools import lru_cache
from pathlib import Path

import common as C
import pytest
import yaml

from editorteam import profiles as P

evalscore = C.sibling("evalscore")
CASES = sorted(p for p in Path("tests/evals/cases").iterdir() if (p / "case.yaml").exists())
THRESHOLDS = evalscore.load_thresholds()
GOLDEN = Path("tests/evals/golden/eval-input-01.json")

REQUIRED_KEYS = {
    "id",
    "profile",
    "editorial_mode",
    "defect_classes",
    "topic",
    "claims",
    "structure",
    "input_must_fail",
    "input_must_pass",
    "thresholds",
    "notes",
}


@lru_cache(maxsize=None)
def scored_input(case_dir: str) -> dict:
    case = evalscore.load_case(case_dir)
    return evalscore.score(case["_input"], case, THRESHOLDS, is_input=True)


def test_thresholds_cover_every_profile_used():
    profiles = {
        yaml.safe_load((d / "case.yaml").read_text(encoding="utf-8"))["profile"] for d in CASES
    }
    for profile in profiles:
        assert profile in THRESHOLDS, profile
        assert profile in P.available(), profile


@pytest.mark.parametrize("case_dir", CASES, ids=[d.name for d in CASES])
def test_case_is_well_formed(case_dir):
    case = evalscore.load_case(case_dir)
    assert REQUIRED_KEYS <= set(case), set(case) ^ REQUIRED_KEYS
    assert case["id"] == case_dir.name
    words = len(case["_input"].split())
    # короткий слоп реален: новость на 40 слов, черновик на 100. Верх — 700
    lo, hi = (40, 250) if case["profile"] == "news" else (100, 700)
    assert lo <= words <= hi, f"{case['id']}: {words} слов"
    text = case["_input"]
    for card in case["claims"].get("cards") or []:
        assert C.sibling("structure").phrase_present(card, text), (
            f"{case['id']}: карта {card} не во входе"
        )
    for cls in case["claims"].get("classes") or []:
        assert cls in C.sibling("claims")._classes_in(text), (
            f"{case['id']}: класс {cls} не во входе"
        )
    for neg in case["claims"].get("negations") or []:
        assert neg["text"].lower() in text.lower(), (
            f"{case['id']}: отрицание «{neg['text']}» не во входе"
        )
    for section in case["structure"].get("sections_missing_in_input") or []:
        assert section in {s.id for s in P.load(case["profile"]).sections}


@pytest.mark.parametrize("case_dir", CASES, ids=[d.name for d in CASES])
def test_slop_inputs_fail_and_control_passes(case_dir):
    case = evalscore.load_case(case_dir)
    result = scored_input(str(case_dir))
    if case["input_must_pass"]:
        assert result["accepted"], f"контроль должен проходить: {result['failed']}"
    else:
        assert not result["accepted"], "слоп не должен проходить пороги"
        missing = set(case["input_must_fail"]) - set(result["failed"])
        assert not missing, f"ожидались проверки {missing}, провалено {result['failed']}"


def test_gate_itself_passes_control_and_rejects_rhetoric():
    gate = C.sibling("rewrite_gate")
    control = evalscore.load_case("tests/evals/cases/00-control-clean-guide")
    violations, _, _ = gate.analyze(control["_input"], profile="constructed-guide")
    assert violations == [], violations
    slop = evalscore.load_case("tests/evals/cases/01-slop-rhetoric-bomb-warrior")
    violations, _, _ = gate.analyze(slop["_input"], profile="constructed-guide")
    assert {v["kind"] for v in violations} >= {"structure_missing"}


def test_scorer_output_matches_golden():
    case = evalscore.load_case("tests/evals/cases/01-slop-rhetoric-bomb-warrior")
    result = evalscore.score(case["_input"], case, THRESHOLDS, is_input=True)
    expected = json.loads(GOLDEN.read_text(encoding="utf-8"))
    assert result["evals_schema_version"] == expected["evals_schema_version"] == "1.0"
    assert result["failed"] == expected["failed"]
    assert result["metrics"]["markers_per_10k"] == expected["metrics"]["markers_per_10k"]
    assert result["metrics"]["sections"] == expected["metrics"]["sections"]


def test_runner_inputs_only_writes_report(tmp_path, capsys):
    import importlib.util

    spec = importlib.util.spec_from_file_location("run_evals", Path("tools/run_evals.py"))
    runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)
    import sys

    argv = sys.argv
    sys.argv = [
        "run_evals",
        "--inputs-only",
        "--case",
        "01",
        "--case",
        "00",
        "--out",
        str(tmp_path),
        "--format",
        "json",
    ]
    try:
        code = runner.main()
    finally:
        sys.argv = argv
    assert code == 0
    report = json.loads((tmp_path / "report.json").read_text(encoding="utf-8"))
    assert report["run"]["source"] == "inputs"
    assert {c["id"] for c in report["cases"]} == {
        "00-control-clean-guide",
        "01-slop-rhetoric-bomb-warrior",
    }
    assert (tmp_path / "report.md").exists()
