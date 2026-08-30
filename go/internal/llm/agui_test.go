package llm

import (
	"context"
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"
)

func TestAGUICompleteCollectsTextAndForwardsRouting(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if got := r.Header.Get("X-OpenBot-Agent-Token"); got != "internal-token" {
			t.Errorf("authorization: %q", got)
		}
		var input aguiRequest
		if err := json.NewDecoder(r.Body).Decode(&input); err != nil {
			t.Errorf("request: %v", err)
		}
		if input.ForwardedProps["openbotAgentModel"] != "gpt-5.6-luna" {
			t.Errorf("model: %#v", input.ForwardedProps["openbotAgentModel"])
		}
		if input.ForwardedProps["openbotAgentReasoningEffort"] != "xhigh" {
			t.Errorf("reasoning: %#v", input.ForwardedProps["openbotAgentReasoningEffort"])
		}
		if input.State == nil {
			t.Error("AG-UI state must be present")
		}
		if input.Context == nil || len(input.Context) != 0 {
			t.Errorf("AG-UI context: %#v", input.Context)
		}
		if len(input.Messages) != 2 || input.Messages[0].Role != "system" || input.Messages[0].Content != "Правила" || input.Messages[1].Content != "Исходный текст" {
			t.Errorf("messages: %#v", input.Messages)
		}
		w.Header().Set("Content-Type", "text/event-stream")
		_, _ = io.WriteString(w, "data: {\"type\":\"RUN_STARTED\"}\n\n")
		_, _ = io.WriteString(w, "data: {\"type\":\"TEXT_MESSAGE_CONTENT\",\"delta\":\"Готовый \"}\n\n")
		_, _ = io.WriteString(w, "data: {\"type\":\"TEXT_MESSAGE_CONTENT\",\"delta\":\"текст.\"}\n\n")
		_, _ = io.WriteString(w, "data: {\"type\":\"RUN_FINISHED\"}\n\n")
	}))
	defer server.Close()

	client := NewAGUI(server.URL, "internal-token", "gpt-5.6-luna", "xhigh", 2*time.Second)
	got, err := client.Complete(context.Background(), []Message{
		{Role: "system", Content: "Правила"},
		{Role: "user", Content: "Исходный текст"},
	}, 0)
	if err != nil {
		t.Fatal(err)
	}
	if got != "Готовый текст." {
		t.Fatalf("текст: %q", got)
	}
}

func TestAGUICompleteReturnsRunError(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "text/event-stream")
		_, _ = io.WriteString(w, "data: {\"type\":\"RUN_ERROR\",\"message\":\"внутренний сбой\"}\n\n")
	}))
	defer server.Close()

	client := NewAGUI(server.URL, "token", "gpt-5.6-luna", "xhigh", 2*time.Second)
	_, err := client.Complete(context.Background(), []Message{{Role: "user", Content: "текст"}}, 0)
	if err == nil || !strings.Contains(err.Error(), "внутренний сбой") {
		t.Fatalf("ошибка: %v", err)
	}
}
