import json
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from http.server import ThreadingHTTPServer

import server


@contextmanager
def running_sidecar():
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{httpd.server_port}"
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=2)


def post_json(base_url, payload):
    request = urllib.request.Request(
        f"{base_url}/analyze",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=2) as response:
        return response.status, json.load(response)


def test_offsets_are_exact():
    text = "Темные дары дают 42%.\n\nПоля сражений."
    sentences = server._basic_sentences(text)
    tokens = server._basic_tokens(text)
    assert sentences[0]["text"].startswith("Темные")
    assert text[
        sentences[0]["offset"] : sentences[0]["offset"] + sentences[0]["length"]
    ].startswith("Темные")
    assert tokens[3]["text"] == "42"
    assert tokens[3]["offset"] == text.index("42")


def test_utf8_byte_offsets_and_character_line_columns_are_exact():
    text = "🙂 Игрок\nусилил существо."
    tokens = server._basic_tokens(text)
    player = next(token for token in tokens if token["text"] == "Игрок")
    strengthened = next(token for token in tokens if token["text"] == "усилил")

    assert player["byte_offset"] == len("🙂 ".encode("utf-8"))
    assert player["byte_length"] == len("Игрок".encode("utf-8"))
    assert (player["line"], player["column"]) == (1, 3)
    assert strengthened["byte_offset"] == len("🙂 Игрок\n".encode("utf-8"))
    assert (strengthened["line"], strengthened["column"]) == (2, 1)


def test_linear_location_index_matches_public_offset_contract():
    text = "🙂 Игрок\nусилил существо."
    locate = server._location_index(text)
    for start, stop in ((0, 1), (2, 7), (8, 14), (15, len(text))):
        assert locate(start, stop) == server._location(text, start, stop)


def test_analyze_uses_one_document_location_index(monkeypatch):
    monkeypatch.setattr(server, "_location", lambda *_args: (_ for _ in ()).throw(AssertionError()))
    result = server._analyze(
        "# Совет 🙂\n\nИгрок усилил существо.", "ru-RU", "hearthstone", "guide"
    )
    assert result["tokens"]


def test_analysis_returns_structured_findings():
    result = server._analyze(
        "Карта сильная. Карта полезная. Карта нужна. Карта лучшая.",
        "ru-RU",
        "hearthstone",
        "guide",
    )
    assert {"sentences", "tokens", "paragraphs", "entities", "findings", "meta"} <= result.keys()
    assert any(item["rule_id"] == "repeat.word" for item in result["findings"])


def test_natasha_models_create_one_segmenter(monkeypatch):
    created = []

    class Segmenter:
        def __init__(self):
            created.append(self)

    class Model:
        def __init__(self, *_args):
            pass

    monkeypatch.setattr(server, "Segmenter", Segmenter, raising=False)
    for name in (
        "Doc",
        "MorphVocab",
        "NewsEmbedding",
        "NewsMorphTagger",
        "NewsNERTagger",
        "NamesExtractor",
    ):
        monkeypatch.setattr(server, name, Model)
    monkeypatch.setattr(server, "_natasha", None)

    first = server._natasha_models()
    second = server._natasha_models()

    assert first is second
    assert first["segmenter"] is created[0]
    assert len(created) == 1


def test_analyze_passes_the_initialized_segmenter_to_doc(monkeypatch):
    segmenter = object()

    class Doc:
        spans = []
        tokens = []

        def __init__(self, _text):
            pass

        def segment(self, value):
            assert value is segmenter

        def tag_morph(self, _tagger):
            pass

        def tag_ner(self, _tagger):
            pass

    monkeypatch.setattr(server, "Doc", Doc)
    monkeypatch.setattr(
        server,
        "_natasha",
        {
            "segmenter": segmenter,
            "morph_vocab": object(),
            "morph_tagger": object(),
            "ner_tagger": object(),
        },
    )
    result = server._analyze("Текст.", "ru-RU", "hearthstone", "guide")
    assert result["meta"]["complete"] is bool(server.sentenize and server.tokenize)


def test_cache_key_includes_pipeline_and_all_request_context():
    base = server._cache_key("Текст", "ru-RU", "hearthstone", "guide")
    assert base != server._cache_key("Другой текст", "ru-RU", "hearthstone", "guide")
    assert base != server._cache_key("Текст", "en-US", "hearthstone", "guide")
    assert base != server._cache_key("Текст", "ru-RU", "wow", "guide")
    assert base != server._cache_key("Текст", "ru-RU", "hearthstone", "news")
    assert server.NLP_VERSION.encode() in server._cache_material(
        "Текст", "ru-RU", "hearthstone", "guide"
    )


def test_health_has_explicit_natasha_state(monkeypatch):
    monkeypatch.setattr(server, "_natasha", {"segmenter": object()})
    monkeypatch.setattr(server, "sentenize", lambda _text: [])
    monkeypatch.setattr(server, "tokenize", lambda _text: [])
    health = server._health_payload()
    assert health["natasha"] == {
        "status": "ok",
        "complete": True,
        "engine": "natasha",
        "version": server.NLP_VERSION,
    }


def test_fallback_razdel_is_explicitly_degraded(monkeypatch):
    monkeypatch.setattr(server, "_natasha", None)
    monkeypatch.setattr(server, "_natasha_error", "Natasha unavailable")
    result = server._analyze("Игрок усилил существо.", "ru-RU", "hearthstone", "guide")
    health = server._health_payload()
    assert result["meta"]["engine"] == "razdel-fallback"
    assert result["meta"]["complete"] is False
    expected = "degraded" if server.sentenize and server.tokenize else "unavailable"
    assert health["natasha"]["status"] == expected
    assert health["natasha"]["complete"] is False


def test_broken_natasha_model_falls_back_without_crashing(monkeypatch):
    class BrokenEmbedding:
        def __init__(self):
            raise RuntimeError("broken model")

    monkeypatch.setattr(server, "NewsEmbedding", BrokenEmbedding)
    monkeypatch.setattr(server, "_natasha", None)
    monkeypatch.setattr(server, "_natasha_error", None)
    result = server._analyze("Игрок усилил существо.", "ru-RU", "hearthstone", "guide")
    assert result["meta"]["complete"] is False
    assert result["meta"]["engine"] == "razdel-fallback"


def test_invalid_json_returns_400():
    with running_sidecar() as base_url:
        request = urllib.request.Request(
            f"{base_url}/analyze",
            data=b"{",
            headers={"Content-Type": "application/json"},
        )
        try:
            urllib.request.urlopen(request, timeout=2)
        except urllib.error.HTTPError as error:
            assert error.code == 400
        else:
            raise AssertionError("malformed JSON must fail")


def test_different_game_and_profile_do_not_share_cache(monkeypatch):
    calls = []

    def analyze(text, language, game, profile):
        calls.append((language, game, profile))
        return {"sentences": [], "tokens": [], "meta": {"complete": True}}

    monkeypatch.setattr(server, "_analyze", analyze)
    server._cache.clear()
    with running_sidecar() as base_url:
        for game, profile in (("hearthstone", "guide"), ("wow", "guide"), ("wow", "news")):
            status, body = post_json(
                base_url,
                {"text": "Текст", "language": "ru-RU", "game": game, "profile": profile},
            )
            assert status == 200
            assert body["cached"] is False
    assert calls == [
        ("ru-RU", "hearthstone", "guide"),
        ("ru-RU", "wow", "guide"),
        ("ru-RU", "wow", "news"),
    ]


def test_analysis_timeout_returns_504(monkeypatch):
    def slow_analyze(*_args):
        time.sleep(0.1)
        return {"meta": {"complete": True}}

    monkeypatch.setattr(server, "_analyze", slow_analyze)
    monkeypatch.setattr(server, "ANALYZE_TIMEOUT", 0.01)
    server._cache.clear()
    with running_sidecar() as base_url:
        try:
            post_json(base_url, {"text": "Медленный текст."})
        except urllib.error.HTTPError as error:
            assert error.code == 504
        else:
            raise AssertionError("slow analysis must time out")


def test_busy_sidecar_rejects_new_work_instead_of_queueing():
    acquired = [server._analysis_slots.acquire(blocking=False) for _ in range(server.MAX_INFLIGHT)]
    assert all(acquired)
    try:
        with running_sidecar() as base_url:
            try:
                post_json(base_url, {"text": "Текст без места в очереди."})
            except urllib.error.HTTPError as error:
                assert error.code == 503
            else:
                raise AssertionError("busy sidecar must reject unbounded queued work")
    finally:
        for _ in acquired:
            server._analysis_slots.release()


def test_parallel_requests_are_safe(monkeypatch):
    def analyze(text, language, game, profile):
        return {
            "sentences": [{"text": text}],
            "tokens": [],
            "meta": {
                "language": language,
                "game": game,
                "profile": profile,
                "complete": True,
            },
        }

    monkeypatch.setattr(server, "_analyze", analyze)
    server._cache.clear()
    with running_sidecar() as base_url, ThreadPoolExecutor(max_workers=8) as pool:
        futures = [
            pool.submit(
                post_json,
                base_url,
                {
                    "text": f"Текст {index} 🙂",
                    "language": "ru-RU",
                    "game": "hearthstone",
                    "profile": "guide",
                },
            )
            for index in range(8)
        ]
        results = [future.result() for future in futures]
    assert all(status == 200 for status, _body in results)
    assert {body["sentences"][0]["text"] for _status, body in results} == {
        f"Текст {index} 🙂" for index in range(8)
    }
