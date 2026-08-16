"""Мутационные тесты: детектор обязан срабатывать на своей ошибке.

Регрессия по корпусу проверяет обратное — что детекторы молчат на хорошем
тексте. Без этих тестов половина каталога могла бы не работать вовсе, и
никто бы не заметил: на вычитанных гайдах молчит и сломанный детектор.

Каждая фикстура — чистый образец с ровно одной внесённой ошибкой.
"""

import json
import subprocess
import sys
from functools import lru_cache

import pytest

FIXTURES = "tests/fixtures"
CLEAN = f"{FIXTURES}/negative/clean-guide.md"

# мутация -> детектор, который обязан её поймать
EXPECTED = {
    "cards-apostrophe": "cards.apostrophe",
    "cards-dash": "cards.dash",
    "consistency-variant": "consistency.variants",
    "structure-missing-mulligan": "structure.missing.mulligan",
    "markers-kancelarit": "markers.kancelarit-svyazka",
    "markers-chat": "markers.chat-ostatki",
    "advice-contradiction": "consistency.advice",
}


@lru_cache(maxsize=None)
def _audit(path: str, profile: str) -> str:
    """Разбор кешируется: индекс из 6602 карт строится один раз на файл,
    иначе набор идёт минуту вместо секунд."""
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
    return r.stdout


def audit(capsys, path, profile="constructed-guide"):
    return json.loads(_audit(path, profile))


def ids(data):
    return {f["id"] for f in data["findings"]}


def test_clean_fixture_has_no_findings(capsys):
    """Основа для мутаций обязана быть чистой, иначе тесты ничего не значат."""
    data = audit(capsys, CLEAN)
    assert data["findings"] == [], ids(data)


@pytest.mark.parametrize("mutation,detector", sorted(EXPECTED.items()))
def test_mutation_is_detected(capsys, mutation, detector):
    data = audit(capsys, f"{FIXTURES}/positive/{mutation}.md")
    assert detector in ids(data), f"{mutation}: ожидался {detector}, получено {ids(data)}"


@pytest.mark.parametrize("mutation,detector", sorted(EXPECTED.items()))
def test_mutation_does_not_trigger_others(capsys, mutation, detector):
    """Одна ошибка — одна находка: детектор не должен цеплять соседей."""
    data = audit(capsys, f"{FIXTURES}/positive/{mutation}.md")
    assert ids(data) == {detector}, f"{mutation}: лишние находки {ids(data) - {detector}}"


def test_card_errors_are_exact_not_heuristic(capsys):
    """Сверка со справочником — единственное, что зовётся error."""
    data = audit(capsys, f"{FIXTURES}/positive/cards-apostrophe.md")
    card = next(f for f in data["findings"] if f["id"] == "cards.apostrophe")
    assert card["severity"] == "error"
    assert card["suggestion"] == "Кел'Тузад"


def test_advice_is_review_not_verdict(capsys):
    """Смысл советов машина не разбирает — только сигнал редактору."""
    data = audit(capsys, f"{FIXTURES}/positive/advice-contradiction.md")
    adv = next(f for f in data["findings"] if f["id"] == "consistency.advice")
    assert adv["severity"] == "review"
    assert adv["confidence"] < 0.6


def test_structure_finding_carries_corpus_share(capsys):
    """Требование раздела опирается на частоту в корпусе, а не на вкус."""
    data = audit(capsys, f"{FIXTURES}/positive/structure-missing-mulligan.md")
    st = next(f for f in data["findings"] if f["id"] == "structure.missing.mulligan")
    assert st["meta"]["corpus_share"] == 100
