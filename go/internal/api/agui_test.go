package api

import (
	"bytes"
	"encoding/json"
	"io"
	"log/slog"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	analyzerpkg "github.com/Manacost-Labs/EditorTeam/go/internal/analyzer"
	"github.com/Manacost-Labs/EditorTeam/go/internal/config"
	"github.com/Manacost-Labs/EditorTeam/go/internal/editor"
	llmpkg "github.com/Manacost-Labs/EditorTeam/go/internal/llm"
)

func testGatewayHandler(t *testing.T, token string) http.Handler {
	t.Helper()

	analyzerServer := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		switch r.URL.Path {
		case "/rules":
			_ = json.NewEncoder(w).Encode(analyzerpkg.Rules{
				Game: "hearthstone", Profile: "constructed-guide",
				Norms: map[string]any{"provisional": false},
			})
		case "/validate":
			_ = json.NewEncoder(w).Encode(analyzerpkg.Verdict{Accepted: true})
		case "/analyze":
			_ = json.NewEncoder(w).Encode(analyzerpkg.Report{Profile: "constructed-guide"})
		default:
			http.NotFound(w, r)
		}
	}))
	t.Cleanup(analyzerServer.Close)

	llmServer := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		_, _ = io.WriteString(w, `{"choices":[{"message":{"content":"Готовый текст."}}]}`)
	}))
	t.Cleanup(llmServer.Close)

	an := analyzerpkg.New(analyzerServer.URL, 2*time.Second)
	lm := llmpkg.New("openrouter", "test-model", "test-key", "", "", 2*time.Second)
	lm.SetEndpointForTest(llmServer.URL)

	cfg := &config.Config{
		AnalyzerURL:    analyzerServer.URL,
		AgentToken:     token,
		Provider:       "openrouter",
		Model:          "test-model",
		APIKey:         "test-key",
		MaxAttempts:    1,
		RequestTimeout: 2 * time.Second,
		MaxTextBytes:   64 * 1024,
	}
	return New(
		cfg,
		editor.New(lm, an, 1),
		an,
		slog.New(slog.NewTextHandler(io.Discard, nil)),
	).Routes()
}

func sseEvents(t *testing.T, body string) []map[string]any {
	t.Helper()
	var events []map[string]any
	for _, block := range strings.Split(body, "\n\n") {
		line := strings.TrimSpace(block)
		if !strings.HasPrefix(line, "data: ") {
			continue
		}
		var event map[string]any
		if err := json.Unmarshal([]byte(strings.TrimPrefix(line, "data: ")), &event); err != nil {
			t.Fatalf("неверное SSE-событие %q: %v", line, err)
		}
		events = append(events, event)
	}
	return events
}

func TestAGUIEditsLatestUserMessage(t *testing.T) {
	handler := testGatewayHandler(t, "")
	body, err := json.Marshal(map[string]any{
		"threadId": "thread-1",
		"runId":    "run-1",
		"messages": []any{
			map[string]any{"role": "assistant", "content": "старый ответ"},
			map[string]any{
				"role": "user",
				"content": []map[string]string{
					{"type": "text", "text": "лёгкая\nИсходный текст."},
				},
			},
		},
	})
	if err != nil {
		t.Fatal(err)
	}

	req := httptest.NewRequest(http.MethodPost, "/ag-ui", bytes.NewReader(body))
	recorder := httptest.NewRecorder()
	handler.ServeHTTP(recorder, req)

	if recorder.Code != http.StatusOK {
		t.Fatalf("статус AG-UI: %d, тело: %s", recorder.Code, recorder.Body.String())
	}
	events := sseEvents(t, recorder.Body.String())
	wantTypes := []string{
		"RUN_STARTED", "TEXT_MESSAGE_START", "TEXT_MESSAGE_CONTENT",
		"TEXT_MESSAGE_END", "RUN_FINISHED",
	}
	if len(events) != len(wantTypes) {
		t.Fatalf("события: %#v", events)
	}
	for i, want := range wantTypes {
		if events[i]["type"] != want {
			t.Fatalf("событие %d: получили %#v, ожидали %s", i, events[i]["type"], want)
		}
	}
	delta, ok := events[2]["delta"].(string)
	if !ok {
		t.Fatalf("текст ответа не строка: %#v", events[2]["delta"])
	}
	for _, want := range []string{
		"Готовый текст.",
		"Проверка редактора",
		"Изменения:",
		"Исходный текст.",
		"Готовый текст.",
		"Сохранено при правке:",
		"Сохранение:** результат возвращён в чат",
	} {
		if !strings.Contains(delta, want) {
			t.Fatalf("в отчёте нет %q: %s", want, delta)
		}
	}
}

func TestAGUIRequiresConfiguredToken(t *testing.T) {
	handler := testGatewayHandler(t, "expected")
	req := httptest.NewRequest(http.MethodPost, "/ag-ui", strings.NewReader(`{"messages":[]}`))
	recorder := httptest.NewRecorder()
	handler.ServeHTTP(recorder, req)
	if recorder.Code != http.StatusUnauthorized {
		t.Fatalf("статус без токена: %d, тело: %s", recorder.Code, recorder.Body.String())
	}

	req = httptest.NewRequest(http.MethodPost, "/ag-ui", strings.NewReader(`{"messages":[]}`))
	req.Header.Set("Authorization", "Bearer expected")
	recorder = httptest.NewRecorder()
	handler.ServeHTTP(recorder, req)
	if recorder.Code != http.StatusBadRequest {
		t.Fatalf("валидный токен должен пройти auth: %d, тело: %s", recorder.Code, recorder.Body.String())
	}
}

func TestRenderedResultExplainsRejectedEdit(t *testing.T) {
	result := &editor.Result{
		Text:     "Исходный текст.",
		Accepted: false,
		Attempts: []editor.Attempt{
			{N: 1, Violations: []analyzerpkg.Violation{
				{Kind: "rhythm_flattened", Message: "ритм выровнен"},
			}},
			{N: 2, Violations: []analyzerpkg.Violation{
				{Kind: "rhythm_flattened", Message: "ритм выровнен"},
				{Kind: "protected_lost", Message: "пропало защищённое число"},
			}},
		},
	}

	got := renderedResult(result)
	if !strings.Contains(got, "**Причины отказа:**\n> - ритм выровнен\n> - пропало защищённое число") {
		t.Fatalf("нет причин отказа: %s", got)
	}
	if strings.Count(got, "- ритм выровнен") != 1 {
		t.Fatalf("причина продублирована: %s", got)
	}
}

func TestRenderedResultKeepsAcceptedTextPrimary(t *testing.T) {
	result := &editor.Result{
		Text:       "Исправленный текст.",
		Accepted:   true,
		Attempts:   []editor.Attempt{{N: 1, Accepted: true}},
		Changes:    []editor.Change{{Kind: "changed", Line: 1, Before: "Исходный текст.", After: "Исправленный текст."}},
		Preserved:  []string{"факты и числа"},
		SaveStatus: "результат возвращён в чат; исходный текст не перезаписывался",
	}

	got := renderedResult(result)
	if !strings.HasPrefix(got, "Исправленный текст.\n\n---\n> **Проверка редактора:** правка принята") {
		t.Fatalf("исправленный текст должен быть главным ответом, а проверка — отдельной заметкой: %s", got)
	}
	if !strings.Contains(got, "> - Строка 1: «Исходный текст.» → «Исправленный текст.»") {
		t.Fatalf("прозрачный список изменений должен сохраниться: %s", got)
	}
}
