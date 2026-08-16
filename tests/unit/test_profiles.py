"""Профили жанров: BG-материал не мерится требованиями гайда по колоде."""

import pytest

from editorteam import profiles

BG = (
    "Идея стратегии\nСобираем мурлоков в тавернах и держим темп.\n"
    "Ключевые существа\nБрановый мурлок и Мурлок-налетчик.\n"
    "План по ходам\nНа четвертом ходу ищем тройку существ.\n"
    "Герой таверны выбирается под аксессуары в лобби.\n"
)

CONSTRUCTED = (
    "Сборки архетипа\nМуллиган\nСтратегия игры\nМатч-апы\n"
    "Вопросы декбилдинга\nКолода играет через размен и добор.\n"
)


def test_all_profiles_load():
    for name in profiles.available():
        p = profiles.load(name)
        assert p.id == name


def test_battlegrounds_does_not_require_deck_sections():
    bg = profiles.load("battlegrounds-guide")
    ids = {s.id for s in bg.required_sections}
    assert "mulligan" not in ids
    assert "deckbuilding" not in ids
    assert "matchups" not in ids
    assert bg.require_classes is False


def test_battlegrounds_requires_its_own_sections():
    bg = profiles.load("battlegrounds-guide")
    ids = {s.id for s in bg.required_sections}
    assert {"idea", "minions", "curve"} <= ids


def test_constructed_requires_eleven_classes():
    p = profiles.load("constructed-guide")
    assert p.require_classes is True
    assert len(p.required_sections) == 5


def test_news_disables_noisy_analyzers():
    p = profiles.load("news")
    assert p.enabled("rhythm") is False
    assert p.enabled("soul") is False
    assert p.enabled("cards") is True


def test_unknown_profile_raises():
    with pytest.raises(profiles.ProfileError):
        profiles.load("нет-такого")


def test_detect_battlegrounds():
    name, conf = profiles.detect(BG)
    assert name == "battlegrounds-guide"
    assert conf > 0.3


def test_detect_constructed():
    name, _ = profiles.detect(CONSTRUCTED)
    assert name == "constructed-guide"


def test_detect_reports_zero_confidence_on_empty():
    name, conf = profiles.detect("Просто текст ни о чём.")
    assert conf == 0.0
    assert name == profiles.DEFAULT


def test_weights_sum_to_one():
    for name in profiles.available():
        p = profiles.load(name)
        assert abs(sum(p.weights.values()) - 1.0) < 1e-6, name
