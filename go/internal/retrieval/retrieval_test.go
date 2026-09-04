package retrieval

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"github.com/Manacost-Labs/EditorTeam/go/internal/analyzer"
)

func example(id, game, profile, excerpt string, score float64) StyleExample {
	return StyleExample{ID: id, Game: game, Profile: profile, Excerpt: excerpt, Score: score}
}

func ids(items []StyleExample) string {
	out := make([]string, 0, len(items))
	for _, item := range items {
		out = append(out, item.ID)
	}
	return strings.Join(out, ",")
}

func TestSelectKeepsOnlyTheSameGame(t *testing.T) {
	query := RetrievalQuery{Text: "Оставляйте монету.", Game: "hearthstone", Profile: "constructed-guide"}
	got := Select(query, []StyleExample{
		example("wow-1", "wow", "wow-guide", "Танку важно не отрываться от группы.", 9),
		example("hs-1", "hearthstone", "constructed-guide", "Не спешите с разменом на втором ходу.", 3),
		example("hs-2", "", "constructed-guide", "Пустая игра считается Hearthstone.", 2),
	})
	if ids(got) != "hs-1,hs-2" {
		t.Fatalf("Hearthstone must never receive a WoW example: %s", ids(got))
	}
}

func TestSelectPrefersSameProfileAndNeverMixesNewsIntoGuides(t *testing.T) {
	query := RetrievalQuery{Text: "Оставляйте монету.", Game: "hearthstone", Profile: "constructed-guide"}
	got := Select(query, []StyleExample{
		example("news-1", "hearthstone", "news", "Разработчики анонсировали патч.", 9),
		example("bg-1", "hearthstone", "battlegrounds-guide", "Берите тройку, если стол пустой.", 4),
		example("guide-1", "hearthstone", "constructed-guide", "Не спешите с разменом на втором ходу.", 4),
	})
	if ids(got) != "guide-1,bg-1" {
		t.Fatalf("same profile first, same family next, news never: %s", ids(got))
	}
	news := Select(RetrievalQuery{Text: "Патч вышел.", Game: "hearthstone", Profile: "news"}, []StyleExample{
		example("news-1", "hearthstone", "news", "Разработчики анонсировали патч.", 1),
		example("guide-1", "hearthstone", "constructed-guide", "Не спешите с разменом.", 9),
	})
	if ids(news) != "news-1" {
		t.Fatalf("news profile only takes news: %s", ids(news))
	}
}

func TestSelectRespectsAuthorFilterViaSidecarAndDropsCurrentTextByHash(t *testing.T) {
	text := "Не спешите с разменом на втором ходу.\n\nОставляйте монету для ключевого хода."
	query := RetrievalQuery{Text: text, Game: "hearthstone", Profile: "constructed-guide"}
	got := Select(query, []StyleExample{
		example("same-doc", "hearthstone", "constructed-guide", strings.ToUpper(text), 9),
		example("same-para", "hearthstone", "constructed-guide", "Оставляйте монету  для ключевого хода.", 9),
		example("superset", "hearthstone", "constructed-guide", "Вступление. "+text+" Финал.", 9),
		example("other", "hearthstone", "constructed-guide", "Ранний темп важнее красивого стола.", 1),
	})
	if ids(got) != "other" {
		t.Fatalf("the edited text must be excluded by hash and containment: %s", ids(got))
	}
	if ContentHash("Маг  и Воин\n") != ContentHash("маг и воин") {
		t.Fatal("content hash must ignore case and whitespace")
	}
}

func TestSelectLimitsCountAndSize(t *testing.T) {
	long := strings.Repeat("Длинный абзац с советом. ", 80) // ~2000 runes
	var candidates []StyleExample
	for i := 0; i < 6; i++ {
		candidates = append(candidates, example("e"+string(rune('0'+i)), "hearthstone", "constructed-guide", long+string(rune('a'+i)), float64(6-i)))
	}
	got := Select(RetrievalQuery{Text: "Текст.", Game: "hearthstone", Profile: "constructed-guide"}, candidates)
	if len(got) > MaxExamples {
		t.Fatalf("more than %d examples: %d", MaxExamples, len(got))
	}
	for _, item := range got {
		if n := len([]rune(item.Excerpt)); n > MaxExcerptLen {
			t.Fatalf("excerpt %s has %d runes", item.ID, n)
		}
	}
	if TotalLen(got) > MaxTotalLen {
		t.Fatalf("total %d runes exceeds %d", TotalLen(got), MaxTotalLen)
	}
	if got := Select(RetrievalQuery{Text: "Текст.", Game: "hearthstone", Profile: "constructed-guide", Limit: 1}, candidates); len(got) != 1 {
		t.Fatalf("explicit limit: %d", len(got))
	}
}

func TestSelectReturnsEmptyWhenNothingRelevant(t *testing.T) {
	got := Select(RetrievalQuery{Text: "Текст.", Game: "league", Profile: "guide"}, []StyleExample{
		example("hs", "hearthstone", "constructed-guide", "Не спешите.", 9),
	})
	if len(got) != 0 {
		t.Fatalf("unrelated game leaked: %s", ids(got))
	}
}

func TestHTTPRetrieverUsesSidecarAndReportsUnavailable(t *testing.T) {
	var received map[string]any
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/corpus/examples" {
			t.Fatalf("path: %s", r.URL.Path)
		}
		_ = json.NewDecoder(r.Body).Decode(&received)
		_ = json.NewEncoder(w).Encode(map[string]any{"status": "ok", "examples": []StyleExample{
			example("g1", "hearthstone", "constructed-guide", "Не спешите с разменом.", 4),
			example("w1", "wow", "wow-guide", "Танку важно держать агро.", 9),
		}})
	}))
	defer server.Close()
	retriever := NewHTTP(analyzer.New(server.URL, time.Second), time.Second)
	got, err := retriever.Retrieve(context.Background(), RetrievalQuery{Text: "Оставляйте монету.", Game: "hearthstone", Profile: "constructed-guide", Author: "manacost"})
	if err != nil || ids(got) != "g1" {
		t.Fatalf("retrieve: %s %v", ids(got), err)
	}
	if received["game"] != "hearthstone" || received["author"] != "manacost" || received["exclude_hash"] != ContentHash("Оставляйте монету.") {
		t.Fatalf("sidecar query: %+v", received)
	}
	if received["text"] != "Оставляйте монету." {
		t.Fatalf("sidecar needs the text for keyword ranking: %+v", received)
	}
	server.Close()
	if _, err := retriever.Retrieve(context.Background(), RetrievalQuery{Text: "x", Game: "hearthstone"}); err == nil || !strings.Contains(err.Error(), ErrUnavailable.Error()) {
		t.Fatalf("closed sidecar must be unavailable: %v", err)
	}
	broken := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) { _, _ = w.Write([]byte("not json")) }))
	defer broken.Close()
	if _, err := NewHTTP(analyzer.New(broken.URL, time.Second), time.Second).Retrieve(context.Background(), RetrievalQuery{Text: "x"}); err == nil {
		t.Fatal("invalid JSON must be unavailable")
	}
	if _, err := (*HTTPRetriever)(nil).Retrieve(context.Background(), RetrievalQuery{}); err == nil {
		t.Fatal("nil retriever must be unavailable")
	}
}
