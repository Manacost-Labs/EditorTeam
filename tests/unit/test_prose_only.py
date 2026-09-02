"""Метрики голоса и ритма считаются по прозе: таблица — не предложение."""

import common as C

soul = C.sibling("soul")
rhythm = C.sibling("rhythm")
elegance = C.sibling("elegance")

PROSE = (
    "Вы копите броню и замешиваете бомбы. Но не спешите. "
    "Стол важнее урона в лицо, хотя против Мага темп решает больше, чем размен, "
    "и это стоит помнить с первого хода. Держите Мастера брони.\n"
)
TABLE = (
    "| Колода | Легенда | Топ |\n|---|---|---|\n"
    + "| Атак Друид | 56,2% / 66 183 | 55,2% / 8 459 |\n" * 12
)
CODE = "```\nAAECAZICBs2eBpKDB6+HB+DAB+XEB6PaBwyunwSIgweqrwesrwfosQe+sgfXwAfk2QeR2gfH5ge85wfK5wcAAA==\n```\n"


def test_prose_only_drops_tables_codes_and_headings_but_keeps_lists():
    text = "## Сборки\n" + PROSE + TABLE + CODE + "- пункт списка остаётся\n> цитата остаётся\n"
    kept = C.prose_only(text)
    assert "Атак Друид" not in kept
    assert "AAECA" not in kept
    assert "## Сборки" not in kept
    assert "пункт списка остаётся" in kept
    assert "цитата остаётся" in kept


def test_rhythm_and_soul_ignore_tables():
    with_table = PROSE + "\n" + TABLE + CODE
    assert rhythm.measure(with_table)["ratio"] == rhythm.measure(PROSE)["ratio"]
    assert rhythm.measure(with_table, prose=False)["max"] > rhythm.measure(PROSE)["max"]
    s_table, w_table = soul.measure(with_table)
    s_prose, w_prose = soul.measure(PROSE)
    assert w_table == w_prose
    assert s_table["обращение к читателю"]["per1k"] == s_prose["обращение к читателю"]["per1k"]
    assert soul.measure(with_table, prose=False)[1] > w_prose


def test_elegance_ignores_tables():
    with_table = PROSE + "\n" + TABLE
    assert elegance.measure(with_table)["words"] == elegance.measure(PROSE)["words"]
