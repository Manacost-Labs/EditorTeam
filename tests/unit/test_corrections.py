"""Журнал правок: замечание автора становится правилом, а не остаётся в чате."""

import common as C
import pytest

from editorteam import corrections as CR

gate = C.sibling("rewrite_gate")


def test_seeded_journal_loads_and_kinds_are_valid():
    items = CR.load()
    assert len(items) >= 8
    assert all(c.kind in CR.KINDS for c in items)
    assert any(c.was == "карты-награды" for c in items)


def test_proposals_come_from_short_replacements_only():
    before = "На Бриллианте эта яичная сборка карает провайдеров и держит темп."
    after = "На Алмазе эта сборка на яйцах карает источники и держит темп."
    found = CR.proposals(before, after)
    pairs = {(p["was"], p["became"]) for p in found}
    assert ("Бриллианте", "Алмазе") in pairs
    assert ("провайдеров", "источники") in pairs
    assert all(len(p["was"].split()) <= CR.MAX_WORDS for p in found)
    rewritten = CR.proposals(
        "Абзац целиком другой и длинный, много слов подряд без замены.",
        "Совсем иной текст, который ничего общего с исходным не имеет вовсе.",
    )
    assert all(len(p["was"].split()) <= CR.MAX_WORDS for p in rewritten)


def test_add_and_load_round_trip(tmp_path, monkeypatch):
    file = tmp_path / "corrections.yaml"
    monkeypatch.setenv("EDITOR_CORRECTIONS", str(file))
    item = CR.add("оболочка", "версия", kind="term", reason="калька")
    assert item.date
    again = CR.add("оболочка", "версия", kind="term")
    assert again.was == item.was
    assert len(CR.load()) == 1
    with pytest.raises(CR.CorrectionsError):
        CR.add("то же", "то же")


def test_gate_flags_journal_terms_and_phrases():
    text = (
        "Сборки\n"
        + "Новые карты встроились в яичные сборки, а провайдеры расходятся в оценке. " * 3
    )
    hits = gate.terminology_hits(text)
    preferred = {h["preferred"] for h in hits}
    assert "сборка на яйцах" in preferred
    assert "источник статистики" in preferred


def test_prompt_view_carries_reason():
    view = CR.for_prompt()
    assert view and {"was", "became", "kind", "reason"} <= set(view[0])
