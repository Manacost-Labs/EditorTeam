"""Конфигурация правил валидируется, конфликты ловятся тестом."""

import pytest
import yaml

from editorteam import rules


def test_config_is_valid():
    assert rules.validate() == []


def test_terminology_loads():
    terms = rules.terminology()
    assert len(terms) >= 10
    ids = [t.id for t in terms]
    assert len(ids) == len(set(ids)), "id должны быть уникальны"


def test_winrate_is_allowed_not_replaced():
    """Отменённое правило должно остаться отменённым."""
    r = next(t for t in rules.terminology() if t.id == "term.winrate")
    assert r.decision == "allowed"
    assert r.replacement_for("винрейт") is None


def test_deck_is_replaced():
    r = next(t for t in rules.terminology() if t.id == "term.deck")
    assert r.replacement_for("дека") == "колода"


def test_duplicate_id_is_caught(monkeypatch):
    bad = {"rules": [
        {"id": "x", "slang": "a", "decision": "allowed"},
        {"id": "x", "slang": "b", "decision": "allowed"},
    ]}
    monkeypatch.setattr(rules, "_load_yaml", lambda name: bad if "term" in name else {})
    rules.terminology.cache_clear()
    rules.typography.cache_clear()
    problems = rules.validate()
    rules.terminology.cache_clear()
    rules.typography.cache_clear()
    assert any("повторяющийся id" in p for p in problems)


def test_conflicting_decisions_are_caught(monkeypatch):
    bad = {"rules": [
        {"id": "a", "slang": "дека", "decision": "allowed"},
        {"id": "b", "slang": "дека", "decision": "auto_replace", "preferred": "колода"},
    ]}
    monkeypatch.setattr(rules, "_load_yaml", lambda name: bad if "term" in name else {})
    rules.terminology.cache_clear()
    rules.typography.cache_clear()
    problems = rules.validate()
    rules.terminology.cache_clear()
    rules.typography.cache_clear()
    assert any("противоречивые решения" in p for p in problems)


def test_replacement_against_corpus_is_caught(monkeypatch):
    """Нельзя заменять слово на более редкое в корпусе — это ошибка винрейта."""
    bad = {"rules": [{
        "id": "z", "slang": "винрейт", "decision": "auto_replace",
        "preferred": "процент побед", "corpus": {"slang": 68, "preferred": 17},
    }]}
    monkeypatch.setattr(rules, "_load_yaml", lambda name: bad if "term" in name else {})
    rules.terminology.cache_clear()
    rules.typography.cache_clear()
    problems = rules.validate()
    rules.terminology.cache_clear()
    rules.typography.cache_clear()
    assert any("чаще исходное слово" in p for p in problems)


def test_unknown_decision_rejected(monkeypatch):
    bad = {"rules": [{"id": "q", "slang": "x", "decision": "выдумка"}]}
    monkeypatch.setattr(rules, "_load_yaml", lambda name: bad)
    rules.terminology.cache_clear()
    with pytest.raises(rules.ConfigError):
        rules.terminology()
    rules.terminology.cache_clear()


def test_markdown_table_matches_config():
    table = rules.as_markdown_table()
    assert "винрейт" in table and "оставлять" in table
    assert "дека" in table


def test_yaml_files_parse():
    for f in rules.CONFIG_DIR.rglob("*.yaml"):
        assert yaml.safe_load(f.read_text(encoding="utf-8")) is not None
