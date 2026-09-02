"""Дымовые тесты: анализаторы импортируются и не падают на краевых входах."""

import common as C
import pytest

ANALYZERS = [
    "markers",
    "rhythm",
    "soul",
    "structure",
    "consistency",
    "cards",
    "author",
    "guide_voice",
    "clarity",
    "certainty_guard",
    "semantic_diff",
    "rewrite_gate",
    "claims",
    "elegance",
    "lexicon",
]


@pytest.mark.parametrize("name", ANALYZERS)
def test_analyzer_imports(name):
    assert C.sibling(name) is not None


@pytest.mark.parametrize("text", ["", "   ", "Одно.", "Слово"])
def test_soul_survives_tiny_input(text):
    soul = C.sibling("soul")
    result, words = soul.measure(text)
    assert result is None or isinstance(result, dict)


@pytest.mark.parametrize("text", ["", "Короткий текст."])
def test_rhythm_survives_tiny_input(text):
    rhythm = C.sibling("rhythm")
    assert rhythm.measure(text) is None or isinstance(rhythm.measure(text), dict)


def test_paths_resolve():
    assert C.ROOT.exists()
    assert C.SCRIPTS.name == "scripts"
    assert C.ASSETS.name == "assets"
