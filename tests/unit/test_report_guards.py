"""Отчёт до/после обязан ловить потерю голоса и защищённых элементов."""

import common as C

report = C.sibling("report")

LIVE = ("Оставляйте Гарпунную пушку во всех матч-апах. Но не спешите играть ее рано. "
        "Хотя соблазн велик, вы потеряете темп. Раскапывайте механизмы (особенно против Мага). ")
DRY = ("Гарпунную пушку следует оставлять во всех ситуациях. Игра на третьем ходу "
       "приводит к потере темпа. Механизмы требуется раскапывать заранее. ")


def test_protected_numbers_loss_detected():
    a = "Карта стоит 3 маны и имеет статы 4/5.\n"
    b = "Карта стоит маны и имеет статы.\n"
    gone = report.protected_lost(a, b)
    assert "числа" in gone and "статы" in gone


def test_protected_otk_loss_detected():
    gone = report.protected_lost("Колода делает ОТК на девятом ходу.\n",
                                 "Колода добивает на девятом ходу.\n")
    assert "ОТК" in gone


def test_no_false_protected_loss():
    a = "Карта стоит 3 маны, статы 4/5, это ОТК.\n"
    assert report.protected_lost(a, a) == {}


def test_deck_code_loss_detected():
    a = "Код колоды:\nAAECAZ8FHoQBqAKLA9EDh"
    b = "Код колоды:\n"
    assert "коды колод" in report.protected_lost(a, b)
