"""Регрессия смысловых и читательских ошибок публичной статьи."""

import common as C

from editorteam import profiles

clarity = C.sibling("clarity")


def ids(findings: list[dict]) -> set[str]:
    return {item["id"] for item in findings}


def test_keyword_used_as_card_is_hard_error() -> None:
    findings, _ = clarity.analyze("Купите Venomous на раннем ходу.", "battlegrounds-article")
    role = [item for item in findings if item["id"] == "clarity.entity.poison.role"]
    assert role
    assert role[0]["severity"] == "error"
    assert "существо с ядом" in role[0]["suggestion"]


def test_unlocalized_keyword_is_flagged_without_card_claim() -> None:
    findings, _ = clarity.analyze("Venomous помогает пережить первый бой.", "battlegrounds-article")
    assert "clarity.entity.poison.unlocalized" in ids(findings)
    assert "clarity.entity.poison.role" not in ids(findings)


def test_dark_gifts_use_official_name() -> None:
    findings, _ = clarity.analyze(
        "Подарки помогают раньше выбрать направление. Dark Gifts усиливают стол.",
        "battlegrounds-article",
    )
    found = ids(findings)
    assert "terminology.dark-gifts-generic" in found
    assert "clarity.entity.dark-gifts.unlocalized" in found


def test_creature_type_replaces_tribe_term() -> None:
    findings, _ = clarity.analyze(
        "Племенная привязка с самыми большими числами часто выигрывает.",
        "battlegrounds-article",
    )
    assert "terminology.minion-type" in ids(findings)


def test_official_battlegrounds_terms_are_clean() -> None:
    findings, _ = clarity.analyze(
        "Темные дары помогают выбрать направление. Темный дар связан с типом существа.",
        "battlegrounds-article",
    )
    found = ids(findings)
    assert "terminology.dark-gifts-generic" not in found
    assert "terminology.minion-type" not in found
    assert "clarity.entity.dark-gifts.unlocalized" not in found


def test_research_density_is_a_review_signal() -> None:
    text = (
        "Выборка игроков ограничена верхней половиной рейтинга. "
        "Данные показывают связь, но это не доказывает причинный эффект. "
        "Результат нельзя считать репрезентативным для всех игроков."
    )
    findings, metrics = clarity.analyze(text, "analytics-article")
    assert "clarity.paragraph.research-density" in ids(findings)
    assert metrics["research_dense_paragraphs"] == 1


def test_numeric_density_is_a_review_signal() -> None:
    text = "Показатели 12, 24, 36, 48, 60 и 72 требуют короткого пояснения для читателя."
    findings, metrics = clarity.analyze(text, "analytics-article")
    assert "clarity.paragraph.numeric-density" in ids(findings)
    assert metrics["numeric_dense_paragraphs"] == 1


def test_public_jargon_is_flagged() -> None:
    findings, _ = clarity.analyze(
        "Скриншот показывает редкий результат партии.", "battlegrounds-article"
    )
    assert "jargon.metaphor" in ids(findings)


def test_clear_intro_contains_problem_and_consequence() -> None:
    text = (
        "# Заголовок\n\n"
        "Проблема не в самой цифре, а в скорости развития игрока.\n\n"
        "Ранний эффект дает дополнительные действия и меняет темп партии.\n\n"
        "Из-за этого отстающий игрок получает меньше времени на перестройку.\n\n"
        "Поэтому матч решается раньше, чем появляется равный шанс на ответ."
    )
    findings, metrics = clarity.analyze(text, "battlegrounds-article")
    assert metrics["thesis_problem"] is True
    assert metrics["thesis_consequence"] is True
    assert "clarity.thesis.missing" not in ids(findings)


def test_dense_paragraph_is_review_signal() -> None:
    paragraph = " ".join(["Игрок получает ресурс и меняет ход партии."] * 20)
    findings, metrics = clarity.analyze(paragraph, "battlegrounds-article")
    assert metrics["dense_paragraphs"] == 1
    assert "clarity.paragraph.density" in ids(findings)


def test_profile_is_available_and_old_profiles_keep_clarity_off() -> None:
    assert "battlegrounds-article" in profiles.available()
    assert profiles.load("battlegrounds-article").enabled("clarity") is True
    assert profiles.load("battlegrounds-guide").enabled("clarity") is False


def test_model_rules_expose_player_facing_terms() -> None:
    rules = clarity.model_rules("battlegrounds-article")
    assert rules["audience"] == "обычный игрок Полей сражений"
    poison = next(item for item in rules["entities"] if item["kind"] == "keyword")
    assert poison["use"] == "существо с ядом"
    dark_gifts = next(item for item in rules["entities"] if item["kind"] == "system")
    assert dark_gifts["use"] == "Темные дары"
    assert "подарок" in rules["avoid"]
    assert "племя" in rules["avoid"]
    preferred = {item["use"] for item in rules["preferred_terms"]}
    assert "Темные дары / Темный дар" in preferred
    assert "тип существа / типы существ" in preferred


def test_analytics_model_rules_expose_editorial_shape() -> None:
    rules = clarity.model_rules("analytics-article")
    assert rules["audience"] == "обычный игрок Hearthstone"
    assert "Сначала назвать, что происходит сейчас" in rules["reader_contract"]["opening"]
    assert "ranking" in rules["formats"]
    assert rules["quality"]["max_paragraph_sentences"] == 4
