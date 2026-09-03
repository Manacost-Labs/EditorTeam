"""Лёгкий NLP-сайдкар для русского текста.

Razdel отвечает за границы предложений и токенов, Natasha — за морфологию и
NER, если зависимости установлены. При недоступности Natasha сервис всё равно
возвращает базовые offsets и понятную отметку деградации: Go-оркестратор не
считает такой прогон полной проверкой.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
import logging
import re
import signal
import threading
from collections import Counter
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

try:  # optional in local development; image installs both packages
    from razdel import sentenize, tokenize
except Exception:  # pragma: no cover - exercised in minimal image only
    sentenize = tokenize = None

try:
    from natasha import (
        Doc,
        MorphVocab,
        NamesExtractor,
        NewsEmbedding,
        NewsMorphTagger,
        NewsNERTagger,
    )
except Exception:  # pragma: no cover - optional fallback
    Doc = MorphVocab = NewsEmbedding = NewsMorphTagger = NewsNERTagger = NamesExtractor = None

MAX_BODY = 2 * 1024 * 1024
ANALYZE_TIMEOUT = 10
MAX_CACHE = 256
_cache: dict[str, dict[str, Any]] = {}
_cache_lock = threading.Lock()
_natasha_lock = threading.Lock()
_natasha: dict[str, Any] | None = None
_executor = concurrent.futures.ThreadPoolExecutor(max_workers=4, thread_name_prefix="nlp")


class JSONLogFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        return json.dumps(
            {"level": record.levelname.lower(), "message": record.getMessage()},
            ensure_ascii=False,
        )


log = logging.getLogger("editorteam-nlp")
handler = logging.StreamHandler()
handler.setFormatter(JSONLogFormatter())
log.addHandler(handler)
log.setLevel(logging.INFO)


def _natasha_models() -> dict[str, Any] | None:
    global _natasha
    if _natasha is not None:
        return _natasha
    if not all((Doc, MorphVocab, NewsEmbedding, NewsMorphTagger, NewsNERTagger, NamesExtractor)):
        return None
    with _natasha_lock:
        if _natasha is None:
            emb = NewsEmbedding()
            _natasha = {
                "morph_vocab": MorphVocab(),
                "morph_tagger": NewsMorphTagger(emb),
                "ner_tagger": NewsNERTagger(emb),
                "names": NamesExtractor(MorphVocab()),
            }
    return _natasha


def _line_col(text: str, offset: int) -> tuple[int, int]:
    before = text[:offset]
    return before.count("\n") + 1, offset - before.rfind("\n")


def _paragraphs(text: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for match in re.finditer(r"[^\n]+(?:\n(?!\s*\n)[^\n]+)*", text):
        value = match.group(0)
        if not value.strip():
            continue
        line, column = _line_col(text, match.start())
        out.append({"text": value, "offset": match.start(), "line": line, "column": column})
    return out


def _basic_sentences(text: str) -> list[dict[str, Any]]:
    if sentenize:
        return [
            {
                "text": item.text,
                "offset": item.start,
                "length": item.stop - item.start,
                "line": _line_col(text, item.start)[0],
                "column": _line_col(text, item.start)[1],
            }
            for item in sentenize(text)
        ]
    out = []
    for match in re.finditer(r"[^.!?\n]+[.!?]?(?:\s+|$)", text):
        value = match.group(0).strip()
        if value:
            start = match.start() + len(match.group(0)) - len(match.group(0).lstrip())
            out.append({"text": value, "offset": start, "length": len(value), "line": _line_col(text, start)[0], "column": _line_col(text, start)[1]})
    return out


def _basic_tokens(text: str) -> list[dict[str, Any]]:
    if tokenize:
        return [
            {
                "text": item.text,
                "offset": item.start,
                "length": item.stop - item.start,
                "line": _line_col(text, item.start)[0],
                "column": _line_col(text, item.start)[1],
            }
            for item in tokenize(text)
        ]
    return [
        {"text": m.group(0), "offset": m.start(), "length": len(m.group(0)), "line": _line_col(text, m.start())[0], "column": _line_col(text, m.start())[1]}
        for m in re.finditer(r"\w+|[^\w\s]", text, re.UNICODE)
    ]


def _analyze(text: str, game: str, profile: str) -> dict[str, Any]:
    sentences = _basic_sentences(text)
    tokens = _basic_tokens(text)
    entities: list[dict[str, Any]] = []
    models = _natasha_models()
    if models and Doc:
        doc = Doc(text)
        doc.segment(sentenize(text) if sentenize else [])
        doc.tag_morph(models["morph_tagger"])
        doc.tag_ner(models["ner_tagger"])
        enriched_tokens = []
        for token in getattr(doc, "tokens", []):
            try:
                token.lemmatize(models["morph_vocab"])
            except Exception:
                pass
            line, column = _line_col(text, token.start)
            enriched_tokens.append({
                "text": token.text,
                "offset": token.start,
                "length": token.stop - token.start,
                "line": line,
                "column": column,
                "lemma": getattr(token, "lemma", None),
                "pos": getattr(token, "pos", None),
                "morph": getattr(token, "feats", None) or {},
            })
        if enriched_tokens:
            tokens = enriched_tokens
        for span in doc.spans:
            if span.type:
                line, column = _line_col(text, span.start)
                entities.append({"text": span.text, "type": span.type, "offset": span.start, "length": span.stop - span.start, "line": line, "column": column})
    # Game terms are deliberately conservative: title-case names and known
    # Hearthstone/Warcraft/League words are candidates, not automatic facts.
    terms = []
    for m in re.finditer(r"(?<!\w)(?:[А-ЯЁ][\w-]+(?:\s+[А-ЯЁ][\w-]+){0,3})(?!\w)", text):
        if len(m.group(0)) > 2:
            line, column = _line_col(text, m.start())
            terms.append({"text": m.group(0), "offset": m.start(), "length": len(m.group(0)), "line": line, "column": column, "kind": "game-term"})
    entities.extend(terms)
    findings: list[dict[str, Any]] = []
    lemmas = [t["text"].lower() for t in tokens if re.match(r"^[\w-]+$", t["text"], re.UNICODE)]
    counts = Counter(lemmas)
    for token in tokens:
        value = token["text"].lower()
        if len(value) >= 5 and counts[value] >= 4:
            findings.append({"analyzer": "natasha-razdel", "rule_id": "repeat.word", "severity": "warning", "message": f"частый повтор слова «{token['text']}»", "line": token["line"], "column": token["column"], "offset": token["offset"], "length": token["length"], "evidence": token["text"], "confidence": 0.75, "tags": ["repeat"]})
            counts[value] = -10  # one signal per repeated lemma
    for item in sentences:
        words = len(re.findall(r"\w+", item["text"], re.UNICODE))
        forty = 40
        if words > forty:
            findings.append({"analyzer": "natasha-razdel", "rule_id": "sentence.long", "severity": "warning", "message": f"предложение длиннее {forty} слов", "line": item["line"], "column": item["column"], "offset": item["offset"], "length": item["length"], "evidence": item["text"], "confidence": 0.9, "tags": ["readability"]})
    for match in re.finditer(r"(?:^|[.!?])\s+([а-яё]+)\s+([а-яё]+)(?:\s|$)", text, re.IGNORECASE):
        if match.group(1).lower() == match.group(2).lower():
            line, column = _line_col(text, match.start(1))
            findings.append({"analyzer": "natasha-razdel", "rule_id": "sentence.boundary", "severity": "info", "message": "возможна неестественная граница предложения", "line": line, "column": column, "offset": match.start(1), "length": len(match.group(0)), "evidence": match.group(0).strip(), "confidence": 0.45, "tags": ["structure"]})
    return {"sentences": sentences, "tokens": tokens, "paragraphs": _paragraphs(text), "entities": entities, "terms": terms, "findings": findings, "meta": {"game": game, "profile": profile, "engine": "natasha+razdel" if models else "razdel-fallback", "complete": bool(models and sentenize and tokenize)}}


class Handler(BaseHTTPRequestHandler):
    server_version = "EditorTeam-NLP/1.0"

    def _json(self, code: int, value: dict[str, Any]) -> None:
        raw = json.dumps(value, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/health":
            self._json(200, {"ok": True, "service": "natasha-razdel", "complete": bool(_natasha_models() and sentenize and tokenize)})
        else:
            self._json(404, {"error": "маршрут не найден"})

    def do_POST(self) -> None:  # noqa: N802
        if self.path != "/analyze":
            self._json(404, {"error": "маршрут не найден"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = 0
        if length <= 0 or length > MAX_BODY:
            self._json(413 if length > MAX_BODY else 400, {"error": "тело запроса пустое или слишком большое", "max_bytes": MAX_BODY})
            return
        try:
            payload = json.loads(self.rfile.read(length))
            text = payload.get("text", "")
            if not isinstance(text, str) or not text.strip():
                raise ValueError("поле text пустое")
            if len(text.encode("utf-8")) > MAX_BODY:
                self._json(413, {"error": "текст слишком большой", "max_bytes": MAX_BODY})
                return
            key = hashlib.sha256((payload.get("language", "ru") + "\0" + text).encode("utf-8")).hexdigest()
            with _cache_lock:
                cached = _cache.get(key)
            if cached is not None:
                self._json(200, {**cached, "cached": True})
                return
            future = _executor.submit(_analyze, text, payload.get("game", "hearthstone"), payload.get("profile", "guide"))
            try:
                result = future.result(timeout=ANALYZE_TIMEOUT)
            except concurrent.futures.TimeoutError:
                future.cancel()
                self._json(504, {"error": "анализ NLP-сайдкара превысил таймаут", "timeout_sec": ANALYZE_TIMEOUT})
                return
            with _cache_lock:
                if len(_cache) >= MAX_CACHE:
                    _cache.pop(next(iter(_cache)))
                _cache[key] = result
            self._json(200, {**result, "cached": False})
            log.info("анализ завершён")
        except (json.JSONDecodeError, ValueError) as exc:
            self._json(400, {"error": str(exc)})
        except Exception as exc:  # keep sidecar alive for one bad document
            log.exception("ошибка анализа")
            self._json(500, {"error": "ошибка NLP-сайдкара", "detail": str(exc)})

    def log_message(self, _format: str, *_args: Any) -> None:
        return


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8742)
    args = parser.parse_args()
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    server.timeout = 1
    stop = threading.Event()

    def shutdown(_signum: int, _frame: Any) -> None:
        if not stop.is_set():
            stop.set()
            threading.Thread(target=server.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)
    log.info("NLP-сайдкар запущен")
    server.serve_forever()
    server.server_close()
    _executor.shutdown(wait=False, cancel_futures=True)


if __name__ == "__main__":
    main()
