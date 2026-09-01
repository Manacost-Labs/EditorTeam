"""Примеры маркеров не должны разъезжаться с их регексами.

Модель читает фразы, детектор — регексы. Если пример перестал матчить
свой шаблон, промпт учит одному, а затвор ловит другое.
"""

import json
import re
from pathlib import Path

import pytest

ASSET = Path(".claude/skills/hs-edit/assets/markers.json")
PATTERNS = json.loads(ASSET.read_text(encoding="utf-8"))["patterns"]


@pytest.mark.parametrize("pattern", PATTERNS, ids=[p["id"] for p in PATTERNS])
def test_every_marker_has_matching_examples(pattern):
    examples = pattern.get("examples") or []
    assert len(examples) >= 1, f"у маркера {pattern['id']} нет примеров"
    rx = re.compile(pattern["re"], re.IGNORECASE | re.MULTILINE | re.UNICODE)
    for example in examples:
        assert rx.search(example), f"{pattern['id']}: пример «{example}» не матчит шаблон"


def test_examples_are_short_phrases():
    for pattern in PATTERNS:
        for example in pattern.get("examples") or []:
            assert len(example) <= 70, f"{pattern['id']}: пример слишком длинный для промпта"
