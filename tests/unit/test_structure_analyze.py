"""Структура глубже присутствия: оглавление, порядок, тонкие разделы, зачин, план."""

from pathlib import Path

import common as C
import pytest

structure = C.sibling("structure")
TOC_GUIDE = Path("tests/fixtures/structure/toc-guide.md").read_text(encoding="utf-8")
CLEAN = Path("tests/fixtures/negative/clean-guide.md").read_text(encoding="utf-8")
SECTIONS = structure.load_profile_sections("constructed-guide")


def ids(findings):
    return {f["id"] for f in findings}


def test_profile_sections_come_from_yaml_with_purpose():
    assert [s["id"] for s in SECTIONS] == [
        "builds",
        "deckbuilding",
        "mulligan",
        "strategy",
        "matchups",
        "conclusion",
    ]
    assert all(s["purpose"] for s in SECTIONS if s["required"])
    assert SECTIONS[0]["min_words"] == 60


def test_fallback_to_blocks_without_yaml():
    sections = structure.load_profile_sections("нет-такого-профиля")
    assert [s["id"] for s in sections][:5] == [
        "builds",
        "deckbuilding",
        "mulligan",
        "strategy",
        "matchups",
    ]


def test_toc_is_detected_and_bodies_resolve_past_it():
    span = structure.toc_span(TOC_GUIDE, SECTIONS)
    assert span is not None
    bodies = structure.resolve_sections(TOC_GUIDE, SECTIONS)
    assert set(bodies) == {
        "builds",
        "deckbuilding",
        "mulligan",
        "strategy",
        "matchups",
        "conclusion",
    }
    assert not bodies["builds"].toc_only
    assert bodies["builds"].words > 60
    assert bodies["builds"].heading_line > span[1] + 1


def test_toc_only_section_is_marked():
    text = (
        "Разделы гайда:\nСборки\nМуллиган\nМатч-апы\n\nСборки\n" + " ".join(["слово"] * 80) + "\n"
    )
    bodies = structure.resolve_sections(text, SECTIONS)
    assert bodies["mulligan"].toc_only is True
    assert bodies["builds"].toc_only is False


def test_order_check_flags_swapped_sections():
    text = (
        "Матч-апы\n" + " ".join(["слово"] * 70) + "\n"
        "Муллиган\n" + " ".join(["слово"] * 70) + "\n"
        "Сборки\n" + " ".join(["слово"] * 70) + "\n"
    )
    bodies = structure.resolve_sections(text, SECTIONS)
    order = structure.check_order(bodies, SECTIONS)
    assert order["ok"] is False
    assert order["actual"] == ["matchups", "mulligan", "builds"]
    findings, _ = structure.analyze(text, "constructed-guide", deep=True)
    assert "structure.order" in ids(findings)


def test_thin_sections_only_in_deep_mode():
    shallow, _ = structure.analyze(CLEAN, "constructed-guide")
    assert ids(shallow) == set(), "по умолчанию только отсутствие разделов, golden не меняется"
    deep, metrics = structure.analyze(CLEAN, "constructed-guide", deep=True)
    assert any(f["id"].startswith("structure.thin.") for f in deep)
    assert metrics["order_ok"] is True


def test_deep_analysis_on_real_guide_shape_is_clean():
    findings, metrics = structure.analyze(
        TOC_GUIDE,
        "constructed-guide",
        archetype="Бомб Воин",
        expansion="Некроситет",
        deep=True,
    )
    assert metrics["present"] == 5
    assert metrics["missing"] == []
    assert metrics["order_ok"] is True
    assert metrics["opening"]["archetype"] is True
    assert metrics["opening"]["expansion"] is True
    assert metrics["opening"]["formula"] is True
    assert metrics["classes_missing"] == []
    assert not any(f["id"].startswith("structure.opening") for f in findings)
    assert not any(f["id"].startswith("structure.thin") for f in findings)


def test_opening_check_uses_lemmas():
    opening = structure.check_opening(
        "Герой гайда — Бомб Воина в Некроситете. " + " ".join(["слово"] * 30),
        archetype="Бомб Воин",
        expansion="Некроситет",
    )
    assert opening["archetype"] is True
    assert opening["formula"] is True
    missing = structure.check_opening(
        "Колода сильная. " + " ".join(["слово"] * 30), archetype="Бомб Воин"
    )
    assert missing["archetype"] is False


def test_matchups_coverage_uses_expected_classes_not_eleven():
    text = "Матч-апы\n" + "Против Мага держите темп, а против Жреца берегите ремувалы. " * 6
    _, metrics = structure.analyze(
        text, "constructed-guide", expected_classes=["Маг", "Жрец"], deep=True
    )
    assert metrics["classes_missing"] == []
    _, metrics_all = structure.analyze(text, "constructed-guide", deep=True)
    assert "Воин" in metrics_all["classes_missing"]


def test_wall_paragraph_is_reported():
    wall = "Мы играем колодой. " * 12
    text = "Сборки\n" + wall + "\n"
    findings, metrics = structure.analyze(text, "constructed-guide", deep=True)
    assert "structure.wall" in ids(findings)
    assert metrics["walls"] == 1


def test_outline_lists_sections_with_purpose_and_order():
    skeleton = structure.outline("constructed-guide")
    assert [s["order"] for s in skeleton["sections"]] == [1, 2, 3, 4, 5, 6]
    assert skeleton["sections"][2]["id"] == "mulligan"
    assert skeleton["sections"][2]["purpose"]
    assert skeleton["opening"]["requires"] == ["archetype", "expansion"]


@pytest.mark.parametrize(
    "outline_json, expected",
    [
        (
            {
                "sections": [
                    {"id": "builds", "title": "Сборки", "claims": ["две сборки"]},
                    {"id": "deckbuilding", "title": "Декбилдинг", "claims": ["основа"]},
                    {"id": "mulligan", "title": "Муллиган", "claims": ["ищите Боевой якорррь"]},
                    {"id": "strategy", "title": "Стратегия", "claims": ["броня и бомбы"]},
                ],
                "missing_sections": ["matchups"],
            },
            set(),
        ),
        (
            {
                "sections": [
                    {"id": "builds", "title": "Сборки", "claims": ["две сборки"]},
                    {"id": "mulligan", "title": "Муллиган", "claims": []},
                ],
                "missing_sections": [],
            },
            {
                "structure.outline.empty-section",
                "structure.outline.missing-section",
            },
        ),
        (
            {
                "sections": [
                    {"id": "mulligan", "title": "Муллиган", "claims": ["x"]},
                    {"id": "builds", "title": "Сборки", "claims": ["y"]},
                    {"id": "bonus", "title": "Бонус", "claims": ["z"]},
                ],
                "missing_sections": ["deckbuilding", "strategy", "matchups", "mulligan"],
            },
            {
                "structure.outline.unknown-section",
                "structure.outline.order",
                "structure.outline.conflict",
            },
        ),
    ],
)
def test_check_outline(outline_json, expected):
    findings = structure.check_outline(outline_json, None, "constructed-guide")
    errors_and_reviews = {f["id"] for f in findings}
    assert errors_and_reviews == expected


def test_check_outline_reports_orphan_cards():
    outline_json = {
        "sections": [
            {"id": sid, "title": sid, "claims": ["общий тезис без карт"]}
            for sid in ("builds", "deckbuilding", "mulligan", "strategy", "matchups")
        ],
        "missing_sections": [],
    }
    claims = {"cards": [{"name": "Мастер брони", "mentions": 2}]}
    findings = structure.check_outline(outline_json, claims, "constructed-guide")
    assert "structure.outline.orphan-claim" in ids(findings)
    assert all(f["severity"] == "review" for f in findings)


def test_markdown_only_ignores_bare_short_lines():
    text = "## Сборки\n" + "Слово " * 70 + "\nМуллиган\n" + "Слово " * 70 + "\n"
    loose = structure.resolve_sections(text, SECTIONS)
    strict = structure.resolve_sections(text, SECTIONS, markdown_only=True)
    assert {"builds", "mulligan"} <= set(loose)
    assert set(strict) == {"builds"}
    assert structure.headings("Совет\n## Муллиган\n", markdown_only=True) == [(1, "Муллиган")]
