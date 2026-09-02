"""Регрессии затвора для коротких и длинных текстов."""

import pytest

from editorteam.server import validate


def _kinds(result: dict) -> set[str]:
    return {item["kind"] for item in result["violations"]}


def _warning_kinds(result: dict) -> set[str]:
    return {item["kind"] for item in result["warnings"]}


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
    after = "Текущая ситуация в мете Арены после выхода мини-набора. Воин абсолютно доминирует."

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


def test_moderate_shortening_requires_review_without_automatic_rejection() -> None:
    before = " ".join("Мы сохраняем эту мысль." for _ in range(20))
    after = " ".join("Мы сохраняем эту мысль." for _ in range(16))

    result = validate(before, after, "hearthstone", "constructed-guide")

    assert result["accepted"] is True
    assert "text_shrunk" not in _kinds(result)
    assert "text_shrunk" in _warning_kinds(result)


def test_short_voice_loss_requires_review_without_automatic_rejection() -> None:
    before = "Рассмотрите этот ход. Но он оставляет вам запасной план."
    after = "Этот ход оставляет запасной план."

    result = validate(before, after, "hearthstone", "constructed-guide")

    assert result["accepted"] is True
    assert "voice_flattened" not in _kinds(result)
    assert "voice_lost" in _warning_kinds(result)


# ── переплавка: точка отсчёта — норма автора, а не исходник ─────────────────

SLOP = (
    "Стоит отметить, что Бомб Воин является одной из наиболее интересных колод. "
    "Важно понимать, что колода демонстрирует впечатляющие результаты на высоких рангах. "
    "Ключевыми картами являются Мастер брони и Боевой якорррь. "
    "Не рекомендуется оставлять Мастера брони против агрессивных колод. "
    "Подведём итог: архетип поистине уникален, и время покажет его судьбу. "
) * 3

REWRITE = (
    "Сборки\n"
    "Герой гайда — Бомб Воин в Некроситете. Колода сильнее всего в Легенде. "
    "Но на низких рангах вы встретите её реже, хотя играть ею проще.\n"
    "Вопросы декбилдинга\n"
    "Основа колоды — Мастер брони и Боевой якорррь. Остальные слоты подбирайте под локальную мету "
    "(обычно их два или три).\n"
    "Муллиган\n"
    "Ищите Боевой якорррь. Не оставляйте Мастера брони против агрессивных колод: он не успевает.\n"
    "Стратегия игры\n"
    "Вы копите броню и замешиваете бомбы. Не спешите. Стол важнее урона в лицо.\n"
    "Матч-апы\n"
    "Против Мага держите темп. Жрец требует бережности к ремувалам.\n"
)


def test_rewrite_depth_disables_relative_checks_and_uses_absolute_gate() -> None:
    result = validate(SLOP, REWRITE, "hearthstone", "constructed-guide", depth="переплавка")

    assert result["edit_depth"] == "переплавка"
    assert "text_shrunk" not in _kinds(result) | _warning_kinds(result)
    assert "voice_flattened" not in _kinds(result)
    assert "rhythm_flattened" not in _kinds(result)
    assert "rewrite_voice_total" in result["metrics"]
    assert result["metrics"]["coverage_cards_total"] == 2
    assert result["metrics"]["coverage_cards_covered"] == 2


def test_rewrite_depth_rejects_lost_card_and_flipped_negation() -> None:
    flipped = REWRITE.replace(
        "Не оставляйте Мастера брони против агрессивных колод: он не успевает.",
        "Оставляйте Мастера брони против агрессивных колод.",
    )
    result = validate(SLOP, flipped, "hearthstone", "constructed-guide", depth="переплавка")
    assert "CLAIM_COVERAGE_LOST" in _kinds(result)
    assert result["accepted"] is False

    dropped = REWRITE.replace("Мастер брони и Боевой якорррь", "две карты").replace(
        "Не оставляйте Мастера брони против агрессивных колод: он не успевает.", ""
    )
    result = validate(SLOP, dropped, "hearthstone", "constructed-guide", depth="переплавка")
    lost = [v for v in result["violations"] if v["kind"] == "CLAIM_COVERAGE_LOST"]
    assert any(v.get("field") == "card" for v in lost)


def test_rewrite_depth_flags_missing_section_unless_declared() -> None:
    without_mulligan = REWRITE.replace(
        "Муллиган\nИщите Боевой якорррь. Не оставляйте Мастера брони против агрессивных колод: "
        "он не успевает.\n",
        "",
    )
    source = SLOP.replace("Не рекомендуется оставлять Мастера брони против агрессивных колод. ", "")
    result = validate(
        source, without_mulligan, "hearthstone", "constructed-guide", depth="переплавка"
    )
    assert "structure_missing" in _kinds(result)

    declared = validate(
        source,
        without_mulligan,
        "hearthstone",
        "constructed-guide",
        depth="переплавка",
        declared_missing=["mulligan"],
    )
    assert "structure_missing" not in _kinds(declared)
    assert "structure_declared_missing" in _warning_kinds(declared)
    assert declared["declared_missing"] == ["mulligan"]


def test_default_depth_keeps_old_behaviour_and_unknown_depth_raises() -> None:
    result = validate(SLOP, "Воин доминирует.", "hearthstone", "constructed-guide")
    assert result["edit_depth"] == "обычная"
    assert "text_shrunk" in _kinds(result)
    with pytest.raises(ValueError):
        validate(SLOP, REWRITE, "hearthstone", "constructed-guide", depth="medium")


def test_rewrite_ignores_header_numbers_and_wrapped_codes_but_keeps_facts() -> None:
    before = (
        "Патч 36.4 • срез 31 августа 2026 года\n"
        "стр. 2\n"
        "Патч 36.4 • стр. 3\n"
        "Пират Воин держит 59,4% побед за 94 351 игру.\n"
        "AAECAZICBs2eBpKDB6+HB+DAB+XEB6PaBwyunwSIgweqrwesrwfosQe+sgfXwAfk2QeR2gfH5g\n"
        "e85wfK5wcAAA==\n"
    )
    after = (
        "Сборки\nПират Воин держит 59,4% побед за 94 351 игру в патче 36.4.\n"
        "AAECAZICBs2eBpKDB6+HB+DAB+XEB6PaBwyunwSIgweqrwesrwfosQe+sgfXwAfk2QeR2gfH5ge85wfK5wcAAA==\n"
    )
    result = validate(before, after, "hearthstone", "constructed-guide", depth="переплавка")
    assert "protected_lost" not in _kinds(result), result["violations"]
    assert "FACTUAL_SEMANTIC_DRIFT" not in _kinds(result)

    dropped = "Сборки\nПират Воин держит много побед в патче 36.4.\n"
    result = validate(before, dropped, "hearthstone", "constructed-guide", depth="переплавка")
    lost = [v for v in result["violations"] if v["kind"] == "protected_lost"]
    assert any("59,4%" in v["message"] for v in lost)
    assert any("коды колод" in v["message"] for v in lost)
