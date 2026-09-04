"""Deterministic OpenAI-compatible test server for the Compose E2E gate."""

from __future__ import annotations

import argparse
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

CALLS: list[dict[str, Any]] = []
LOCK = threading.Lock()


def _stage(system: str) -> str:
    if "QA_FINDINGS" in system:
        return "repair"
    if "Ты critic" in system:
        return "critic"
    if "Проанализируй текст" in system:
        return "analysis"
    return "rewrite"


def _scores(value: int = 8) -> dict[str, int]:
    return {
        "factual_preservation": value,
        "meaning_preservation": value,
        "clarity": value,
        "structure": value,
        "usefulness": value,
        "natural_russian": value,
        "author_voice": value,
        "terminology": value,
    }


def _payload(user: str) -> dict[str, Any]:
    """Critic and repair receive JSON data in the user message."""
    try:
        parsed = json.loads(user)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _reply(stage: str, user: str) -> str:
    if stage == "analysis":
        return json.dumps(
            {
                "thesis": "проверка",
                "audience": "игрок",
                "genre": "гайд",
                "paragraphs": [],
                "weak_spots": [],
                "repetitions": [],
                "unclear": [],
                "template_phrases": [],
                "missing_links": [],
                "factual_risks": [],
            },
            ensure_ascii=False,
        )
    if stage == "rewrite":
        if "Карта стоит 3 маны" in user:
            return "Карта стоит 4 маны."
        if "https://example.com" in user:
            return "Читайте https://evil.example."
        if "# Совет" in user:
            return "Совет\n\nНе спешите."
        return "Проверьте этот текст."
    if stage == "repair":
        return "Проверьте этот текст."
    candidate = _payload(user).get("candidate")
    if candidate == "Проверьте этот текст.":
        with LOCK:
            repairs = sum(call["stage"] == "repair" for call in CALLS)
        findings = (
            []
            if repairs
            else [{"rule_id": "clarity", "severity": "warning", "message": "уточнить", "line": 1}]
        )
        return json.dumps(
            {
                "verdict": "repair" if findings else "accept",
                "scores": _scores(),
                "regressions": [],
                "findings": findings,
                "repair_required": bool(findings),
            },
            ensure_ascii=False,
        )
    return json.dumps(
        {
            "verdict": "accept",
            "scores": _scores(),
            "regressions": [],
            "findings": [],
            "repair_required": False,
        },
        ensure_ascii=False,
    )


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_: Any) -> None:
        return

    def _json(self, code: int, payload: Any) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/health":
            self._json(200, {"ok": True})
            return
        if self.path == "/calls":
            with LOCK:
                self._json(200, {"calls": list(CALLS)})
            return
        self._json(404, {"error": "not found"})

    def do_POST(self) -> None:  # noqa: N802
        if self.path == "/reset":
            with LOCK:
                CALLS.clear()
            self._json(200, {"ok": True})
            return
        if self.path != "/v1/chat/completions":
            self._json(404, {"error": "not found"})
            return
        try:
            size = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(size))
            messages = payload["messages"]
            system = str(messages[0]["content"])
            user = str(messages[-1]["content"])
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
            self._json(400, {"error": {"message": str(error)}})
            return
        stage = _stage(system)
        call = {"stage": stage, "system": system, "user": user, "model": payload.get("model")}
        with LOCK:
            CALLS.append(call)
        content = _reply(stage, user)
        self._json(200, {"choices": [{"message": {"role": "assistant", "content": content}}]})


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    ThreadingHTTPServer((args.host, args.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
