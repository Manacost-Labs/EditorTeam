"""Black-box integration checks for the real Natasha/Razdel HTTP sidecar."""

from __future__ import annotations

import json
import os
import urllib.request
from typing import Any

BASE = os.environ.get("NATASHA_TEST_URL", "http://127.0.0.1:8742").rstrip("/")


def request(path: str, payload: dict[str, Any] | None = None) -> tuple[int, dict[str, Any]]:
    data = None if payload is None else json.dumps(payload, ensure_ascii=False).encode()
    req = urllib.request.Request(
        f"{BASE}{path}",
        data=data,
        headers={"Content-Type": "application/json"},
        method="GET" if payload is None else "POST",
    )
    with urllib.request.urlopen(req, timeout=30) as response:
        return response.status, json.load(response)


def assert_location(text: str, item: dict[str, Any]) -> None:
    start = item["offset"]
    stop = start + item["length"]
    assert text[start:stop] == item["text"], item
    encoded = text.encode()
    byte_start = item["byte_offset"]
    byte_stop = byte_start + item["byte_length"]
    assert encoded[byte_start:byte_stop].decode() == item["text"], item
    prefix = text[:start]
    assert item["line"] == prefix.count("\n") + 1, item
    assert item["column"] == start - prefix.rfind("\n"), item


def main() -> None:
    status, health = request("/health")
    assert status == 200, health
    assert health["natasha"]["status"] == "ok", health
    assert health["natasha"]["complete"] is True, health
    assert health["natasha"]["engine"] == "natasha", health
    assert health["natasha"]["version"], health

    text = "Игроки покупают существ. Игрок купил существо, а затем усилил существ на столе."
    status, result = request(
        "/analyze",
        {
            "text": text,
            "language": "ru-RU",
            "game": "hearthstone",
            "profile": "battlegrounds-guide",
        },
    )
    assert status == 200, result
    assert result["meta"]["complete"] is True, result
    assert result["sentences"] and result["tokens"], result
    for token in result["tokens"]:
        assert token.get("lemma"), token
        assert token.get("pos"), token
        assert_location(text, token)
    creature_tokens = [token for token in result["tokens"] if token["lemma"] == "существо"]
    assert len(creature_tokens) == 3, creature_tokens
    repeats = [finding for finding in result["findings"] if finding["rule_id"] == "repeat.word"]
    assert len(repeats) == 1, repeats
    assert repeats[0]["evidence"].lower().startswith("существ"), repeats
    assert not any(
        finding.get("evidence", "").lower() in {"игрок", "а", "на"} for finding in repeats
    )
    assert isinstance(result["entities"], list), result

    markdown = "# Совет 🙂\n\nИгрок усилил существо."
    status, result = request(
        "/analyze",
        {"text": markdown, "language": "ru-RU", "game": "hearthstone", "profile": "guide"},
    )
    assert status == 200, result
    assert result["meta"]["complete"] is True, result
    for token in result["tokens"]:
        assert_location(markdown, token)


if __name__ == "__main__":
    main()
