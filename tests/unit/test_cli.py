"""CLI: профили меняют вердикт, JSON стабилен, exit code управляем."""

import json
import subprocess
import sys

import pytest

from editorteam.cli import main

BG = (
    "Идея стратегии\nСобираем мурлоков в тавернах и держим темп.\n"
    "Ключевые существа\nБрановый мурлок закрывает ранние ходы.\n"
    "План по ходам\nНа четвертом ходу ищем тройку существ.\n"
)


@pytest.fixture
def bg_file(tmp_path):
    p = tmp_path / "bg.md"
    p.write_text(BG, encoding="utf-8")
    return p


def run_json(capsys, *argv):
    code = main(list(argv))
    return code, json.loads(capsys.readouterr().out)


def test_battlegrounds_profile_does_not_demand_deck_sections(capsys, bg_file):
    _, data = run_json(
        capsys, "--format", "json", "audit", str(bg_file), "--profile", "battlegrounds-guide"
    )
    missing = [f for f in data["findings"] if f["id"].startswith("structure.missing")]
    assert missing == [], "BG-гайд не должен требовать разделы конструированного"


def test_constructed_profile_demands_them(capsys, bg_file):
    _, data = run_json(
        capsys, "--format", "json", "audit", str(bg_file), "--profile", "constructed-guide"
    )
    missing = {f["id"] for f in data["findings"] if f["id"].startswith("structure.missing")}
    assert "structure.missing.mulligan" in missing


def test_json_has_stable_top_level_keys(capsys, bg_file):
    _, data = run_json(capsys, "--format", "json", "audit", str(bg_file))
    assert set(data) == {
        "schema_version",
        "document",
        "profile",
        "summary",
        "metrics",
        "findings",
        "analyzers_skipped",
        "notes",
    }


def test_autodetect_reports_profile_and_confidence(capsys, bg_file):
    _, data = run_json(capsys, "--format", "json", "audit", str(bg_file))
    assert data["profile"] == "battlegrounds-guide"
    assert any("уверенность" in n for n in data["notes"])


def test_config_validate_passes(capsys):
    code, data = run_json(capsys, "--format", "json", "config", "validate")
    assert code == 0
    assert data["summary"]["error"] == 0


def test_console_script_installed():
    r = subprocess.run(
        [sys.executable, "-m", "editorteam.cli", "profiles"], capture_output=True, text=True
    )
    assert r.returncode == 0
    assert "battlegrounds-guide" in r.stdout


def test_flag_works_before_and_after_subcommand(capsys, bg_file):
    """Подпарсер не должен затирать флаг, заданный до подкоманды."""
    for argv in (
        ["--format", "json", "audit", str(bg_file)],
        ["audit", str(bg_file), "--format", "json"],
    ):
        main(argv)
        out = capsys.readouterr().out
        assert json.loads(out)["schema_version"] == "1.0"


def test_fail_on_changes_exit_code(capsys, bg_file):
    code_default = main(
        ["audit", str(bg_file), "--profile", "constructed-guide", "--format", "json"]
    )
    capsys.readouterr()
    code_strict = main(
        [
            "audit",
            str(bg_file),
            "--profile",
            "constructed-guide",
            "--format",
            "json",
            "--fail-on",
            "likely",
        ]
    )
    capsys.readouterr()
    assert code_default == 0  # структурные пропуски — не error
    assert code_strict == 1
