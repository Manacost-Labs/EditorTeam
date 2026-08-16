"""Флаг --строго должен менять поведение, иначе его быть не должно."""

import common as C

consistency = C.sibling("consistency")

# каждый вариант встречается ровно один раз — слабый сигнал
WEAK = "Пират Воин силен. В матч-апах важен темп, а в матчапе с Друидом контроль."

# вариант повторяется — сигнал надёжный
STRONG = ("В матч-апах важен темп. В матч-апах решает размен. "
          "А в матчапе с Друидом всё иначе, и в матчапе с Магом тоже.")


def test_weak_signal_hidden_by_default():
    assert consistency.check_variants(WEAK) == []


def test_weak_signal_shown_in_strict():
    assert consistency.check_variants(WEAK, strict=True) != []


def test_strong_signal_shown_in_both_modes():
    assert consistency.check_variants(STRONG) != []
    assert consistency.check_variants(STRONG, strict=True) != []


def test_strict_is_superset():
    """Строгий режим не может показать меньше обычного."""
    for text in (WEAK, STRONG):
        assert len(consistency.check_variants(text, strict=True)) >= \
               len(consistency.check_variants(text))
