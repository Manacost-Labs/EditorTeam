"""Игровые паки: что зависит от игры, и честность заимствованных норм."""

import pytest

from editorteam import games, profiles


def test_all_games_load():
    for name in games.available():
        assert games.load(name).id == name


def test_hearthstone_has_own_norms():
    g = games.load("hearthstone")
    assert g.norms.provisional is False
    assert g.norms.caveat() is None
    assert g.has_name_check


def test_wow_norms_are_marked_provisional():
    """Норм по WoW нет — система обязана это говорить, а не молчать."""
    g = games.load("wow")
    assert g.norms.provisional is True
    caveat = g.norms.caveat()
    assert caveat and "предварительные" in caveat


def test_wow_name_check_disabled_with_reason():
    g = games.load("wow")
    assert not g.has_name_check
    reason = g.skip_reason("cards")
    assert reason and "Blizzard" in reason


def test_wow_author_score_not_calibrated():
    """Балл соответствия без корпуса калибровать не на чем."""
    assert games.load("wow").norms.author_median is None
    assert games.load("hearthstone").norms.author_median == 9.1


def test_game_profiles_exist():
    known = set(profiles.available())
    for name in games.available():
        for p in games.load(name).profiles:
            assert p in known, f"{name}: профиль {p} не найден"


def test_unknown_game_raises():
    with pytest.raises(games.GameError):
        games.load("tetris")


def test_protected_words_present():
    assert "ОТК" in games.load("hearthstone").protected
    assert "ГКД" in games.load("wow").protected
