"""Лёгкий NLP-сайдкар для русского текста.

Razdel отвечает за границы предложений и токенов, Natasha — за морфологию и
NER, если зависимости установлены. При недоступности Natasha сервис всё равно
возвращает базовые offsets и понятную отметку деградации: Go-оркестратор не
считает такой прогон полной проверкой.
"""

from __future__ import annotations

import argparse
import bisect
import concurrent.futures
import hashlib
import json
import logging
import re
import signal
import threading
from array import array
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
        Segmenter,
    )
except Exception:  # pragma: no cover - optional fallback
    Doc = MorphVocab = NewsEmbedding = NewsMorphTagger = NewsNERTagger = NamesExtractor = None
    Segmenter = None

NLP_VERSION = "natasha-razdel-v2"
MAX_BODY = 2 * 1024 * 1024
ANALYZE_TIMEOUT = 10
MAX_CACHE = 256
MAX_WORKERS = 4
MAX_INFLIGHT = 8
_cache: dict[str, dict[str, Any]] = {}
_cache_lock = threading.Lock()
_natasha_lock = threading.Lock()
_natasha_inference_lock = threading.Lock()
_natasha: dict[str, Any] | None = None
_natasha_error: str | None = None
_executor = concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS, thread_name_prefix="nlp")
_analysis_slots = threading.BoundedSemaphore(MAX_INFLIGHT)


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
    global _natasha, _natasha_error
    if _natasha is not None:
        return _natasha
    if _natasha_error is not None:
        return None
    if not all(
        (Doc, Segmenter, MorphVocab, NewsEmbedding, NewsMorphTagger, NewsNERTagger, NamesExtractor)
    ):
        return None
    with _natasha_lock:
        if _natasha is None:
            try:
                emb = NewsEmbedding()
                morph_vocab = MorphVocab()
                _natasha = {
                    "segmenter": Segmenter(),
                    "morph_vocab": morph_vocab,
                    "morph_tagger": NewsMorphTagger(emb),
                    "ner_tagger": NewsNERTagger(emb),
                    "names": NamesExtractor(morph_vocab),
                }
            except Exception as exc:
                _natasha_error = str(exc)
                log.exception("не удалось инициализировать модели Natasha")
    return _natasha


def _disable_natasha(exc: Exception) -> None:
    global _natasha, _natasha_error
    with _natasha_lock:
        _natasha = None
        _natasha_error = str(exc)


def _cache_material(text: str, language: str, game: str, profile: str) -> bytes:
    return f"{NLP_VERSION}\0{language}\0{game}\0{profile}\0{text}".encode()


def _cache_key(text: str, language: str, game: str, profile: str) -> str:
    return hashlib.sha256(_cache_material(text, language, game, profile)).hexdigest()


def _line_col(text: str, offset: int) -> tuple[int, int]:
    before = text[:offset]
    return before.count("\n") + 1, offset - before.rfind("\n")


def _location(text: str, start: int, stop: int) -> dict[str, int]:
    line, column = _line_col(text, start)
    return {
        "offset": start,
        "length": stop - start,
        "byte_offset": len(text[:start].encode("utf-8")),
        "byte_length": len(text[start:stop].encode("utf-8")),
        "line": line,
        "column": column,
    }


def _location_index(text: str):
    """Build compact char-to-byte and newline indexes once per document."""
    byte_offsets = array("I", [0])
    total = 0
    newlines: list[int] = []
    for index, char in enumerate(text):
        total += len(char.encode("utf-8"))
        byte_offsets.append(total)
        if char == "\n":
            newlines.append(index)

    def locate(start: int, stop: int) -> dict[str, int]:
        line_index = bisect.bisect_left(newlines, start)
        previous_newline = newlines[line_index - 1] if line_index else -1
        return {
            "offset": start,
            "length": stop - start,
            "byte_offset": byte_offsets[start],
            "byte_length": byte_offsets[stop] - byte_offsets[start],
            "line": line_index + 1,
            "column": start - previous_newline,
        }

    return locate


def _paragraphs(text: str, locate=None) -> list[dict[str, Any]]:
    locate = locate or (lambda start, stop: _location(text, start, stop))
    out: list[dict[str, Any]] = []
    for match in re.finditer(r"[^\n]+(?:\n(?!\s*\n)[^\n]+)*", text):
        value = match.group(0)
        if not value.strip():
            continue
        out.append({"text": value, **locate(match.start(), match.end())})
    return out


def _basic_sentences(text: str, locate=None) -> list[dict[str, Any]]:
    locate = locate or (lambda start, stop: _location(text, start, stop))
    if sentenize:
        return [
            {
                "text": item.text,
                **locate(item.start, item.stop),
            }
            for item in sentenize(text)
        ]
    out = []
    for match in re.finditer(r"[^.!?\n]+[.!?]?(?:\s+|$)", text):
        value = match.group(0).strip()
        if value:
            start = match.start() + len(match.group(0)) - len(match.group(0).lstrip())
            out.append(
                {
                    "text": value,
                    **locate(start, start + len(value)),
                }
            )
    return out


def _basic_tokens(text: str, locate=None) -> list[dict[str, Any]]:
    locate = locate or (lambda start, stop: _location(text, start, stop))
    if tokenize:
        return [
            {
                "text": item.text,
                **locate(item.start, item.stop),
            }
            for item in tokenize(text)
        ]
    return [
        {
            "text": m.group(0),
            **locate(m.start(), m.end()),
        }
        for m in re.finditer(r"\w+|[^\w\s]", text, re.UNICODE)
    ]


def _analyze(text: str, language: str, game: str, profile: str) -> dict[str, Any]:
    locate = _location_index(text)
    sentences = _basic_sentences(text, locate)
    tokens = _basic_tokens(text, locate)
    entities: list[dict[str, Any]] = []
    models = _natasha_models()
    if models and Doc:
        try:
            with _natasha_inference_lock:
                doc = Doc(text)
                doc.segment(models["segmenter"])
                doc.tag_morph(models["morph_tagger"])
                for token in getattr(doc, "tokens", []):
                    token.lemmatize(models["morph_vocab"])
                doc.tag_ner(models["ner_tagger"])
            enriched_tokens = []
            external_offsets = {
                (token["offset"], token["offset"] + token["length"]): token for token in tokens
            }
            for token in getattr(doc, "tokens", []):
                location = external_offsets.get(
                    (token.start, token.stop), locate(token.start, token.stop)
                )
                enriched_tokens.append(
                    {
                        "text": token.text,
                        **{key: value for key, value in location.items() if key != "text"},
                        "lemma": getattr(token, "lemma", None),
                        "pos": getattr(token, "pos", None),
                        "morph": getattr(token, "feats", None) or {},
                    }
                )
            if enriched_tokens:
                tokens = enriched_tokens
            for span in doc.spans:
                if span.type:
                    entities.append(
                        {
                            "text": span.text,
                            "type": span.type,
                            **locate(span.start, span.stop),
                        }
                    )
        except Exception as exc:
            _disable_natasha(exc)
            log.exception("модель Natasha отказала во время анализа")
            models = None
    # Game terms are deliberately conservative: title-case names and known
    # Hearthstone/Warcraft/League words are candidates, not automatic facts.
    terms = []
    for m in re.finditer(r"(?<!\w)(?:[А-ЯЁ][\w-]+(?:\s+[А-ЯЁ][\w-]+){0,3})(?!\w)", text):
        if len(m.group(0)) > 2:
            terms.append(
                {
                    "text": m.group(0),
                    **locate(m.start(), m.end()),
                    "kind": "game-term",
                }
            )
    entities.extend(terms)
    findings: list[dict[str, Any]] = []
    ignored_pos = {"ADP", "AUX", "CCONJ", "DET", "PART", "PRON", "SCONJ"}
    stopwords = {
        "а",
        "без",
        "бы",
        "в",
        "во",
        "для",
        "до",
        "и",
        "из",
        "к",
        "на",
        "не",
        "но",
        "о",
        "по",
        "с",
        "у",
    }
    lemmas = [
        (token.get("lemma") or token["text"]).lower()
        for token in tokens
        if re.match(r"^[\w-]+$", token["text"], re.UNICODE)
        and token.get("pos") not in ignored_pos
        and (token.get("lemma") or token["text"]).lower() not in stopwords
    ]
    counts = Counter(lemmas)
    for token in tokens:
        value = (token.get("lemma") or token["text"]).lower()
        if len(value) >= 4 and counts[value] >= 3:
            findings.append(
                {
                    "analyzer": "natasha-razdel",
                    "rule_id": "repeat.word",
                    "severity": "warning",
                    "message": f"частый повтор слова «{token['text']}»",
                    "line": token["line"],
                    "column": token["column"],
                    "offset": token["offset"],
                    "length": token["length"],
                    "byte_offset": token["byte_offset"],
                    "byte_length": token["byte_length"],
                    "evidence": token["text"],
                    "confidence": 0.75,
                    "tags": ["repeat"],
                }
            )
            counts[value] = -10  # one signal per repeated lemma
    for item in sentences:
        words = len(re.findall(r"\w+", item["text"], re.UNICODE))
        forty = 40
        if words > forty:
            findings.append(
                {
                    "analyzer": "natasha-razdel",
                    "rule_id": "sentence.long",
                    "severity": "warning",
                    "message": f"предложение длиннее {forty} слов",
                    "line": item["line"],
                    "column": item["column"],
                    "offset": item["offset"],
                    "length": item["length"],
                    "byte_offset": item["byte_offset"],
                    "byte_length": item["byte_length"],
                    "evidence": item["text"],
                    "confidence": 0.9,
                    "tags": ["readability"],
                }
            )
    for match in re.finditer(r"(?:^|[.!?])\s+([а-яё]+)\s+([а-яё]+)(?:\s|$)", text, re.IGNORECASE):
        if match.group(1).lower() == match.group(2).lower():
            location = locate(match.start(1), match.end(1))
            findings.append(
                {
                    "analyzer": "natasha-razdel",
                    "rule_id": "sentence.boundary",
                    "severity": "info",
                    "message": "возможна неестественная граница предложения",
                    **location,
                    "evidence": match.group(0).strip(),
                    "confidence": 0.45,
                    "tags": ["structure"],
                }
            )
    return {
        "sentences": sentences,
        "tokens": tokens,
        "paragraphs": _paragraphs(text, locate),
        "entities": entities,
        "terms": terms,
        "findings": findings,
        "meta": {
            "language": language,
            "game": game,
            "profile": profile,
            "engine": "natasha+razdel" if models else "razdel-fallback",
            "complete": bool(models and sentenize and tokenize),
            "version": NLP_VERSION,
        },
    }


def _health_payload() -> dict[str, Any]:
    models = _natasha_models()
    razdel_ready = bool(sentenize and tokenize)
    if models and razdel_ready:
        status, engine, complete = "ok", "natasha", True
    elif razdel_ready:
        status, engine, complete = "degraded", "razdel-fallback", False
    else:
        status, engine, complete = "unavailable", "none", False
    return {
        "ok": True,
        "service": "natasha-razdel",
        "complete": complete,
        "natasha": {
            "status": status,
            "complete": complete,
            "engine": engine,
            "version": NLP_VERSION,
        },
    }


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
            self._json(200, _health_payload())
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
            self._json(
                413 if length > MAX_BODY else 400,
                {"error": "тело запроса пустое или слишком большое", "max_bytes": MAX_BODY},
            )
            return
        try:
            payload = json.loads(self.rfile.read(length))
            text = payload.get("text", "")
            if not isinstance(text, str) or not text.strip():
                raise ValueError("поле text пустое")
            if len(text.encode("utf-8")) > MAX_BODY:
                self._json(413, {"error": "текст слишком большой", "max_bytes": MAX_BODY})
                return
            language = payload.get("language", "ru-RU")
            game = payload.get("game", "hearthstone")
            profile = payload.get("profile", "guide")
            if not all(isinstance(value, str) for value in (language, game, profile)):
                raise ValueError("language, game и profile должны быть строками")
            key = _cache_key(text, language, game, profile)
            with _cache_lock:
                cached = _cache.get(key)
            if cached is not None:
                self._json(200, {**cached, "cached": True})
                return
            if not _analysis_slots.acquire(blocking=False):
                self._json(503, {"error": "NLP-сайдкар занят", "max_inflight": MAX_INFLIGHT})
                return
            try:
                future = _executor.submit(_analyze, text, language, game, profile)
            except Exception:
                _analysis_slots.release()
                raise
            future.add_done_callback(lambda _future: _analysis_slots.release())
            try:
                result = future.result(timeout=ANALYZE_TIMEOUT)
            except concurrent.futures.TimeoutError:
                future.cancel()
                self._json(
                    504,
                    {
                        "error": "анализ NLP-сайдкара превысил таймаут",
                        "timeout_sec": ANALYZE_TIMEOUT,
                    },
                )
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
