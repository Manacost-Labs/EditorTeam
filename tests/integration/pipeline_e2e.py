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
    assert result["scores_valid"] is True, result
    assert result["critic_verdict"] == "accept", result
    assert "rejection_reasons" not in result, result
    assert result["text"] == "Проверьте этот текст.", result
    assert set(result["scores"]) == {
        "factual_preservation",
        "meaning_preservation",
        "clarity",
        "structure",
        "usefulness",
        "natural_russian",
        "author_voice",
        "terminology",
    }, result
    calls = request(f"{FAKE}/calls")["calls"]
    stages = [call["stage"] for call in calls]
    # preflight → draft → postflight → critic → (repair → postflight → critic)*
    assert stages[:3] == ["analysis", "rewrite", "critic"], stages
    assert stages[-1] == "critic", stages
    repairs = stages.count("repair")
    assert 1 <= repairs <= 2, stages
    assert result["attempts"] == 1 + repairs, (result, stages)
    for index, stage in enumerate(stages):
        if stage == "repair":
            assert stages[index + 1] == "critic", stages
    first_critic = json.loads(calls[2]["user"])
    assert first_critic["source"] == "Проверьте  этот текст.", first_critic
    assert first_critic["candidate"] == "Проверьте этот текст.", first_critic
    assert "diff" in first_critic and "tool_findings" in first_critic, first_critic
    repair_call = calls[stages.index("repair")]
    assert "QA_FINDINGS" in repair_call["system"], repair_call
    repair_payload = json.loads(repair_call["user"])
    assert repair_payload["candidate"] == "Проверьте этот текст.", repair_payload
    assert repair_payload["findings"], repair_payload
    assert all(
        item["rule_id"] not in {"analyzer_unavailable", "analyzer_degraded"}
        for item in repair_payload["findings"]
    ), repair_payload

    for source in (
        "Карта стоит 3 маны.",
        "Читайте https://example.com.",
        "# Совет\n\n**Не спешите.**",
    ):
        reset()
        rejected = edit(source)
        assert rejected["accepted"] is False, rejected
        assert rejected["text"] == source, rejected
        assert "changes" not in rejected, rejected
        assert rejected["checks_complete"] is True, rejected
        assert "protected_entity_changed" in rejected["rejection_reasons"], rejected


if __name__ == "__main__":
    main()
