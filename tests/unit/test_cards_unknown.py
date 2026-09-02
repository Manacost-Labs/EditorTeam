"""Неизвестные карты: имя, которого нет в справочнике, — адрес, а не ошибка."""

import json

import common as C
import pytest

cards = C.sibling("cards")
claims = C.sibling("claims")


@pytest.fixture(scope="module")
def env():
    if not cards.ASSET.exists():
        pytest.skip("нет справочника карт")
    idx = cards.Index(json.loads(cards.ASSET.read_text(encoding="utf-8"))["карты"], C.morph())
    return idx, cards.corpus_common(idx), cards.corpus_proper(idx)


def test_new_card_is_reported_and_known_cards_are_not(env):
    idx, common, proper = env
    text = (
        "Разбойник получил Главного канонира и Притягивающий крюк, а Чернокнижник "
        "разогнался за счет Крестного отца Казакуса. Оставляйте Мастера брони против агро."
    )
    found = cards.unknown_names(text, idx, common, proper)
    assert "Притягивающий крюк" in found
    assert "Крестного отца Казакуса" in found
    assert not any("канонира" in k or "брони" in k for k in found)


def test_sentence_starts_classes_ranks_and_archetypes_are_not_cards(env):
    idx, common, proper = env
    text = (
        "Друид держит темп на Алмазе и в Легенде. Бомб Воин и Токен Друид снова в мете. "
        "Стандартный формат живет по своим правилам. Оставляйте ОТК на потом."
    )
    assert cards.unknown_names(text, idx, common, proper) == {}


def test_claims_extract_keeps_unknown_cards_as_review_claims(env):
    src = claims.extract(
        "Муллиган\nОставляйте Притягивающий крюк против агро и Мастера брони тоже."
    )
    by_name = {c["name"]: c for c in src["cards"]}
    assert by_name["Притягивающий крюк"]["source"] == "unknown"
    assert by_name["Мастер брони"]["source"] == "db"
    violations, warnings, _ = claims.coverage(
        src, "Муллиган\nОставляйте Мастера брони против агро."
    )
    assert not [v for v in violations if v["field"] == "card"]
    lost = [w for w in warnings if w["field"] == "card" and w["claim"] == "Притягивающий крюк"]
    assert lost and "нет в справочнике" in lost[0]["message"]
