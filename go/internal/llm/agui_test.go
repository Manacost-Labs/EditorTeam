package llm

import (
	"context"
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"os/exec"
	"path/filepath"
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
		_, _ = io.WriteString(w, "data: {\"type\":\"RUN_ERROR\",\"message\":\"**Внутренний отчёт** SECRET_PROMPT\"}\n\n")
	}))
	defer server.Close()

	client := NewAGUI(server.URL, "token", "gpt-5.6-luna", "xhigh", 2*time.Second)
	_, err := client.Complete(context.Background(), []Message{{Role: "user", Content: "текст"}}, 0)
	if err == nil || err.Error() != "AG-UI не смог завершить правку" {
		t.Fatalf("пользователь должен получить стабильную безопасную ошибку: %v", err)
	}
	if strings.Contains(err.Error(), "SECRET_PROMPT") || strings.Contains(err.Error(), "Внутренний отчёт") {
		t.Fatalf("внутреннее сообщение RUN_ERROR не должно попасть пользователю: %v", err)
	}
}

func TestAGUICompleteUsesFinalAssistantMessage(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "text/event-stream")
		_, _ = io.WriteString(w, "data: {\"type\":\"TEXT_MESSAGE_START\",\"messageId\":\"progress\",\"role\":\"assistant\"}\n\n")
		_, _ = io.WriteString(w, "data: {\"type\":\"TEXT_MESSAGE_CONTENT\",\"messageId\":\"progress\",\"delta\":\"Проверяю правила редактора.\"}\n\n")
		_, _ = io.WriteString(w, "data: {\"type\":\"TEXT_MESSAGE_END\",\"messageId\":\"progress\"}\n\n")
		_, _ = io.WriteString(w, "data: {\"type\":\"TEXT_MESSAGE_START\",\"messageId\":\"final\",\"role\":\"assistant\"}\n\n")
		_, _ = io.WriteString(w, "data: {\"type\":\"TEXT_MESSAGE_CONTENT\",\"messageId\":\"final\",\"delta\":\"Исправленный текст.\"}\n\n")
		// A transport may end after the final content chunk without a matching END event. The latest
		// answer is still the editor result; an older completed progress message must not win.
		_, _ = io.WriteString(w, "data: {\"type\":\"RUN_FINISHED\"}\n\n")
	}))
	defer server.Close()

	client := NewAGUI(server.URL, "token", "gpt-5.6-luna", "xhigh", 2*time.Second)
	got, err := client.Complete(context.Background(), []Message{{Role: "user", Content: "Исходный текст."}}, 0)
	if err != nil {
		t.Fatal(err)
	}
	if got != "Исправленный текст." {
		t.Fatalf("редактор должен вернуть последнюю правку, а не техническое сообщение: %q", got)
	}
}

// This opt-in integration test validates the exact JSON produced by the Go client against the
// @ag-ui/core version used by a production agent checkout. Normal unit tests never discover or
// depend on a sibling repository implicitly. Run with EDITOR_TEST_AGUI_SCHEMA_DIR=/path/to/agent.
func TestAGUIRequestMatchesProductionRunAgentInputSchema(t *testing.T) {
	agentDir := strings.TrimSpace(os.Getenv("EDITOR_TEST_AGUI_SCHEMA_DIR"))
	if agentDir == "" {
		t.Skip("set EDITOR_TEST_AGUI_SCHEMA_DIR to run the production schema integration test")
	}
	agentDir = filepath.Clean(agentDir)
	if _, err := os.Stat(filepath.Join(agentDir, "node_modules", "@ag-ui", "core")); err != nil {
		t.Skip("adjacent production @ag-ui/core is not installed")
	}
	bun, err := exec.LookPath("bun")
	if err != nil {
		t.Skip("bun is not installed")
	}

	var body []byte
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		body, _ = io.ReadAll(r.Body)
		w.Header().Set("Content-Type", "text/event-stream")
		_, _ = io.WriteString(w, "data: {\"type\":\"TEXT_MESSAGE_CONTENT\",\"delta\":\"Исправленный текст.\"}\n\n")
	}))
	defer server.Close()

	client := NewAGUI(server.URL, "token", "gpt-5.6-luna", "xhigh", 2*time.Second)
	if _, err := client.Complete(context.Background(), []Message{
		{Role: "system", Content: "Правила"},
		{Role: "user", Content: "Исходный текст."},
	}, 0); err != nil {
		t.Fatal(err)
	}

	script := `import { RunAgentInputSchema } from "@ag-ui/core";
const input = JSON.parse(await Bun.stdin.text());
const result = RunAgentInputSchema.safeParse(input);
if (!result.success) {
  console.error(JSON.stringify(result.error.issues));
  process.exit(1);
}`
	cmd := exec.Command(bun, "-e", script)
	cmd.Dir = agentDir
	cmd.Stdin = strings.NewReader(string(body))
	if output, err := cmd.CombinedOutput(); err != nil {
		t.Fatalf("исходящий запрос не соответствует production RunAgentInputSchema: %v: %s", err, output)
	}
}

func TestAGUICompleteExplainsContractFieldsWithoutRawBody(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusBadRequest)
		_, _ = io.WriteString(w, `{"error":"Invalid request body.","invalidFields":["state","context"]}`)
	}))
	defer server.Close()

	client := NewAGUI(server.URL, "token", "gpt-5.6-luna", "xhigh", 2*time.Second)
	_, err := client.Complete(context.Background(), []Message{{Role: "user", Content: "текст"}}, 0)
	if err == nil || !strings.Contains(err.Error(), "HTTP 400; поля: state, context") {
		t.Fatalf("ошибка должна назвать несовместимые поля: %v", err)
	}
	if strings.Contains(err.Error(), "Invalid request body") {
		t.Fatalf("сырой технический ответ не должен попадать пользователю: %v", err)
	}
}

func TestAGUICompleteDoesNotEchoUntrustedInvalidFieldNames(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusBadRequest)
		_, _ = io.WriteString(w, `{"error":"Invalid request body.","invalidFields":["**SECRET_FIELD**"]}`)
	}))
	defer server.Close()

	client := NewAGUI(server.URL, "token", "gpt-5.6-luna", "xhigh", 2*time.Second)
	_, err := client.Complete(context.Background(), []Message{{Role: "user", Content: "текст"}}, 0)
	if err == nil || err.Error() != "AG-UI отклонил контракт запроса (HTTP 400)" {
		t.Fatalf("неизвестные поля внешнего ответа не должны отражаться в ошибке: %v", err)
	}
}
