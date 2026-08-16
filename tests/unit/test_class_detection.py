"""Составные имена классов не должны растворяться в коротких.

До исправления «Охотник на демонов» засчитывался как «Охотник»,
а сам Охотник на демонов терялся.
"""

import pytest

import common as C

structure = C.sibling("structure")


def matchups(body):
    text = "Матч-апы\n" + body + " " * 600     # объём, чтобы взялся хвост раздела
    heads = structure.headings(text)
    found = structure.find_blocks(heads)
    return structure.check_matchups(text, heads, found)


def test_only_demon_hunter():
    seen, missing = matchups("Против Охотника на демонов играем быстро.")
    assert "Охотник на демонов" in seen
    assert "Охотник" in missing


def test_only_hunter():
    seen, missing = matchups("Против Охотника держим стол.")
    assert "Охотник" in seen
    assert "Охотник на демонов" in missing


def test_both_classes():
    seen, _ = matchups("Против Охотника держим стол, против Охотника на демонов спешим.")
    assert "Охотник" in seen
    assert "Охотник на демонов" in seen


@pytest.mark.parametrize("form", [
    "Охотнику на демонов", "Охотником на демонов", "Охотники на демонов",
])
def test_declined_demon_hunter(form):
    seen, _ = matchups(f"Тяжело против {form} в первые ходы.")
    assert "Охотник на демонов" in seen


@pytest.mark.parametrize("form,cls", [
    ("Рыцарю смерти", "Рыцарь смерти"),
    ("Жрецом", "Жрец"),
    ("Чернокнижника", "Чернокнижник"),
])
def test_declined_other_classes(form, cls):
    seen, _ = matchups(f"Матч-ап с {form} обычно простой.")
    assert cls in seen
