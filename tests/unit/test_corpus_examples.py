"""Retrieval примеров авторского стиля через существующий индекс корпуса."""

from __future__ import annotations

from pathlib import Path

import pytest

from editorteam import server

ROOT = Path(__file__).resolve().parents[2]
GUIDES = sorted((ROOT / "гайды").glob("*.md"))

QUERY = (
    "В медленных поединках куда важнее станут источники добора, а также крупные угрозы. "
    "Чем больше тяжелых угроз будет у Воина, тем лучше, но нужно постоянно следить за пулом "
    "существ, которых воскрешают легендарки. Медленные матч-апы во многом определяются "
    "зачистками противника, исцелением и добором карт."
)


@pytest.fixture(scope="module")
def has_corpus() -> None:
    if not GUIDES:
        pytest.skip("корпус гайды/ отсутствует")


def test_examples_come_from_the_same_game_and_profile_family(has_corpus: None) -> None:
    result = server.corpus_examples(QUERY, "hearthstone", "constructed-guide")
    assert result["status"] == "ok"
    examples = result["examples"]
    assert 1 <= len(examples) <= server.RETRIEVAL_MAX
    total = 0
    for item in examples:
        assert item["game"] == "hearthstone"
        assert server.GENRE_FAMILIES.get(item["profile"]) == "guide"
        assert 0 < len(item["excerpt"]) <= server.RETRIEVAL_EXCERPT_MAX
        assert item["why_relevant"]
        assert isinstance(item["voice_features"], list)
        assert "#" in item["id"]
        total += len(item["excerpt"])
    assert total <= server.RETRIEVAL_TOTAL_MAX
    assert len({item["excerpt"] for item in examples}) == len(examples)


def test_other_game_and_news_profile_get_nothing(has_corpus: None) -> None:
    assert server.corpus_examples(QUERY, "wow", "wow-guide")["examples"] == []
    assert server.corpus_examples(QUERY, "hearthstone", "news")["examples"] == []
    assert server.corpus_examples("", "hearthstone", "constructed-guide")["examples"] == []


def test_edited_text_is_excluded_by_hash_and_containment(has_corpus: None) -> None:
    body = server._scripts().body(GUIDES[28])
    paragraphs = server._scripts().paragraphs(body, min_words=35)
    assert paragraphs
    own = paragraphs[len(paragraphs) // 2]
    result = server.corpus_examples(own, "hearthstone", "constructed-guide")
    own_norm = server._content_norm(own)
    for item in result["examples"]:
        assert server._content_norm(item["excerpt"]) != own_norm
        assert server._content_norm(item["excerpt"]) not in own_norm
    excluded = server.corpus_examples(
        QUERY, "hearthstone", "constructed-guide", exclude_hash=server._content_hash(own)
    )
    assert all(server._content_norm(item["excerpt"]) != own_norm for item in excluded["examples"])


def test_author_filter_and_limit(has_corpus: None) -> None:
    assert (
        server.corpus_examples(QUERY, "hearthstone", "constructed-guide", author="кто-то")[
            "examples"
        ]
        == []
    )
    assert (
        len(server.corpus_examples(QUERY, "hearthstone", "constructed-guide", limit=1)["examples"])
        <= 1
    )


def test_author_is_returned_and_matched_ignoring_case(has_corpus: None) -> None:
    same = server.corpus_examples(QUERY, "hearthstone", "constructed-guide", author=" Manacost ")
    assert same["examples"]
    assert all(item["author"] == "manacost" for item in same["examples"])
    plain = server.corpus_examples(QUERY, "hearthstone", "constructed-guide")["examples"]
    assert plain and all("author" in item for item in plain)
