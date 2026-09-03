from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def _vale() -> str:
    binary = os.environ.get("VALE_BIN") or shutil.which("vale")
    if not binary:
        pytest.skip("Vale integration binary is not available")
    return binary


def _rules(tmp_path: Path, profile: str, text: str) -> list[dict[str, object]]:
    path = tmp_path / f"case.{profile}.md"
    path.write_text(text, encoding="utf-8")
    proc = subprocess.run(
        [_vale(), f"--config={ROOT / '.vale.ini'}", "--output=JSON", str(path)],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert proc.returncode in {0, 1}, proc.stdout + proc.stderr
    payload = json.loads(proc.stdout or "{}")
    return [item for items in payload.values() for item in items]


def _checks(findings: list[dict[str, object]]) -> set[str]:
    return {str(item.get("Check", "")) for item in findings}


@pytest.mark.parametrize("profile", ["news", "analysis", "meta-report"])
def test_overcertainty_is_a_suggestion_for_non_guide_profiles(tmp_path: Path, profile: str) -> None:
    findings = _rules(
        tmp_path,
        profile,
        "Этот вариант гарантированно побеждает любую колоду и никогда не ошибается.",
    )
    overcertainty = [item for item in findings if item.get("Check") == "EditorTeam.Overcertainty"]
    assert overcertainty
    assert {item.get("Severity") for item in overcertainty} == {"suggestion"}


def test_guide_instruction_does_not_trigger_overcertainty(tmp_path: Path) -> None:
    findings = _rules(
        tmp_path,
        "guide",
        "Всегда оставляйте монету для ключевого хода, если соперник не давит на стол.",
    )
    assert "EditorTeam.Overcertainty" not in _checks(findings)


def test_promotion_is_a_suggestion_in_news(tmp_path: Path) -> None:
    findings = _rules(
        tmp_path,
        "news",
        "Разработчики назвали обновление уникальным и незаменимым для всех игроков.",
    )
    promotion = [item for item in findings if item.get("Check") == "EditorTeam.Promotion"]
    assert promotion
    assert {item.get("Severity") for item in promotion} == {"suggestion"}


def test_justified_game_instruction_does_not_trigger_promotion(tmp_path: Path) -> None:
    findings = _rules(
        tmp_path,
        "guide",
        "Уникальный эффект карты срабатывает один раз: сохраните её до шестого хода.",
    )
    assert "EditorTeam.Promotion" not in _checks(findings)


def test_headings_quotes_and_code_are_ignored(tmp_path: Path) -> None:
    findings = _rules(
        tmp_path,
        "analysis",
        '# Уникальный план\n\n> "Эта карта никогда не проигрывает", — написал игрок.\n\n'
        "```text\nвсегда уникальный незаменимый\n```\n",
    )
    checks = _checks(findings)
    assert "EditorTeam.Overcertainty" not in checks
    assert "EditorTeam.Promotion" not in checks
