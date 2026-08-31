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
