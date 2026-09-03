from server import _analyze, _basic_sentences, _basic_tokens


def test_offsets_are_exact():
    text = "Темные дары дают 42%.\n\nПоля сражений."
    sentences = _basic_sentences(text)
    tokens = _basic_tokens(text)
    assert sentences[0]["text"].startswith("Темные")
    assert text[sentences[0]["offset"] : sentences[0]["offset"] + sentences[0]["length"]].startswith("Темные")
    assert tokens[3]["text"] == "42"
    assert tokens[3]["offset"] == text.index("42")


def test_analysis_returns_structured_findings():
    result = _analyze("Карта сильная. Карта полезная. Карта нужна. Карта лучшая.", "hearthstone", "guide")
    assert {"sentences", "tokens", "paragraphs", "entities", "findings", "meta"} <= result.keys()
    assert any(item["rule_id"] == "repeat.word" for item in result["findings"])

