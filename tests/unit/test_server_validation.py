"""Регрессии затвора для коротких и длинных текстов."""

from editorteam.server import validate


def _kinds(result: dict) -> set[str]:
    return {item["kind"] for item in result["violations"]}


def test_short_arena_correction_is_accepted() -> None:
    before = (
        "Текущаю ситуация в мете на Арене после выхода мини набора. "
        "Как можно заметить абсолютную доминацию Воина."
    )
    after = (
        "Текущая ситуация в мете Арены после выхода мини-набора. "
        "Как можно заметить, Воин абсолютно доминирует."
    )

    result = validate(before, after, "hearthstone", "constructed-guide")

    assert result["accepted"] is True
    assert "rhythm_flattened" not in _kinds(result)
    assert result["metrics"]["rhythm_before"] is not None
    assert result["metrics"]["rhythm_after"] is not None


def test_short_edit_may_remove_filler_without_shrink_rejection() -> None:
    before = (
        "Текущая ситуация в мете Арены после выхода мини-набора. "
        "Как можно заметить, Воин абсолютно доминирует."
    )
    after = (
        "Текущая ситуация в мете Арены после выхода мини-набора. "
        "Воин абсолютно доминирует."
    )

    result = validate(before, after, "hearthstone", "constructed-guide")

    assert "text_shrunk" not in _kinds(result)


def test_severe_shortening_is_still_rejected() -> None:
    before = (
        "Текущая ситуация в мете Арены после выхода мини-набора заметно изменилась. "
        "Как можно заметить, Воин теперь абсолютно доминирует почти в каждом матче."
    )
    after = "Воин доминирует."

    result = validate(before, after, "hearthstone", "constructed-guide")

    assert "text_shrunk" in _kinds(result)


def test_article_sized_flattening_is_still_rejected() -> None:
    before_sentences = []
    after_sentences = []
    for index in range(15):
        before_length = 3 if index % 2 == 0 else 14
        before_sentences.append(" ".join([f"слово{index}"] * before_length) + ".")
        after_sentences.append(" ".join([f"слово{index}"] * 8) + ".")

    result = validate(
        " ".join(before_sentences),
        " ".join(after_sentences),
        "hearthstone",
        "constructed-guide",
    )

    assert "rhythm_flattened" in _kinds(result)
