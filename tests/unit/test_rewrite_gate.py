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


# ── форма подачи: как в гайдах автора, где нет таблиц и повторов ─────────────

TABLE = "| Колода | Легенда |\n|---|---|\n| Атак Друид | 56,2% |\n"
CODE = "AAECAZICBs2eBpKDB6+HB+DAB+XEB6PaBwyunwSIgweqrwesrwfosQe+sgfXwAfk2QeR2gfH5ge85wfK5wcAAA==\n"


def test_form_metrics_count_tables_codes_labels_and_repeats():
    text = (
        TABLE
        + "\n"
        + TABLE
        + "\n"
        + CODE * 5
        + "Положение: A−/B+, недоигранный контрпик.\n\n"
        + "У Друида 64,5% против Воина, и это решает выбор на высоких рангах.\n\n"
        + "Напомним: 64,5% против Воина — главная причина брать Друида в Легенде.\n\n"
        + "И снова те же 64,5% против Воина объясняют падение Пират Воина в Топ Легенде.\n"
    )
    fm = gate.form_metrics(text)
    assert fm["tables"] == 2
    assert fm["codes"] == 5
    assert fm["grade_labels"]
    # ключ — процент плюс класс из предложения: «64,5% воин» звучит трижды, «64,5% друид» дважды
    assert fm["repeated_facts"]["64,5% воин"] == [3, 4, 5]
    assert len(fm["repeated_facts"]["64,5% друид"]) == 2


def test_form_violations_follow_profile_limits():
    text = (
        TABLE
        + "\n"
        + TABLE
        + "\n"
        + CODE * 5
        + "Положение: A−/B+, недоигранный контрпик.\n\n"
        + "У Друида 64,5% против Воина, и это решает выбор на высоких рангах.\n\n"
        + "Напомним: 64,5% против Воина — главная причина брать Друида в Легенде.\n\n"
        + "И снова те же 64,5% против Воина объясняют падение Пират Воина в Топ Легенде.\n"
    )
    violations, warnings, fm = gate.form_checks(
        text,
        {"tables_max": 1, "codes_max": 4, "repeated_facts_max": 0, "grade_labels": "forbidden"},
    )
    assert {"form_tables", "form_codes", "form_grade_labels", "form_fact_repeated"} == kinds(
        violations
    )
    # тот же текст без ограничений формы проходит
    v2, _, _ = gate.form_checks(
        text,
        {
            "tables_max": None,
            "codes_max": None,
            "repeated_facts_max": None,
            "grade_labels": "allowed",
        },
    )
    assert v2 == []


def test_meta_report_profile_enforces_form_in_gate():
    text = TABLE + "\n" + TABLE + "\n" + section("Что изменилось") + section("Лидеры меты")
    violations, _, metrics = gate.analyze(text, profile="meta-report")
    assert "form_tables" in kinds(violations)
    assert metrics["form"]["tables"] == 2
    ok, _, _ = gate.analyze(section("Что изменилось") + TABLE, profile="meta-report")
    assert "form_tables" not in kinds(ok)


def test_twice_repeated_fact_is_only_a_warning():
    text = (
        "У Друида 64,5% против Воина, и это решает выбор на высоких рангах.\n\n"
        + "Напомним: 64,5% против Воина — главная причина брать Друида в Легенде.\n"
    )
    violations, warnings, _ = gate.form_checks(text, {"repeated_facts_max": 0})
    assert violations == []
    assert "form_fact_repeated" in kinds(warnings)


def test_same_percent_for_different_subjects_is_not_a_repeat():
    text = (
        "Квест Жрец держит 52,6% побед на Бриллианте, но выше проседает.\n\n"
        "Гарольд Чернокнижник лучше сохраняет винрейт при росте ранга: 52,6% в Легенде.\n\n"
        "У Чистого Паладина тоже 52,6%, хотя это другая история.\n"
    )
    _, _, fm = gate.form_checks(text, {"repeated_facts_max": 0})
    assert not any(len(v) >= 3 for v in fm["repeated_facts"].values())
    same = (
        "Пират Воин бьет Друида лишь в 36,1% матчей.\n\n"
        "Напомним: против Друида у Пират Воина 36,1%.\n\n"
        "И снова: 36,1% против Друида — вот почему Воин падает.\n"
    )
    violations, _, _ = gate.form_checks(same, {"repeated_facts_max": 0})
    assert "form_fact_repeated" in kinds(violations)


def test_terminology_hits_follow_the_dictionary():
    bad = (
        "На Бриллианте эта дека сильна, а в Топ Легенде её контрпик — Друид. "
        "На этом отрезке статы решают."
    )
    found = {h["preferred"] for h in gate.terminology_hits(bad)}
    assert {"Алмаз", "колода", "топ Легенды", "контра", "ранг", "характеристики"} <= found
    good = "На Алмазе и в Легенде колода сильна, а в топе Легенды её контра — Друид."
    assert gate.terminology_hits(good) == []
    violations, _, metrics = gate.analyze(section("Сборки") + bad, profile="constructed-guide")
    assert "term_replace" in kinds(violations)
    assert metrics["terminology_hits"] >= 5


def test_rewrite_sections_count_only_markdown_headings():
    """Если модель поставила решётки, короткие строки заголовками не считаются;
    без решёток разделы угадываются по-старому, но с предупреждением."""
    hashed = (
        "## Сборки\n" + "Оставляйте Мастера брони, он тащит против агро. " * 8 + "\n"
        "Муллиган\n" + "Против Мага держите ответ на раннюю доску. " * 8 + "\n"
    )
    violations, warnings, metrics = gate.analyze(hashed, profile="constructed-guide")
    assert "structure_no_headings" not in kinds(warnings)
    assert "builds" in metrics["sections_present"]
    assert "mulligan" not in metrics["sections_present"]
    assert "mulligan" in metrics["sections_missing"]

    bare = hashed.replace("## Сборки", "Сборки")
    violations, warnings, metrics = gate.analyze(bare, profile="constructed-guide")
    assert "structure_no_headings" in kinds(warnings)
    assert {"builds", "mulligan"} <= set(metrics["sections_present"])


def test_terminology_aliases_hit_english_and_transliterated_forms():
    text = "На Diamond колода держится, в Top Legend её каунтерпик — Друид, а на этом брекете хуже."
    hits = gate.terminology_hits(text)
    by_pref = {h["preferred"]: h["found"] for h in hits}
    assert by_pref.get("Алмаз") == "Diamond"
    assert by_pref.get("топ Легенды") == "Top Legend"
    assert by_pref.get("контра") == "каунтерпик"
    assert by_pref.get("ранг") == "брекете"
    assert not [
        h
        for h in gate.terminology_hits("в топе Легенды и на Алмазе")
        if h["rule"] == "term.top_legend"
    ]
