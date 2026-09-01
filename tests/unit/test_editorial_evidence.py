"""Evidence может управлять советом, но не голосом гайда."""

from editorteam.server import analyze, rules_for, validate


def _findings(result: dict, analyzer: str) -> list[dict]:
    return [item for item in result["findings"] if item["analyzer"] == analyzer]


def _kinds(result: dict) -> set[str]:
    return {item["kind"] for item in result["violations"]}


def test_guide_mode_hides_replay_narration() -> None:
    result = analyze(
        "В 82% replay игроки оставляют X.",
        "hearthstone",
        "constructed-guide",
        "GUIDE",
    )

    hits = _findings(result, "guide_voice")
    assert hits
    assert hits[0]["suggestion"] == "X почти всегда стоит оставлять."


def test_analysis_mode_allows_statistical_form() -> None:
    result = analyze(
        "В 82% replay игроки оставляют X.",
        "hearthstone",
        "constructed-guide",
        "ANALYSIS",
    )
    assert _findings(result, "guide_voice") == []


def test_guide_may_remove_evidence_percentage_but_not_author_number() -> None:
    result = validate(
        "В 82% replay игроки оставляют X.",
        "X почти всегда стоит оставлять.",
        "hearthstone",
        "constructed-guide",
    )
    assert "FACTUAL_SEMANTIC_DRIFT" not in _kinds(result)
    assert "protected_lost" not in _kinds(result)

    author_number = validate(
        "Колода показывает 55% побед.",
        "Колода показывает хороший результат.",
        "hearthstone",
        "constructed-guide",
    )
    assert "FACTUAL_SEMANTIC_DRIFT" in _kinds(author_number)


def test_low_confidence_cannot_become_mandatory() -> None:
    claim = {"claim_id": "m1", "meaning": {"card": "X"}, "confidence": "LOW"}
    result = validate(
        "X можно оставить.",
        "X обязательно оставляйте.",
        "hearthstone",
        "constructed-guide",
        claims_before=[claim],
        claims_after=[claim],
    )
    assert "CERTAINTY_DRIFT" in _kinds(result)


def test_negation_flip_is_hard_semantic_error() -> None:
    result = validate(
        "Не оставляйте X.",
        "Оставляйте X.",
        "hearthstone",
        "constructed-guide",
    )
    assert "FACTUAL_SEMANTIC_DRIFT" in _kinds(result)
    assert result["accepted"] is False


def test_claim_contract_and_freshness_are_enforced() -> None:
    before = {
        "claim_id": "m1",
        "meaning": {"action": "KEEP", "card": "X", "context": "VS_ROGUE"},
        "confidence": "MEDIUM",
        "patch": "36.4",
        "meta_epoch": "aug-31",
    }
    after = {**before, "meaning": {**before["meaning"], "action": "DROP"}}
    result = validate(
        "X обычно оставляют.",
        "X обычно оставляют.",
        "hearthstone",
        "constructed-guide",
        claims_before=[before],
        claims_after=[after],
        current_patch="36.5",
        current_meta_epoch="aug-31",
    )
    assert "FACTUAL_SEMANTIC_DRIFT" in _kinds(result)
    assert "STALE_EVIDENCE" in _kinds(result)


def test_rules_separate_style_memory_from_game_knowledge() -> None:
    rules = rules_for("hearthstone", "constructed-guide")
    assert rules["style_memory"]["allowed"] == "approved guides from any patch"
    assert rules["game_knowledge"]["allowed"] == "current validated evidence only"
    assert rules["corpus_version"].startswith("v")


def test_rules_carry_skeleton_and_norms_for_every_depth() -> None:
    rules = rules_for("hearthstone", "constructed-guide")
    assert [s["id"] for s in rules["sections"]][:5] == [
        "builds",
        "deckbuilding",
        "mulligan",
        "strategy",
        "matchups",
    ]
    assert rules["sections"][2]["purpose"]
    assert rules["min_words"] == 600
    assert rules["norms"]["voice_low"] == 20.6
    assert rules["norms"]["rhythm_alarm"] == 0.45
    assert rules["depth"] == "обычная"
    assert "style_examples" not in rules


def test_rewrite_rules_add_voice_examples_and_marker_phrases() -> None:
    source = (
        "Бомб Воин в Некроситете накапливает броню и замешивает бомбы в колоду противника. "
        "Мастер брони и Боевой якорррь держат стол, а Галакронд добивает медленных оппонентов."
    )
    rules = rules_for("hearthstone", "constructed-guide", text=source, depth="переплавка")
    assert rules["depth"] == "переплавка"
    assert rules["skeleton"]["sections"][2]["id"] == "mulligan"
    assert "Герой гайда" in rules["voice_signature"]
    assert 3 <= len(rules["style_examples"]) <= 5
    for example in rules["style_examples"]:
        assert 35 <= len(example["text"].split()) <= 90
        assert example["name"]
    assert rules["style_examples_source"]
    assert any(m["examples"] for m in rules["markers"]["remove"])
    assert rules["rhythm_instruction"]
    assert rules["prompt_budget"]["added_tokens_max"] == 1900


def test_rewrite_rules_fall_back_to_exemplars_without_matching_archive() -> None:
    rules = rules_for("hearthstone", "constructed-guide", text="Просто текст.", depth="переплавка")
    assert len(rules["style_examples"]) >= 3
    assert rules["style_examples_source"] in {"exemplars", "global", "archive+exemplars"}
