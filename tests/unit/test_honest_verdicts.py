"""Формулировки не должны выдавать неполную проверку за полный вердикт."""

import subprocess
import sys

import common as C

consistency = C.sibling("consistency")
precedent = C.sibling("precedent")

SCRIPTS = C.SCRIPTS


def run(script, *args):
    return subprocess.run(
        [sys.executable, str(SCRIPTS / script), *args], capture_output=True, text=True, timeout=120
    )


def test_consistency_does_not_claim_full_check(tmp_path):
    f = tmp_path / "t.md"
    f.write_text("Простой текст без расхождений внутри себя.\n", encoding="utf-8")
    out = run("consistency.py", str(f)).stdout
    assert "сам себе не противоречит" not in out
    assert "Проверяемых расхождений не найдено" in out
    assert "не заменяет смысловую вычитку" in out


def test_precedent_does_not_judge_the_author(tmp_path):
    out = run("precedent.py", "криптовалюта", "-n", "1").stdout
    assert "не твоё слово" not in out
    assert "не встречается" in out


def test_precedent_zero_examples_does_not_crash():
    """-n 0 раньше падал с ZeroDivisionError."""
    r = run("precedent.py", "темп", "-n", "0")
    assert r.returncode == 0, r.stderr
