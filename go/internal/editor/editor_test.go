package editor

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"github.com/Manacost-Labs/EditorTeam/go/internal/analyzer"
	"github.com/Manacost-Labs/EditorTeam/go/internal/llm"
)

// фальшивый сайдкар: отдаёт заранее заданные вердикты по очереди
func fakeAnalyzer(t *testing.T, verdicts []analyzer.Verdict) *httptest.Server {
	t.Helper()
	i := 0
	return httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		switch r.URL.Path {
		case "/rules":
			_ = json.NewEncoder(w).Encode(analyzer.Rules{
				Game: "hearthstone", Profile: "constructed-guide",
				Protected: []string{"ОТК"},
				Replace:   []map[string]string{{"from": "дека", "to": "колода"}},
				Keep:      []string{"винрейт"},
				Norms:     map[string]any{"provisional": false},
			})
		case "/validate":
			v := verdicts[min(i, len(verdicts)-1)]
			i++
			_ = json.NewEncoder(w).Encode(v)
		case "/analyze":
			_ = json.NewEncoder(w).Encode(analyzer.Report{Profile: "constructed-guide"})
		default:
			w.WriteHeader(404)
		}
	}))
}

func fakeLLM(t *testing.T, replies ...string) *httptest.Server {
	t.Helper()
	i := 0
	return httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		reply := replies[min(i, len(replies)-1)]
		i++
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write([]byte(`{"choices":[{"message":{"role":"assistant","content":` +
			mustJSON(reply) + `}}]}`))
	}))
}

func mustJSON(s string) string {
	b, _ := json.Marshal(s)
	return string(b)
}

func newService(t *testing.T, anURL, llmURL string, attempts int) *Service {
	t.Helper()
	an := analyzer.New(anURL, 5*time.Second)
	lm := llm.New("openrouter", "test-model", "k", "", "", 5*time.Second)
	// клиент ходит по фиксированному адресу провайдера, поэтому в тесте
	// подменяем транспорт на фальшивый сервер
	lm.SetEndpointForTest(llmURL)
	return New(lm, an, attempts)
}

func TestAcceptedOnFirstTry(t *testing.T) {
	an := fakeAnalyzer(t, []analyzer.Verdict{{Accepted: true}})
	defer an.Close()
	lm := fakeLLM(t, "Поправленный текст.")
	defer lm.Close()

	res, err := newService(t, an.URL, lm.URL, 3).Edit(context.Background(),
		Request{Text: "Исходный текст.", Game: "hearthstone"})
	if err != nil {
		t.Fatal(err)
	}
	if !res.Accepted || res.Text != "Поправленный текст." {
		t.Fatalf("правка должна была пройти: %+v", res)
	}
	if len(res.Attempts) != 1 {
		t.Fatalf("ожидалась одна попытка, было %d", len(res.Attempts))
	}
}

func TestRetriesAfterVoiceLoss(t *testing.T) {
	an := fakeAnalyzer(t, []analyzer.Verdict{
		{Accepted: false, Violations: []analyzer.Violation{
			{Kind: "voice_lost", Signal: "императив читателю", Message: "вычищено живое"}}},
		{Accepted: true},
	})
	defer an.Close()
	lm := fakeLLM(t, "Первая, плохая.", "Вторая, хорошая.")
	defer lm.Close()

	res, err := newService(t, an.URL, lm.URL, 3).Edit(context.Background(),
		Request{Text: "Исходный.", Game: "hearthstone"})
	if err != nil {
		t.Fatal(err)
	}
	if !res.Accepted {
		t.Fatal("вторая попытка должна была пройти")
	}
	if len(res.Attempts) != 2 {
		t.Fatalf("ожидались две попытки, было %d", len(res.Attempts))
	}
	if res.Text != "Вторая, хорошая." {
		t.Fatalf("вернулся не тот текст: %q", res.Text)
	}
}

func TestReturnsOriginalWhenAllAttemptsFail(t *testing.T) {
	// Испорченный текст хуже неправленого: клиент должен получить исходник
	an := fakeAnalyzer(t, []analyzer.Verdict{{Accepted: false,
		Violations: []analyzer.Violation{{Kind: "protected_lost", Message: "пропали числа"}}}})
	defer an.Close()
	lm := fakeLLM(t, "Испорченный.")
	defer lm.Close()

	original := "Исходный текст с числом 5."
	res, err := newService(t, an.URL, lm.URL, 2).Edit(context.Background(),
		Request{Text: original, Game: "hearthstone"})
	if err != nil {
		t.Fatal(err)
	}
	if res.Accepted {
		t.Fatal("правка не должна была пройти")
	}
	if res.Text != original {
		t.Fatalf("должен вернуться исходник, вернулось: %q", res.Text)
	}
	if len(res.Attempts) != 2 {
		t.Fatalf("ожидались две попытки, было %d", len(res.Attempts))
	}
	if len(res.Caveats) == 0 {
		t.Fatal("клиенту нужно объяснить, почему текст не изменился")
	}
}

func TestProvisionalNormsSurfaceAsCaveat(t *testing.T) {
	an := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		switch r.URL.Path {
		case "/rules":
			_ = json.NewEncoder(w).Encode(analyzer.Rules{
				Game: "wow", Norms: map[string]any{"provisional": true}})
		case "/validate":
			_ = json.NewEncoder(w).Encode(analyzer.Verdict{Accepted: true})
		default:
			_ = json.NewEncoder(w).Encode(analyzer.Report{})
		}
	}))
	defer an.Close()
	lm := fakeLLM(t, "Текст.")
	defer lm.Close()

	res, _ := newService(t, an.URL, lm.URL, 1).Edit(context.Background(),
		Request{Text: "Т.", Game: "wow"})
	joined := strings.Join(res.Caveats, " ")
	if !strings.Contains(joined, "заимствован") {
		t.Fatalf("заимствованные нормы должны быть названы: %v", res.Caveats)
	}
}

func TestStripFence(t *testing.T) {
	cases := map[string]string{
		"```\nтекст\n```":         "текст",
		"```markdown\nтекст\n```": "текст",
		"обычный текст":           "обычный текст",
	}
	for in, want := range cases {
		if got := stripFence(in); got != want {
			t.Errorf("stripFence(%q) = %q, ожидалось %q", in, got, want)
		}
	}
}

func TestSystemPromptCarriesRules(t *testing.T) {
	p := buildSystemPrompt(&analyzer.Rules{
		Game:      "hearthstone",
		Protected: []string{"ОТК", "возвещение"},
		Replace:   []map[string]string{{"from": "дека", "to": "колода"}},
		Keep:      []string{"винрейт"},
		Typography: map[string]any{
			"yo":     map[string]any{"decision": "remove"},
			"quotes": map[string]any{"decision": "straight"},
		},
	}, "лёгкая")

	for _, want := range []string{"ОТК", "дека → колода", "винрейт",
		"букву ё не ставить", "кавычки прямые", "лёгкая", "Но", "хотя"} {
		if !strings.Contains(p, want) {
			t.Errorf("в запросе к модели нет %q", want)
		}
	}
}

func min(a, b int) int {
	if a < b {
		return a
	}
	return b
}
