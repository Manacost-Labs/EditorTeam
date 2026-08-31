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

type contextualCompleter struct {
	completion llm.Completion
}

func (c contextualCompleter) Model() string { return "test-context-model" }

func (c contextualCompleter) Complete(_ context.Context, _ []llm.Message, _ int) (string, error) {
	return c.completion.Text, nil
}

func (c contextualCompleter) CompleteWithContext(_ context.Context, _ []llm.Message, _ int, _ llm.RequestContext) (llm.Completion, error) {
	return c.completion, nil
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
	if len(res.Changes) != 1 || res.Changes[0].Before != "Исходный текст." ||
		res.Changes[0].After != "Поправленный текст." {
		t.Fatalf("отчёт должен показать замену строки: %+v", res.Changes)
	}
	if res.Saved {
		t.Fatal("текст не должен считаться записанным в файл")
	}
	if !strings.Contains(res.SaveStatus, "не перезаписывался") {
		t.Fatalf("неверный статус сохранения: %q", res.SaveStatus)
	}
	if len(res.Preserved) == 0 {
		t.Fatal("отчёт должен перечислить сохранённые свойства текста")
	}
}

func TestAcceptedReviewWarningsAreSurfacedWithoutRetry(t *testing.T) {
	an := fakeAnalyzer(t, []analyzer.Verdict{{Accepted: true, Warnings: []analyzer.Violation{{
		Kind: "text_shrunk", Message: "проверьте сокращение",
	}}}})
	defer an.Close()
	lm := fakeLLM(t, "Локально исправленный текст.")
	defer lm.Close()

	res, err := newService(t, an.URL, lm.URL, 3).Edit(context.Background(),
		Request{Text: "Исходный текст.", Game: "hearthstone"})
	if err != nil {
		t.Fatal(err)
	}
	if !res.Accepted || len(res.Attempts) != 1 || len(res.Attempts[0].Warnings) != 1 {
		t.Fatalf("review warning не должен запускать повтор: %+v", res)
	}
	if !strings.Contains(strings.Join(res.Caveats, " "), "проверьте сокращение") {
		t.Fatalf("review warning должен быть виден человеку: %v", res.Caveats)
	}
}

func TestGoogleDocumentSourceIsUsedForGuardAndDiff(t *testing.T) {
	an := fakeAnalyzer(t, []analyzer.Verdict{{Accepted: true}})
	defer an.Close()

	service := New(contextualCompleter{completion: llm.Completion{
		Text:       "# Intro\n\nТекст с числом 5!",
		SourceText: "# Intro\n\nТекст с числом 5.",
	}}, analyzer.New(an.URL, 5*time.Second), 1)
	res, err := service.Edit(context.Background(), Request{
		Text:    "https://docs.google.com/document/d/doc_123/edit?pli=1",
		Game:    "hearthstone",
		Profile: "constructed-guide",
		LLMContext: llm.RequestContext{Tools: []llm.Tool{{
			Name: "mcp__google-drive__read_google_document",
		}}},
	})
	if err != nil {
		t.Fatal(err)
	}
	if !res.Accepted {
		t.Fatalf("правка документа должна пройти: %+v", res.Attempts)
	}
	// summarizeChanges reports the changed prose line; the URL must not be the
	// baseline (or its document id would become a protected number).
	if len(res.Changes) != 1 || res.Changes[0].Before != "Текст с числом 5." {
		t.Fatalf("diff должен сравнивать текст документа, а не URL: %+v", res.Changes)
	}
}

func TestGoogleDocumentReadFailureDoesNotValidateURLAsProse(t *testing.T) {
	an := fakeAnalyzer(t, []analyzer.Verdict{{Accepted: true}})
	defer an.Close()

	service := New(contextualCompleter{completion: llm.Completion{Text: "Не удалось прочитать документ."}}, analyzer.New(an.URL, 5*time.Second), 1)
	_, err := service.Edit(context.Background(), Request{
		Text:    "https://docs.google.com/document/d/doc_123/edit?pli=1",
		Game:    "hearthstone",
		Profile: "constructed-guide",
		LLMContext: llm.RequestContext{Tools: []llm.Tool{{
			Name: "mcp__google-drive__read_google_document",
		}}},
	})
	if err == nil || !strings.Contains(err.Error(), "не удалось получить содержимое Google Docs") {
		t.Fatalf("ошибка чтения должна быть контролируемой: %v", err)
	}
}

func TestRetriesAfterHardValidationFailure(t *testing.T) {
	an := fakeAnalyzer(t, []analyzer.Verdict{
		{Accepted: false, Violations: []analyzer.Violation{
			{Kind: "voice_flattened", Signal: "всего", Message: "голос статьи выровнен"}}},
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
	if len(res.Changes) != 0 {
		t.Fatalf("отклонённая правка не должна попадать в diff: %+v", res.Changes)
	}
	if res.Saved || !strings.Contains(res.SaveStatus, "не сохранена") {
		t.Fatalf("неверный статус отклонённой правки: saved=%v status=%q", res.Saved, res.SaveStatus)
	}
}

func TestSummarizeChangesHandlesAddedAndRemovedLines(t *testing.T) {
	changes := summarizeChanges("Первая строка.\nУдалить эту строку.", "Первая строка.\nДобавить эту строку.\nТретья строка.")
	if len(changes) != 2 {
		t.Fatalf("ожидались две изменения в блоке: %+v", changes)
	}
	if changes[0].Kind != "changed" || changes[0].Before != "Удалить эту строку." ||
		changes[0].After != "Добавить эту строку." {
		t.Fatalf("неверная замена в diff: %+v", changes[0])
	}
	if changes[1].Kind != "added" || changes[1].After != "Третья строка." {
		t.Fatalf("неверное добавление в diff: %+v", changes[1])
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
		"букву ё не ставить", "кавычки прямые", "лёгкая", "Но", "хотя",
		"оставить → починить локально → пересобрать", "Метрики — сигнал для проверки, не цель"} {
		if !strings.Contains(p, want) {
			t.Errorf("в запросе к модели нет %q", want)
		}
	}
}

func TestClaimContractKeepsMeaningButHidesSources(t *testing.T) {
	claims := safeClaims([]map[string]any{{
		"claim_id":   "m1",
		"meaning":    map[string]any{"action": "KEEP", "card": "X", "context": "VS_ROGUE"},
		"confidence": "LOW",
		"patch":      "36.4",
		"meta_epoch": "aug-31",
		"evidence":   map[string]any{"replays": 184, "source": "HSGuru"},
	}})
	raw, _ := json.Marshal(claims)
	got := string(raw)
	for _, want := range []string{"m1", "KEEP", "LOW", "36.4", "aug-31"} {
		if !strings.Contains(got, want) {
			t.Fatalf("в claim contract нет %q: %s", want, got)
		}
	}
	for _, hidden := range []string{"replays", "HSGuru", "source"} {
		if strings.Contains(got, hidden) {
			t.Fatalf("backstage evidence просочилось в prompt: %s", got)
		}
	}
}

func min(a, b int) int {
	if a < b {
		return a
	}
	return b
}
