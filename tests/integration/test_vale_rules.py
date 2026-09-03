"""Positive and negative checks for every EditorTeam Vale rule.

The tests need a real Vale binary (``VALE_BIN`` or ``vale`` in PATH) and use
the repository ``.vale.ini`` so profile sections, ``TokenIgnores`` and
``BlockIgnores`` are exercised exactly as the Go gateway uses them.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
PROFILES = ("guide", "news", "analysis", "meta-report")
RULES = (
    "AIFrames",
    "Intensifiers",
    "Intro",
    "Overcertainty",
    "PassiveVoice",
    "Promotion",
    "Repeat",
    "Terminology",
    "WeakVerb",
    "Wordiness",
)


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


def test_every_rule_file_is_covered_by_this_module() -> None:
    files = {p.stem for p in (ROOT / ".vale/styles/EditorTeam").glob("*.yml")}
    assert files == set(RULES)


# --- positive: each rule fires on a sentence written for it -----------------

POSITIVE = [
    ("AIFrames", "analysis", "Стоит отметить, что колода стала медленнее после патча."),
    ("Intensifiers", "news", "Это абсолютно уникальный вариант для ладдера."),
    ("Intro", "analysis", "В этой статье мы разберём колоду и её матч-апы."),
    ("Overcertainty", "news", "Этот вариант гарантированно побеждает любую колоду."),
    ("PassiveVoice", "guide", "Ошибка была обнаружена после выхода патча."),
    ("Promotion", "news", "Разработчики назвали обновление уникальным и незаменимым для всех."),
    ("Repeat", "guide", "Эта колода колода играет очень быстро."),
    ("Terminology", "guide", "Игрок получил три подарка от таверны."),
    ("WeakVerb", "guide", "Эта карта является лучшей в колоде."),
    ("Wordiness", "guide", "Нужно осуществлять размен каждый ход."),
]


@pytest.mark.parametrize(("rule", "profile", "text"), POSITIVE, ids=[p[0] for p in POSITIVE])
def test_rule_fires_on_its_positive_example(
    tmp_path: Path, rule: str, profile: str, text: str
) -> None:
    findings = _rules(tmp_path, profile, text + "\n")
    hits = [item for item in findings if item.get("Check") == f"EditorTeam.{rule}"]
    assert hits, f"EditorTeam.{rule} did not fire on: {text}"
    assert {item.get("Severity") for item in hits} == {"suggestion"}


# --- negative: ordinary author language never triggers the rule -------------

NEGATIVE = [
    ("AIFrames", "analysis", "Отметим сразу: колода стала медленнее после патча."),
    ("Intensifiers", "news", "Уникальный эффект карты срабатывает один раз за партию."),
    ("Intro", "analysis", "Сегодня в мете три колоды, и каждая играет по-своему."),
    (
        "Overcertainty",
        "news",
        "Всегда оставляйте монету для ключевого хода, если соперник не давит.",
    ),
    ("PassiveVoice", "guide", "Ошибку нашли после выхода патча."),
    (
        "Promotion",
        "news",
        "Уникальный эффект карты срабатывает один раз: сохраните её до шестого хода.",
    ),
    ("Repeat", "guide", "Колода играет быстро, и эта же колода не боится агро."),
    ("Terminology", "guide", "Темные дары усиливают существо, а тип существа не меняется."),
    ("WeakVerb", "guide", "Эта карта лучшая в колоде."),
    ("Wordiness", "guide", "Разменивайтесь каждый ход и не жадничайте."),
]


@pytest.mark.parametrize(("rule", "profile", "text"), NEGATIVE, ids=[n[0] for n in NEGATIVE])
def test_rule_stays_silent_on_its_negative_example(
    tmp_path: Path, rule: str, profile: str, text: str
) -> None:
    findings = _rules(tmp_path, profile, text + "\n")
    assert f"EditorTeam.{rule}" not in _checks(findings), findings


# --- severity: nothing in the style is ever more than a suggestion ----------

SLOP = (
    "В этой статье мы рассмотрим колоду. Стоит отметить, что она является абсолютно уникальным "
    "и незаменимым решением, которое гарантированно побеждает всегда. Ошибка была обнаружена, "
    "поэтому нужно осуществлять размен. Игрок получил подарок. Колода колода сильная.\n"
)


@pytest.mark.parametrize("profile", PROFILES)
def test_all_findings_are_suggestions_in_every_profile(tmp_path: Path, profile: str) -> None:
    findings = _rules(tmp_path, profile, SLOP)
    assert findings
    assert {item.get("Severity") for item in findings} == {"suggestion"}


# --- profiles: categorical words are allowed in guides -----------------------


@pytest.mark.parametrize("profile", ["news", "analysis", "meta-report"])
def test_overcertainty_promotion_and_intensifiers_stay_on_for_non_guides(
    tmp_path: Path, profile: str
) -> None:
    findings = _rules(
        tmp_path,
        profile,
        "Этот абсолютно уникальный вариант гарантированно побеждает любую колоду и незаменим.\n",
    )
    checks = _checks(findings)
    assert {"EditorTeam.Overcertainty", "EditorTeam.Intensifiers"} <= checks, checks


def test_guide_profile_disables_categorical_rules_but_keeps_the_rest(tmp_path: Path) -> None:
    findings = _rules(
        tmp_path,
        "guide",
        "Этот абсолютно уникальный вариант гарантированно побеждает любую колоду, "
        "и стоит отметить, что нужно осуществлять размен.\n",
    )
    checks = _checks(findings)
    assert (
        not {"EditorTeam.Overcertainty", "EditorTeam.Promotion", "EditorTeam.Intensifiers"} & checks
    )
    assert {"EditorTeam.AIFrames", "EditorTeam.Wordiness"} <= checks


def test_justified_game_instruction_does_not_trigger_promotion(tmp_path: Path) -> None:
    findings = _rules(
        tmp_path,
        "guide",
        "Уникальный эффект карты срабатывает один раз: сохраните её до шестого хода.\n",
    )
    assert "EditorTeam.Promotion" not in _checks(findings)


# --- context exclusions: headings, quotes, blockquotes, code ----------------


def test_headings_are_excluded_from_categorical_rules(tmp_path: Path) -> None:
    findings = _rules(
        tmp_path,
        "analysis",
        "# Уникальный и незаменимый план, который гарантированно побеждает\n\nТекст.\n",
    )
    checks = _checks(findings)
    assert "EditorTeam.Overcertainty" not in checks
    assert "EditorTeam.Promotion" not in checks


@pytest.mark.parametrize(
    "quoted",
    [
        "Автор написал: «Эта карта гарантированно побеждает, стоит отметить».",
        'Автор написал: "Эта карта гарантированно побеждает, стоит отметить".',
        "Автор написал: “Эта карта гарантированно побеждает, стоит отметить”.",
    ],
    ids=["guillemets", "straight", "curly"],
)
def test_direct_quotes_are_ignored(tmp_path: Path, quoted: str) -> None:
    findings = _rules(tmp_path, "news", quoted + "\n")
    checks = _checks(findings)
    assert "EditorTeam.Overcertainty" not in checks
    assert "EditorTeam.AIFrames" not in checks


def test_single_and_multiline_blockquotes_are_ignored(tmp_path: Path) -> None:
    text = (
        "> Эта карта гарантированно побеждает.\n\n"
        "> Стоит отметить, что колода\n> в настоящее время является лучшей.\n\n"
        "Собственный текст без сигналов.\n"
    )
    findings = _rules(tmp_path, "news", text)
    assert _checks(findings) == set(), findings


def test_fenced_and_inline_code_are_ignored(tmp_path: Path) -> None:
    text = (
        "```text\nвсегда побеждает уникальный незаменимый стоит отметить\n```\n\n"
        "Команда `гарантированно побеждает` в коде не считается.\n"
    )
    findings = _rules(tmp_path, "analysis", text)
    assert _checks(findings) == set(), findings


def test_quoted_card_name_is_not_a_signal(tmp_path: Path) -> None:
    findings = _rules(tmp_path, "news", "Карта «Незаменимый уникальный страж» стоит 4 маны.\n")
    assert "EditorTeam.Promotion" not in _checks(findings)
