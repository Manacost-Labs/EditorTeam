"""Совет привязывается к карте в своей части предложения.

«Оставляйте A, но сбрасывайте B» — два совета к разным картам.
Прежняя версия приписывала отрицательный совет обеим.
"""

import common as C

consistency = C.sibling("consistency")

MIXED = ("Оставляйте Мастера брони, но сбрасывайте Боевую ярость против агро.\n"
         "Мастер брони хорош в защите, держите его всегда.\n")

REAL = ("Оставляйте Мастера брони против агро колод.\n"
        "Против быстрых колод сбрасывайте Мастера брони, он слишком медленный.\n")


def test_split_sentence_is_not_a_contradiction():
    assert consistency.check_advice(MIXED) == {}


def test_real_contradiction_still_caught():
    assert "Мастер брони" in consistency.check_advice(REAL)


def test_segments_split_on_adversative():
    parts = consistency.segments("Оставляйте A, но сбрасывайте B")
    assert len(parts) == 2
    assert "но" not in parts[1].split()[0].lower()


def test_segments_keep_simple_sentence_whole():
    assert consistency.segments("Оставляйте Мастера брони всегда") == \
        ["Оставляйте Мастера брони всегда"]
