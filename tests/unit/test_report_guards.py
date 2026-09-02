"""Отчёт до/после обязан ловить потерю голоса и защищённых элементов."""

import common as C

report = C.sibling("report")

LIVE = (
    "Оставляйте Гарпунную пушку во всех матч-апах. Но не спешите играть ее рано. "
    "Хотя соблазн велик, вы потеряете темп. Раскапывайте механизмы (особенно против Мага). "
)
DRY = (
    "Гарпунную пушку следует оставлять во всех ситуациях. Игра на третьем ходу "
    "приводит к потере темпа. Механизмы требуется раскапывать заранее. "
)


def test_protected_numbers_loss_detected():
    a = "Карта стоит 3 маны и имеет статы 4/5.\n"
    b = "Карта стоит маны и имеет статы.\n"
    gone = report.protected_lost(a, b)
    assert "числа" in gone and "статы" in gone


def test_protected_otk_loss_detected():
    gone = report.protected_lost(
        "Колода делает ОТК на девятом ходу.\n", "Колода добивает на девятом ходу.\n"
    )
    assert "ОТК" in gone


def test_no_false_protected_loss():
    a = "Карта стоит 3 маны, статы 4/5, это ОТК.\n"
    assert report.protected_lost(a, a) == {}


def test_deck_code_loss_detected():
    a = "Код колоды:\nAAECAZ8FHoQBqAKLA9EDh"
    b = "Код колоды:\n"
    assert "коды колод" in report.protected_lost(a, b)


P1 = "Оставляйте Мастера брони против агро, он держит первые ходы и не даёт разогнаться.\n"
P2 = "Против Мага держите ответ на раннюю доску, иначе темп уйдёт к третьему ходу.\n"
P3 = "В лейтгейме колода выигрывает за счёт добора, и спешить с разменом не нужно.\n"


def test_moved_paragraph_is_not_counted_as_rewritten():
    before = P1 + "\n" + P2 + "\n" + P3
    after = P1 + "\n" + P3 + "\n" + P2
    d = report.diff_stats(before, after)
    assert d["changed"] == 0 and d["moved"] == 1
    kinds = [tag for tag, _, _ in report.edits(before, after)]
    assert kinds == ["move"]


def test_edited_paragraph_still_counts_words():
    before = P1 + "\n" + P2
    after = P1 + "\n" + P2.replace("держите", "оставляйте")
    d = report.diff_stats(before, after)
    assert d["changed"] == 1 and d["moved"] == 0
    assert ("replace", "держите", "оставляйте") in report.edits(before, after)


def test_added_and_removed_paragraphs_are_reported():
    """Сильно переписанный абзац на том же месте — правка; лишний или
    пропавший абзац — добавление или удаление целиком."""
    d = report.diff_stats(P1 + "\n" + P2, P1 + "\n" + P3)
    assert d["paragraphs_removed"] == 0 and d["paragraphs_added"] == 0 and d["changed"] > 0
    d = report.diff_stats(P1 + "\n" + P2 + "\n" + P3, P1 + "\n" + P3)
    assert d["paragraphs_removed"] == 1 and d["paragraphs_added"] == 0
    d = report.diff_stats(P1, P1 + "\n" + P2)
    assert d["paragraphs_added"] == 1 and d["inserted"] == len(P2.split())
