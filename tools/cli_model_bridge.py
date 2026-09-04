"""OpenAI-совместимый мост к CLI-агентам: Claude Code, Codex, Gemini CLI.

Поднимает локальный HTTP-сервер с `POST /v1/chat/completions`, склеивает
сообщения в один prompt, запускает выбранный CLI в неинтерактивном режиме и
возвращает его ответ как сообщение ассистента. После этого любой CLI можно
подставить и в gateway (`EDITOR_PROVIDER=openai`, `EDITOR_BASE_URL`), и в
Promptfoo (`EDITOR_EVAL_PROVIDER=openai-compatible`, `EDITOR_EVAL_BASE_URL`).

    python tools/cli_model_bridge.py --backend codex --port 8790
    python tools/cli_model_bridge.py --backend claude --model sonnet
    python tools/cli_model_bridge.py --backend custom --command 'my-cli --stdin'

Сбой CLI (не залогинен, лимит, ненулевой код возврата, пустой ответ) — это
HTTP 503, таймаут — HTTP 504: pipeline пометит их как critic_unavailable или
critic_timeout, а не как провал качества. В логах только служебные поля,
текст prompt и ответа не пишется.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

FENCE = re.compile(r"^\s*```[a-zA-Z0-9_-]*\s*\n(.*?)\n\s*```\s*$", re.S)
LOGIN_HINTS = (
    "not logged in",
    "please run /login",
    "unauthorized",
    "401",
    "login required",
    "sign in",
)
RATE_HINTS = ("rate limit", "too many requests", "429", "quota", "usage limit")


def build_prompt(messages: list[dict[str, Any]]) -> tuple[str, str]:
    """Возвращает (system, user_prompt). Несколько реплик (например, повтор
    critic с текстом ошибки разбора) складываются в один prompt с ролями."""
    system_parts: list[str] = []
    turns: list[tuple[str, str]] = []
    for message in messages:
        role = str(message.get("role", "user"))
        content = message.get("content", "")
        if isinstance(content, list):
            content = "\n".join(
                str(part.get("text", "")) for part in content if isinstance(part, dict)
            )
        content = str(content)
        if role == "system":
            system_parts.append(content)
        else:
            turns.append((role, content))
    system = "\n\n".join(part for part in system_parts if part.strip())
    if len(turns) == 1:
        return system, turns[0][1]
    lines = []
    for role, content in turns:
        lines.append(f"[{role.upper()}]\n{content}")
    lines.append("[ASSISTANT]")
    return system, "\n\n".join(lines)


def strip_fence(text: str) -> str:
    """Снимает единственное markdown-ограждение вокруг всего ответа: critic
    возвращает JSON, а агентные CLI любят оборачивать его в ```json."""
    stripped = text.strip()
    match = FENCE.match(stripped)
    return match.group(1).strip() if match else stripped


def classify_failure(output: str, code: int | None) -> str:
    lower = output.lower()
    if any(hint in lower for hint in LOGIN_HINTS):
        return "cli не залогинен: выполните вход в CLI и повторите"
    if any(hint in lower for hint in RATE_HINTS):
        return "cli упёрся в лимит запросов"
    if code is None:
        return "cli не ответил вовремя"
    return f"cli завершился с кодом {code}"


class Backend:
    """Сборка команды для конкретного CLI. Prompt всегда идёт через stdin,
    чтобы не упираться в длину аргументов и не светить текст в списке
    процессов."""

    def __init__(self, name: str, model: str, command: str, extra: list[str], workdir: str):
        self.name = name
        self.model = model
        self.command = command
        self.extra = extra
        self.workdir = workdir

    def run(self, system: str, prompt: str, timeout: float) -> tuple[str, int | None, str]:
        """Возвращает (ответ, код возврата, служебный вывод). Код None — таймаут."""
        with tempfile.TemporaryDirectory(prefix="cli-bridge-") as scratch:
            last_message = os.path.join(scratch, "last.txt")
            argv, stdin_text = self.argv(system, prompt, scratch, last_message)
            try:
                proc = subprocess.run(
                    argv,
                    input=stdin_text,
                    capture_output=True,
                    text=True,
                    timeout=timeout,
                    cwd=self.workdir or scratch,
                    check=False,
                )
            except subprocess.TimeoutExpired:
                return "", None, "timeout"
            except FileNotFoundError as error:
                return "", 127, f"команда не найдена: {error.filename}"
            reply = ""
            if self.name == "codex" and os.path.exists(last_message):
                with open(last_message, encoding="utf-8") as handle:
                    reply = handle.read()
            else:
                reply = proc.stdout
            return strip_fence(reply), proc.returncode, (proc.stderr or "")[-2000:]

    def argv(
        self, system: str, prompt: str, scratch: str, last_message: str
    ) -> tuple[list[str], str]:
        if self.name == "claude":
            argv = [
                "claude",
                "-p",
                "--output-format",
                "text",
                "--max-turns",
                "1",
                "--tools",
                "",
                "--bare",
                "--no-session-persistence",
            ]
            if system:
                argv += ["--system-prompt", system]
            if self.model:
                argv += ["--model", self.model]
            return argv + self.extra, prompt
        if self.name == "codex":
            argv = [
                "codex",
                "exec",
                "--skip-git-repo-check",
                "--ephemeral",
                "-s",
                "read-only",
                "-C",
                scratch,
                "-o",
                last_message,
            ]
            if self.model:
                argv += ["-m", self.model]
            return argv + self.extra + ["-"], join_system(system, prompt)
        if self.name == "gemini":
            # Gemini CLI: prompt через stdin, системная часть склеивается сверху.
            argv = ["gemini", "-p", ""]
            if self.model:
                argv += ["-m", self.model]
            return argv + self.extra, join_system(system, prompt)
        argv = [part.replace("{model}", self.model) for part in shlex.split(self.command)]
        return argv + self.extra, join_system(system, prompt)


def join_system(system: str, prompt: str) -> str:
    if not system:
        return prompt
    return f"[SYSTEM]\n{system}\n\n[USER]\n{prompt}"


class Bridge:
    def __init__(self, backend: Backend, timeout: float, parallel: int):
        self.backend = backend
        self.timeout = timeout
        self.gate = threading.Semaphore(max(1, parallel))
        self.calls = 0
        self.lock = threading.Lock()

    def complete(self, payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        messages = payload.get("messages")
        if not isinstance(messages, list) or not messages:
            return 400, {"error": {"message": "нужен непустой список messages"}}
        system, prompt = build_prompt(messages)
        model = str(payload.get("model") or self.backend.model or self.backend.name)
        started = time.time()
        with self.gate:
            reply, code, stderr = self.backend.run(system, prompt, self.timeout)
        duration = int((time.time() - started) * 1000)
        with self.lock:
            self.calls += 1
            call = self.calls
        ok = code == 0 and bool(reply.strip())
        log(
            backend=self.backend.name,
            model=model,
            call=call,
            duration_ms=duration,
            exit_code=code,
            prompt_chars=len(system) + len(prompt),
            reply_chars=len(reply),
            ok=ok,
        )
        if code is None:
            return 504, {
                "error": {"message": classify_failure(stderr, None), "backend": self.backend.name}
            }
        if not ok:
            detail = classify_failure(stderr + "\n" + reply, code)
            return 503, {"error": {"message": detail, "backend": self.backend.name}}
        return 200, {
            "id": f"cli-{uuid.uuid4().hex[:12]}",
            "object": "chat.completion",
            "created": int(started),
            "model": f"{self.backend.name}:{model}",
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": reply},
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_chars": len(system) + len(prompt), "completion_chars": len(reply)},
        }


def log(**fields: Any) -> None:
    sys.stderr.write(
        json.dumps({"time": time.strftime("%Y-%m-%dT%H:%M:%S"), **fields}, ensure_ascii=False)
        + "\n"
    )
    sys.stderr.flush()


def make_handler(bridge: Bridge):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_: Any) -> None:  # noqa: D401 — тексты не логируем
            return

        def _json(self, code: int, payload: Any) -> None:
            body = json.dumps(payload, ensure_ascii=False).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:  # noqa: N802
            if self.path.rstrip("/") in ("/health", "/v1/health", ""):
                self._json(
                    200,
                    {
                        "ok": True,
                        "backend": bridge.backend.name,
                        "model": bridge.backend.model,
                        "calls": bridge.calls,
                    },
                )
                return
            if self.path.rstrip("/") in ("/v1/models", "/models"):
                self._json(
                    200,
                    {
                        "object": "list",
                        "data": [
                            {"id": bridge.backend.model or bridge.backend.name, "object": "model"}
                        ],
                    },
                )
                return
            self._json(404, {"error": {"message": "нет такого пути"}})

        def do_POST(self) -> None:  # noqa: N802
            if self.path.rstrip("/") not in ("/v1/chat/completions", "/chat/completions"):
                self._json(404, {"error": {"message": "нет такого пути"}})
                return
            try:
                size = int(self.headers.get("Content-Length", "0"))
                payload = json.loads(self.rfile.read(size) or b"{}")
            except (ValueError, json.JSONDecodeError) as error:
                self._json(400, {"error": {"message": f"нужен корректный JSON: {error}"}})
                return
            code, body = bridge.complete(payload)
            self._json(code, body)

    return Handler


def serve(bridge: Bridge, host: str, port: int) -> ThreadingHTTPServer:
    server = ThreadingHTTPServer((host, port), make_handler(bridge))
    return server


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--backend", choices=["claude", "codex", "gemini", "custom"], default="codex"
    )
    parser.add_argument(
        "--model", default="", help="модель для флага CLI; пусто — значение по умолчанию CLI"
    )
    parser.add_argument(
        "--command",
        default="",
        help="для --backend custom: команда, prompt подаётся в stdin, {model} подставляется",
    )
    parser.add_argument(
        "--extra-arg",
        action="append",
        default=[],
        help="дополнительный аргумент CLI (можно повторять)",
    )
    parser.add_argument(
        "--workdir", default="", help="рабочий каталог CLI; по умолчанию пустой временный каталог"
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8790)
    parser.add_argument("--timeout", type=float, default=180.0, help="секунд на один вызов CLI")
    parser.add_argument(
        "--parallel", type=int, default=1, help="сколько вызовов CLI выполнять одновременно"
    )
    args = parser.parse_args(argv)
    if args.backend == "custom" and not args.command:
        parser.error("--backend custom требует --command")
    return args


def main() -> None:
    args = parse_args()
    backend = Backend(args.backend, args.model, args.command, args.extra_arg, args.workdir)
    bridge = Bridge(backend, args.timeout, args.parallel)
    server = serve(bridge, args.host, args.port)
    log(
        event="listening",
        backend=args.backend,
        model=args.model,
        host=args.host,
        port=args.port,
        parallel=args.parallel,
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
