"""Валидация корпуса: целостность важнее удобства."""

import pytest

from editorteam import corpus


def test_corpus_is_not_empty():
    assert corpus.stats()["documents"] >= 40


def test_every_document_has_required_fields():
    missing = [p for p in corpus.validate() if p.kind == "missing-field"]
    assert missing == [], [f"{p.document}: {p.message}" for p in missing[:5]]


def test_ids_are_unique():
    assert [p for p in corpus.validate() if p.kind == "duplicate-id"] == []


def test_no_empty_files():
    assert [p for p in corpus.validate() if p.kind == "empty-file"] == []


def test_genres_match_known_profiles():
    assert [p for p in corpus.validate() if p.kind == "unknown-genre"] == []


def test_unknown_values_are_allowed_not_errors():
    """Дат и патчей в исходниках нет — unknown честнее выдуманного значения."""
    assert corpus.stats()["unknown_values"] > 0
    assert [
        p for p in corpus.validate() if "unknown" in p.message.lower() and p.severity == "error"
    ] == []


def test_extraction_artifacts_are_review_not_error():
    """Следы PDF показываются, но корпус ради них не правится."""
    arts = [p for p in corpus.validate() if p.kind == "extraction-artifact"]
    assert all(p.severity == "review" for p in arts)


def test_front_matter_is_stripped_from_body():
    for path, meta, text in corpus.documents()[:5]:
        assert meta.get("id", "").startswith("guide-")
        assert not text.lstrip().startswith("---")
        assert "extraction_source" not in text[:200]


def test_split_is_deterministic():
    a, b = corpus.split(), corpus.split()
    assert a == b


def test_split_is_disjoint_and_complete():
    parts = corpus.split()
    cal, hold = set(parts["calibration"]), set(parts["holdout"])
    assert not (cal & hold)
    assert len(cal | hold) == corpus.stats()["documents"]
    assert 0 < len(hold) < len(cal)


@pytest.mark.parametrize("field", corpus.REQUIRED_FIELDS)
def test_field_present_in_first_document(field):
    _, meta, _ = corpus.documents()[0]
    assert field in meta
