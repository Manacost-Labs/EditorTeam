"""Атомарное обучение corpus с явным human approval."""

from pathlib import Path

import pytest

from editorteam.corpus_learning import CorpusError, CorpusStore


def _guide(doc_id: str, genre: str = "constructed-guide", text: str | None = None) -> str:
    body = text or (
        "# Стратегия\n\n"
        f"Материал {doc_id}. "
        "Оставляйте ключевую карту в медленных матч-апах. "
        "Но не тратьте ресурс без необходимости."
    )
    return f"---\nid: {doc_id}\ntitle: Guide {doc_id}\ngenre: {genre}\n---\n{body}\n"


def _store(tmp_path: Path, count: int = 49, regression=lambda: True) -> CorpusStore:
    legacy = tmp_path / "гайды"
    legacy.mkdir()
    for index in range(count):
        (legacy / f"{index:02d}.md").write_text(_guide(f"legacy-{index}"), encoding="utf-8")
    return CorpusStore(tmp_path, regression_runner=regression, quality_runner=lambda _t, _p: [])


def test_approved_add_changes_49_to_50(tmp_path: Path) -> None:
    store = _store(tmp_path)
    new = tmp_path / "new.md"
    new.write_text(_guide("new-guide"), encoding="utf-8")

    result = store.add(
        new,
        published_at="2026-08-30",
        patch="36.4",
        author="manacost",
        tags=["standard"],
        source="published",
        genre="constructed-guide",
        approve=True,
    )

    assert result["guides"] == {"before": 49, "after": 50}
    assert result["regression"] == "PASS"
    assert "structure_changes" in result
    assert result["rule_changes"] == []
    assert store.inspect()["approved_guides"] == 50


def test_candidate_does_not_change_baseline_and_duplicate_is_rejected(tmp_path: Path) -> None:
    store = _store(tmp_path, count=2)
    new = tmp_path / "new.md"
    new.write_text(_guide("candidate"), encoding="utf-8")
    result = store.add(
        new,
        published_at="2026-08-30",
        patch="36.4",
        author="manacost",
        tags=[],
        source="published",
        genre="constructed-guide",
    )
    assert result["guide"]["status"] == "candidate"
    assert result["guides"] == {"before": 2, "after": 2}

    with pytest.raises(CorpusError) as caught:
        store.add(
            new,
            published_at="2026-08-30",
            patch="36.4",
            author="manacost",
            tags=[],
            source="published",
            genre="constructed-guide",
        )
    assert caught.value.code == "CORPUS_DUPLICATE"


def test_failed_regression_keeps_active_version_atomic(tmp_path: Path) -> None:
    store = _store(tmp_path, count=3, regression=lambda: False)
    store.ensure()
    new = tmp_path / "break.md"
    new.write_text(_guide("break"), encoding="utf-8")

    with pytest.raises(CorpusError) as caught:
        store.add(
            new,
            published_at="2026-08-30",
            patch="36.4",
            author="manacost",
            tags=[],
            source="published",
            genre="constructed-guide",
            approve=True,
        )
    assert caught.value.code == "CORPUS_REGRESSION_FAILED"
    assert store.inspect()["current_version"] == "v1"
    assert list((tmp_path / "corpus" / "guides").glob("*.md")) == []


def test_rollback_creates_new_version_without_mutating_snapshot(tmp_path: Path) -> None:
    store = _store(tmp_path, count=3)
    new = tmp_path / "new.md"
    new.write_text(_guide("approved"), encoding="utf-8")
    store.add(
        new,
        published_at="2026-08-30",
        patch="36.4",
        author="manacost",
        tags=[],
        source="published",
        genre="constructed-guide",
        approve=True,
    )
    original_snapshot = (tmp_path / "corpus" / "snapshots" / "v1.json").read_bytes()

    result = store.rollback("v1")

    assert result["corpus_version"] == "v3"
    assert store.inspect()["approved_guides"] == 3
    assert (tmp_path / "corpus" / "snapshots" / "v1.json").read_bytes() == original_snapshot


def test_small_genre_uses_global_fallback(tmp_path: Path) -> None:
    store = _store(tmp_path, count=3)
    new = tmp_path / "bg.md"
    new.write_text(_guide("bg", "battlegrounds-guide"), encoding="utf-8")
    store.add(
        new,
        published_at="2026-08-30",
        patch="36.4",
        author="manacost",
        tags=["battlegrounds"],
        source="published",
        genre="battlegrounds-guide",
        approve=True,
    )
    baseline = store._baseline(store.ensure())
    assert baseline["genres"]["battlegrounds-guide"]["fallback"] is True


def test_reject_candidate_keeps_approved_baseline(tmp_path: Path) -> None:
    store = _store(tmp_path, count=2)
    new = tmp_path / "candidate.md"
    new.write_text(_guide("candidate"), encoding="utf-8")
    added = store.add(
        new,
        published_at="2026-08-30",
        patch="36.4",
        author="manacost",
        tags=[],
        source="published",
        genre="constructed-guide",
    )

    rejected = store.reject(added["guide"]["id"])

    assert rejected["guides"] == {"before": 2, "after": 2}
    assert store.inspect()["approved_guides"] == 2
    assert store.inspect()["managed_statuses"] == {"rejected": 1}


def test_approval_requires_valid_publication_metadata(tmp_path: Path) -> None:
    store = _store(tmp_path, count=2)
    new = tmp_path / "draft.md"
    new.write_text(_guide("draft"), encoding="utf-8")

    with pytest.raises(CorpusError) as bad_date:
        store.add(
            new,
            published_at="30.08.2026",
            patch="36.4",
            author="manacost",
            tags=[],
            source="published",
            genre="constructed-guide",
            approve=True,
        )
    assert bad_date.value.code == "CORPUS_METADATA_ERROR"

    with pytest.raises(CorpusError) as draft_source:
        store.add(
            new,
            published_at="2026-08-30",
            patch="36.4",
            author="manacost",
            tags=[],
            source="draft",
            genre="constructed-guide",
            approve=True,
        )
    assert draft_source.value.code == "CORPUS_NOT_PUBLISHED"


def test_extreme_guide_produces_drift_warning_without_hiding_robust_baseline(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    new = tmp_path / "outlier.md"
    long_sentence = " ".join(["ресурс"] * 500) + "."
    new.write_text(_guide("outlier", text=long_sentence), encoding="utf-8")

    result = store.add(
        new,
        published_at="2026-08-30",
        patch="36.4",
        author="manacost",
        tags=[],
        source="published",
        genre="constructed-guide",
        approve=True,
    )

    assert any(
        warning["code"] == "CORPUS_DRIFT_WARNING" and warning["metric"] == "sentence_length"
        for warning in result["potential_drift"]
    )
    assert result["style_changes"]["sentence_length"]["delta"] == 0
