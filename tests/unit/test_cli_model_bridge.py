"""Мост к CLI-агентам: OpenAI-совместимый контракт без настоящих CLI."""

from __future__ import annotations

import importlib.util
import json
import shlex
import sys
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "cli_model_bridge", ROOT / "tools" / "cli_model_bridge.py"
)
bridge_module = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(bridge_module)

ECHO = f"{shlex.quote(sys.executable)} -c \"import sys; print('```json\\n' + sys.stdin.read().upper() + '\\n```')\""
FAIL = f"{shlex.quote(sys.executable)} -c \"import sys; sys.stderr.write('Not logged in - Please run /login'); sys.exit(2)\""
SLOW = f"{shlex.quote(sys.executable)} -c \"import time; time.sleep(5); print('late')\""


def start(command: str, timeout: float = 30.0):
    backend = bridge_module.Backend("custom", "test-model", command, [], "")
    server = bridge_module.serve(bridge_module.Bridge(backend, timeout, 1), "127.0.0.1", 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, f"http://127.0.0.1:{server.server_address[1]}"


def post(url: str, payload: dict) -> tuple[int, dict]:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.status, json.load(response)
    except urllib.error.HTTPError as error:
        return error.code, json.load(error)


def test_bridge_returns_openai_shaped_reply_and_strips_fences() -> None:
    server, base = start(ECHO)
    try:
        code, body = post(
            f"{base}/v1/chat/completions",
            {
                "model": "ignored",
                "messages": [
                    {"role": "system", "content": "Ты critic."},
                    {"role": "user", "content": "привет"},
                ],
            },
        )
        assert code == 200, body
        content = body["choices"][0]["message"]["content"]
        assert content.startswith("[SYSTEM]\nТЫ CRITIC.")
        assert content.endswith("[USER]\nПРИВЕТ")
        assert "```" not in content
        assert body["model"] == "custom:ignored"
        assert body["choices"][0]["finish_reason"] == "stop"
        with urllib.request.urlopen(f"{base}/health", timeout=10) as response:
            health = json.load(response)
        assert health["ok"] is True and health["backend"] == "custom" and health["calls"] == 1
    finally:
        server.shutdown()


def test_bridge_folds_multi_turn_messages_into_one_prompt() -> None:
    system, prompt = bridge_module.build_prompt(
        [
            {"role": "system", "content": "правила"},
            {"role": "user", "content": '{"source": 1}'},
            {"role": "assistant", "content": "мусор"},
            {"role": "user", "content": "Ответ не разобран. Верни JSON."},
        ]
    )
    assert system == "правила"
    assert prompt.startswith('[USER]\n{"source": 1}')
    assert "[ASSISTANT]\nмусор" in prompt
    assert prompt.endswith("[ASSISTANT]")
    assert bridge_module.build_prompt([{"role": "user", "content": "один"}]) == ("", "один")


def test_bridge_reports_cli_failures_as_unavailable_not_as_text() -> None:
    server, base = start(FAIL)
    try:
        code, body = post(
            f"{base}/v1/chat/completions", {"messages": [{"role": "user", "content": "x"}]}
        )
        assert code == 503, body
        assert "не залогинен" in body["error"]["message"]
        code, body = post(f"{base}/v1/chat/completions", {"messages": []})
        assert code == 400
    finally:
        server.shutdown()


def test_bridge_times_out_with_504() -> None:
    server, base = start(SLOW, timeout=0.5)
    try:
        code, body = post(
            f"{base}/v1/chat/completions", {"messages": [{"role": "user", "content": "x"}]}
        )
        assert code == 504, body
        assert "вовремя" in body["error"]["message"]
    finally:
        server.shutdown()


def test_backend_argv_uses_documented_non_interactive_flags(tmp_path: Path) -> None:
    claude = bridge_module.Backend("claude", "sonnet", "", ["--effort", "low"], "")
    argv, stdin = claude.argv("сис", "текст", str(tmp_path), str(tmp_path / "last"))
    assert argv[:4] == ["claude", "-p", "--output-format", "text"]
    assert "--tools" in argv and argv[argv.index("--tools") + 1] == ""
    assert "--bare" in argv and "--no-session-persistence" in argv
    assert argv[argv.index("--system-prompt") + 1] == "сис"
    assert argv[argv.index("--model") + 1] == "sonnet" and argv[-2:] == ["--effort", "low"]
    assert stdin == "текст"
    codex = bridge_module.Backend("codex", "", "", [], "")
    argv, stdin = codex.argv("сис", "текст", str(tmp_path), str(tmp_path / "last"))
    assert argv[:2] == ["codex", "exec"] and argv[-1] == "-"
    for flag in ("--skip-git-repo-check", "--ephemeral", "read-only", "-o"):
        assert flag in argv
    assert stdin == "[SYSTEM]\nсис\n\n[USER]\nтекст"
    assert bridge_module.strip_fence('```json\n{"a": 1}\n```') == '{"a": 1}'
    assert bridge_module.strip_fence("  чистый ответ  ") == "чистый ответ"


def test_custom_backend_requires_command() -> None:
    with pytest.raises(SystemExit):
        bridge_module.parse_args(["--backend", "custom"])
    args = bridge_module.parse_args(
        ["--backend", "claude", "--model", "opus", "--extra-arg=--effort", "--extra-arg=high"]
    )
    assert args.extra_arg == ["--effort", "high"] and args.port == 8790
