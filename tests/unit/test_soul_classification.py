"""Потеря голоса определяется по числу сигналов, а не только по частоте.

Прежняя версия сравнивала per1k: расширение текста читалось как «ПОТЕРЯ»,
а сокращение маскировало настоящее удаление.
"""

import common as C

soul = C.sibling("soul")

LIVE = ("Оставляйте Гарпунную пушку во всех матч-апах. Но не спешите играть ее рано. "
        "Хотя соблазн велик, вы потеряете темп. Раскапывайте механизмы (особенно против Мага). ")
FILLER = ("Колода использует стандартный набор карт и работает по обычной схеме размена. "
          "Архетип сформировался давно и с тех пор менялся незначительно. ")


def m(text):
    return soul.measure(text)


def test_expansion_is_not_loss():
    before, bw = m(LIVE * 8)
    now, nw = m(LIVE * 8 + FILLER * 20)
    for name in soul.SIGNALS:
        state = soul.classify(before[name], now[name], bw, nw)
        assert state != "сигналы удалены", name
        assert now[name]["n"] == before[name]["n"]


def test_real_removal_is_caught():
    before, bw = m(LIVE * 8)
    now, nw = m(FILLER * 20)
    states = [soul.classify(before[n], now[n], bw, nw) for n in soul.SIGNALS]
    assert "сигналы удалены" in states


def test_shrink_hiding_removal_is_caught():
    """Сокращение поднимает частоту — раньше это скрывало удаление."""
    # обе версии длиннее порога MIN_WORDS, иначе вердикт «мало данных»
    before, bw = m(LIVE * 16 + FILLER * 40)
    now, nw = m(LIVE * 8)
    imp = "императив читателю"
    assert now[imp]["per1k"] > before[imp]["per1k"]      # частота выросла
    assert soul.classify(before[imp], now[imp], bw, nw) == "сигналы удалены"


def test_small_sample_gets_no_verdict():
    before, bw = m(LIVE)
    now, nw = m(LIVE)
    for name in soul.SIGNALS:
        assert soul.classify(before[name], now[name], bw, nw) == "мало данных"
