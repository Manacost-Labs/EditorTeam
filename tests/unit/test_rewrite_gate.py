"""Затвор переплавки: абсолютные нормы автора вместо сравнения с исходником."""

from pathlib import Path

import common as C
import pytest

gate = C.sibling("rewrite_gate")
CLEAN = Path("tests/fixtures/negative/clean-guide.md").read_text(encoding="utf-8")
TOC_GUIDE = Path("tests/fixtures/structure/toc-guide.md").read_text(encoding="utf-8")


def kinds(items):
    return {i["kind"] for i in items}


def section(title, sentences=8):
    body = " ".join(
        f"Вы держите {title.lower()} под контролем, но не спешите с разменом."
        for _ in range(sentences)
    )
    return f"{title}\n{body}\n"


@pytest.mark.parametrize(
    "raw, want",
    [
        ("переплавка", "переплавка"),
        ("Переплавка", "переплавка"),
        ("легкая", "лёгкая"),
        ("ЛЁГКАЯ:", "лёгкая"),
        (" глубокая. ", "глубокая"),
        (None, "обычная"),
        ("", "обычная"),
    ],
)
def test_normalize_depth(raw, want):
    assert gate.normalize_depth(raw) == want


def test_unknown_depth_raises():
    with pytest.raises(ValueError):
        gate.normalize_depth("medium")


def test_flat_voiceless_text_fails_absolute_norms():
    text = "Сборки\n" + " ".join(
        f"Колода играет карту номер {i} и делает размен на столе аккуратно." for i in range(20)
    )
    violations, warnings, metrics = gate.analyze(text, profile="constructed-guide")
    assert "rhythm_below_norm" in kinds(violations)
    assert "voice_below_norm" in kinds(violations)
    assert metrics["words"] >= 150
    assert metrics["rhythm_ratio"] < gate.RHYTHM_FLOOR


def test_clean_fixture_passes_voice_and_rhythm_and_structure():
    violations, warnings, metrics = gate.analyze(CLEAN, profile="constructed-guide")
    assert not {"voice_below_norm", "rhythm_below_norm", "structure_missing"} & kinds(violations)
    assert metrics["sections_missing"] == []
    # короткий текст: частотные вердикты не выносятся
    assert metrics["words"] < 150


def test_missing_section_is_violation_unless_declared():
    text = (
        section("Сборки")
        + section("Вопросы декбилдинга")
        + section("Стратегия игры")
        + section("Матч-апы")
    )
    violations, warnings, _ = gate.analyze(text, profile="constructed-guide")
    missing = [v for v in violations if v["kind"] == "structure_missing"]
    assert missing and missing[0]["signal"] == "mulligan"

    violations, warnings, metrics = gate.analyze(
        text, profile="constructed-guide", declared_missing=["mulligan"]
    )
    assert "structure_missing" not in kinds(violations)
    assert "structure_declared_missing" in kinds(warnings)
    assert metrics["sections_declared_missing"] == ["mulligan"]


def test_remove_markers_three_is_violation_two_is_warning():
    base = section("Сборки") + section("Муллиган")
    two = base + "Надеюсь, это поможет вам в ладдере.\nДавайте разберёмся, как играть дальше.\n"
    three = two + "Подведём итог всему сказанному.\n"
    _, warnings, metrics = gate.analyze(two, profile="constructed-guide")
    assert "markers_remove_present" in kinds(warnings)
    assert metrics["markers_remove"] == 2
    violations, _, metrics = gate.analyze(three, profile="constructed-guide")
    assert "markers_remove_present" in kinds(violations)
    assert metrics["markers_remove"] == 3


def test_order_thin_and_wall_are_warnings_not_violations():
    text = (
        "Матч-апы\n" + " ".join(["Вы играете аккуратно, но быстро."] * 12) + "\n"
        "Муллиган\nКоротко.\n"
        "Сборки\n" + " ".join(["Мы играем колодой."] * 12) + "\n"
    )
    violations, warnings, _ = gate.analyze(text, profile="constructed-guide")
    assert {"structure_order", "structure_thin", "structure_wall"} <= kinds(warnings)
    assert not {"structure_order", "structure_thin", "structure_wall"} & kinds(violations)


def test_matchup_coverage_uses_source_classes():
    text = TOC_GUIDE
    _, warnings, metrics = gate.analyze(
        text, profile="constructed-guide", expected_classes=["Маг", "Жрец", "Друид"]
    )
    assert metrics["classes_missing"] == []
    _, warnings_all, metrics_all = gate.analyze(
        text, profile="constructed-guide", expected_classes=["Маг", "Шаман", "Демон"]
    )
    assert metrics_all["classes_missing"] == ["Демон"]
    assert "matchups_incomplete" in kinds(warnings_all)


def test_opening_check_reports_missing_archetype():
    _, warnings, metrics = gate.analyze(
        TOC_GUIDE, profile="constructed-guide", archetype="Пират Воин", expansion="Некроситет"
    )
    assert "opening_missing" in kinds(warnings)
    assert metrics["opening"]["expansion"] is True
    _, warnings_ok, _ = gate.analyze(
        TOC_GUIDE, profile="constructed-guide", archetype="Бомб Воин", expansion="Некроситет"
    )
    assert "opening_missing" not in kinds(warnings_ok)


def test_norms_for_reads_game_config():
    norms = gate.norms_for("hearthstone")
    assert norms["voice_low"] == 20.6
    assert norms["rhythm_alarm"] == 0.45
    assert gate.norms_for("нет-такой-игры")["voice_low"] == 20.6
