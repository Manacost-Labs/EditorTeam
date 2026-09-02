"""Детектор чужих слов: автор не чужой самому себе, слоп — чужой."""

from pathlib import Path

import common as C
import pytest

lexicon = C.sibling("lexicon")
CONTROL = Path("tests/evals/cases/00-control-clean-guide/input.md").read_text(encoding="utf-8")
SLOP = Path("tests/evals/cases/01-slop-rhetoric-bomb-warrior/input.md").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def lex():
    return lexicon.corpus_lexicon()


def test_corpus_lexicon_is_large(lex):
    assert len(lex) > 5000


def test_control_text_is_within_author_range(lex):
    m = lexicon.measure(CONTROL, lexicon=lex)
    assert m["ratio"] <= lexicon.WARN_PCT, m["missing"]
    assert lexicon.findings(CONTROL, m) == []


def test_report_language_is_foreign(lex):
    text = CONTROL + (
        "\nВыборка репрезентативна, корреляция подтверждена, доверительные интервалы "
        "не публикуются провайдерами, а методология классификации архетипов "
        "остаётся предварительной. Аналитический вывод требует верификации."
    )
    m = lexicon.measure(text, lexicon=lex)
    assert m["ratio"] > lexicon.measure(CONTROL, lexicon=lex)["ratio"]
    assert {"репрезентативна", "корреляция", "методология"} <= set(m["missing"])


def test_names_and_cards_are_not_counted(lex):
    text = (
        CONTROL
        + "\nБерите Гарольд Чернокнижника: Незримый атлас и Крестный отец Казакус разгоняют руку."
    )
    m = lexicon.measure(text, lexicon=lex)
    assert "гарольд" not in m["missing"]
    assert "незримый" not in m["missing"]


def test_findings_severity_follows_thresholds(lex):
    m = {"words": 300, "unique": 100, "missing": ["a"] * 5, "ratio": 5.0}
    assert lexicon.findings("x", m)[0]["severity"] == "review"
    m["ratio"], m["missing"] = 7.0, ["a"] * 7
    assert lexicon.findings("x", m)[0]["severity"] == "error"
    assert (
        lexicon.findings("x", {"words": 50, "unique": 10, "missing": ["a"] * 3, "ratio": 30.0})
        == []
    )
