"""Модель находки: стабильная схема, различение точного и эвристики."""

import json

import pytest

from editorteam.finding import SCHEMA_VERSION, Finding, Report, exit_code


def f(**kw):
    base = dict(id="x", analyzer="cards", category="apostrophe", severity="error", message="msg")
    base.update(kw)
    return Finding(**base)


def test_severity_validated():
    with pytest.raises(ValueError):
        f(severity="катастрофа")


def test_confidence_validated():
    with pytest.raises(ValueError):
        f(confidence=1.5)


def test_json_schema_is_stable():
    r = Report(document="a.md", profile="constructed-guide")
    r.add(f(line=3, evidence="КелТузад", suggestion="Кел'Тузад"))
    r.add(
        f(
            id="y",
            severity="review",
            analyzer="soul",
            category="voice",
            message="мало императивов",
            confidence=0.5,
        )
    )
    data = json.loads(r.to_json())
    assert data["schema_version"] == SCHEMA_VERSION
    assert set(data) == {
        "schema_version",
        "document",
        "profile",
        "summary",
        "metrics",
        "findings",
        "analyzers_skipped",
        "notes",
    }
    assert data["summary"]["error"] == 1
    assert data["summary"]["review"] == 1


def test_findings_sorted_by_severity_then_line():
    r = Report(document="a.md", profile="p")
    r.add(f(id="b", severity="review", line=1))
    r.add(f(id="a", severity="error", line=9))
    order = [x["id"] for x in r.to_dict()["findings"]]
    assert order == ["a", "b"]


def test_review_does_not_fail_ci_by_default():
    r = Report(document="a.md", profile="p")
    r.add(f(id="r", severity="review"))
    assert exit_code(r) == 0


def test_error_fails_ci():
    r = Report(document="a.md", profile="p")
    r.add(f(severity="error"))
    assert exit_code(r) == 1


def test_fail_on_review_is_opt_in():
    r = Report(document="a.md", profile="p")
    r.add(f(id="r", severity="review"))
    assert exit_code(r, fail_on="review") == 1


def test_empty_report_passes():
    assert exit_code(Report(document="a.md", profile="p")) == 0


def test_skipped_analyzers_are_visible():
    r = Report(document="a.md", profile="news", skipped=["rhythm", "soul"])
    assert r.to_dict()["analyzers_skipped"] == ["rhythm", "soul"]
