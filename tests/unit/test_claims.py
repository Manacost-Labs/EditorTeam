"""Утверждения источника переживают переплавку — или об этом громко сказано."""

from pathlib import Path

import common as C

claims = C.sibling("claims")
CLEAN = Path("tests/fixtures/negative/clean-guide.md").read_text(encoding="utf-8")


def fields(items):
    return {(i["kind"], i.get("field")) for i in items}


def test_extract_finds_cards_stances_classes_and_archetype():
    src = claims.extract(CLEAN)
    names = {c["name"] for c in src["cards"]}
    assert {"Мастер брони", "Боевой якорррь"} <= names
    assert src["archetype"] == "Бомб Воин"
    assert len(src["classes"]) == 11
    stances = {(s["card"], s["stance"]) for s in src["stances"]}
    assert ("Мастер брони", "оставлять") in stances
    mulligan = [s for s in src["sections"] if s["id"] == "mulligan"]
    assert mulligan and mulligan[0]["line"] == 8


def test_single_word_card_negation_is_not_a_claim_but_anchored_one_is():
    src = claims.extract(CLEAN)
    assert all(n["anchor"] != "Галакронд" for n in src["negations"])
    anchored = claims.extract("Муллиган\nНе оставляйте Мастера брони против Мага в первые ходы.\n")
    kinds = {(n["anchor_kind"], n["anchor"]) for n in anchored["negations"]}
    assert ("card", "Мастер брони") in kinds
    assert ("class", "Маг") in kinds
    assert "оставлять" in anchored["negations"][0]["verb_lemmas"]


def test_dropped_card_is_a_violation():
    src = claims.extract(CLEAN)
    after = CLEAN.replace("Оставляйте Мастера брони против агро колод.", "")
    violations, warnings, metrics = claims.coverage(src, after)
    assert ("CLAIM_COVERAGE_LOST", "card") in fields(violations)
    assert metrics["cards_covered"] == metrics["cards_total"] - 1


def test_dropped_card_in_declared_missing_section_is_a_warning():
    source_text = (
        "Сборки\nДве сборки колоды, но одна лучше для ладдера.\n"
        "Муллиган\nОставляйте Мастера брони против агрессивных колод.\n"
    )
    src = claims.extract(source_text)
    after = "Сборки\nДве сборки колоды, но одна лучше для ладдера.\n"
    violations, warnings, _ = claims.coverage(src, after, declared_missing=["mulligan"])
    assert violations == []
    assert ("claim_coverage_review", "card") in fields(warnings)


def test_negation_flip_is_a_violation_and_paraphrase_is_not():
    source_text = "Муллиган\nНе оставляйте Мастера брони против Мага.\n"
    src = claims.extract(source_text)
    flipped = "Муллиган\nОставляйте Мастера брони против Мага.\n"
    violations, _, _ = claims.coverage(src, flipped)
    assert ("CLAIM_COVERAGE_LOST", "negation") in fields(violations)

    paraphrased = "Муллиган\nПротив Мага Мастера брони сбрасывайте.\n"
    violations, warnings, metrics = claims.coverage(src, paraphrased)
    assert ("CLAIM_COVERAGE_LOST", "negation") not in fields(violations)
    assert ("CLAIM_COVERAGE_LOST", "stance") not in fields(violations)
    assert metrics["negations_kept"] >= 1


def test_opposite_stance_is_a_violation():
    src = claims.extract("Муллиган\nОставляйте Мастера брони против агро колод.\n")
    violations, _, _ = claims.coverage(
        src, "Муллиган\nСбрасывайте Мастера брони против агро колод.\n"
    )
    assert ("CLAIM_COVERAGE_LOST", "stance") in fields(violations)


def test_lost_class_is_a_warning_and_kept_text_passes():
    src = claims.extract(CLEAN)
    violations, warnings, metrics = claims.coverage(src, CLEAN)
    assert violations == []
    assert metrics["coverage_pct"] == 100.0
    after = CLEAN.replace("Мага, Жреца, ", "")
    _, warnings, _ = claims.coverage(src, after)
    assert {("claim_coverage_review", "class")} <= fields(warnings)


def test_compare_sets_flags_invented_numbers_and_cards():
    src = claims.extract(CLEAN)
    cand = claims.extract(CLEAN + "\nГроммаш Адский Крик даёт 55% побед.\n")
    added = claims.compare_sets(src, cand)
    assert added["added_numbers"] == ["55%"]
    assert "Громмаш Адский Крик" in added["added_cards"]
    assert claims.compare_sets(src, src) == {
        "added_cards": [],
        "added_numbers": [],
        "added_classes": [],
    }
