"""Exercise the real Compose gateway and every configured analyzer for free."""

from __future__ import annotations

import json
import os
import urllib.request
from typing import Any

GATEWAY = os.environ.get("EDITOR_GATEWAY_URL", "http://127.0.0.1:8740").rstrip("/")
FAKE = os.environ.get("EDITOR_FAKE_OPENAI_URL", "http://127.0.0.1:8765").rstrip("/")


def request(url: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    data = None if payload is None else json.dumps(payload, ensure_ascii=False).encode()
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="GET" if payload is None else "POST",
    )
    with urllib.request.urlopen(req, timeout=120) as response:
        assert response.status == 200, response.status
        return json.load(response)


def reset() -> None:
    request(f"{FAKE}/reset", {})


def edit(text: str) -> dict[str, Any]:
    return request(
        f"{GATEWAY}/v2/edit",
        {
            "text": text,
            "mode": "edit",
            "game": "hearthstone",
            "profile": "constructed-guide",
            "language": "ru-RU",
            "editorial_mode": "GUIDE",
        },
    )


def main() -> None:
    health = request(f"{GATEWAY}/health")
    assert health["checks_complete"] is True, health
    assert all(state == "ok" for state in health["analyzers"].values()), health
    assert {
        "native-go",
        "python",
        "natasha-razdel",
        "hunspell",
        "languagetool",
        "vale",
        "markdownlint",
    } <= set(health["analyzers"]), health

    reset()
    result = edit("Проверьте  этот текст.")
    assert result["accepted"] is True, result
    assert result["checks_complete"] is True, result
    assert result["text"] == "Проверьте этот текст.", result
    assert result["attempts"] == 2, result
    calls = request(f"{FAKE}/calls")["calls"]
    assert [call["stage"] for call in calls] == [
        "analysis",
        "rewrite",
        "critic",
        "repair",
        "critic",
    ], calls
    assert calls[2]["user"] == "Проверьте этот текст.", calls
    assert "QA_FINDINGS" in calls[3]["system"], calls
    assert calls[4]["user"] == "Проверьте этот текст.", calls

    for source in (
        "Карта стоит 3 маны.",
        "Читайте https://example.com.",
        "# Совет\n\n**Не спешите.**",
    ):
        reset()
        rejected = edit(source)
        assert rejected["accepted"] is False, rejected
        assert rejected["text"] == source, rejected
        assert rejected["checks_complete"] is True, rejected


if __name__ == "__main__":
    main()
