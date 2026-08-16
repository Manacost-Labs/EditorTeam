"""Golden JSON: схема и содержание вывода не меняются молча.

Эталоны обновляются осознанно: если тест упал, значит изменилось то, на что
опирается CI и внешние потребители JSON.
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest

GOLDEN = Path("tests/golden")

CASES = {
    "clean-guide": ("tests/fixtures/negative/clean-guide.md", "constructed-guide"),
    "cards-apostrophe": ("tests/fixtures/positive/cards-apostrophe.md", "constructed-guide"),
    "bg-under-own-profile": ("tests/fixtures/negative/clean-guide.md", "battlegrounds-guide"),
}


def run(path, profile):
    r = subprocess.run(
        [
            sys.executable,
            "-m",
            "editorteam.cli",
            "audit",
            path,
            "--profile",
            profile,
            "--format",
            "json",
        ],
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert r.returncode in (0, 1), r.stderr
    data = json.loads(r.stdout)
    data.pop("document")  # путь зависит от машины
    return data


@pytest.mark.parametrize("name", sorted(CASES))
def test_matches_golden(name):
    path, profile = CASES[name]
    expected = json.loads((GOLDEN / f"{name}.json").read_text(encoding="utf-8"))
    assert run(path, profile) == expected


def test_golden_files_exist_for_every_case():
    assert {p.stem for p in GOLDEN.glob("*.json")} == set(CASES)


def test_schema_version_pinned():
    for p in GOLDEN.glob("*.json"):
        assert json.loads(p.read_text(encoding="utf-8"))["schema_version"] == "1.0"
