package llm

import (
	"bufio"
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"strings"
	"time"
)

// AGUIClient calls the internal AG-UI worker. The worker owns ChatGPT
// authentication, so this path deliberately has no provider API key.
type AGUIClient struct {
	endpoint        string
	token           string
	model           string
	reasoningEffort string
	http            *http.Client
}

func NewAGUI(endpoint, token, model, reasoningEffort string, timeout time.Duration) *AGUIClient {
	return &AGUIClient{
		endpoint:        endpoint,
		token:           token,
		model:           model,
		reasoningEffort: reasoningEffort,
		http:            &http.Client{Timeout: timeout},
	}
}

func (c *AGUIClient) Model() string { return c.model }

type aguiRequest struct {
	ThreadID       string         `json:"threadId"`
	RunID          string         `json:"runId"`
	State          map[string]any `json:"state"`
	Messages       []aguiMessage  `json:"messages"`
	Tools          []any          `json:"tools"`
	Context        []any          `json:"context"`
	ForwardedProps map[string]any `json:"forwardedProps,omitempty"`
}

type aguiMessage struct {
	ID      string `json:"id"`
	Role    string `json:"role"`
	Content string `json:"content"`
}

type aguiEvent struct {
	Type      string `json:"type"`
	MessageID string `json:"messageId"`
	Role      string `json:"role"`
	Delta     string `json:"delta"`
}

// Complete sends the editor conversation to agent-codex and joins its text
// deltas. System messages remain system messages so agent-codex can promote
// the editor contract into its trusted base instructions.
func (c *AGUIClient) Complete(ctx context.Context, msgs []Message, _ int) (string, error) {
	request := aguiRequest{
		ThreadID: "editor-thread-" + fmt.Sprint(time.Now().UnixNano()),
		RunID:    "editor-run-" + fmt.Sprint(time.Now().UnixNano()),
		State:    map[string]any{},
		Messages: make([]aguiMessage, 0, len(msgs)),
		Tools:    []any{},
		Context:  []any{},
		ForwardedProps: map[string]any{
			"openbotAgentModel":           c.model,
			"openbotAgentReasoningEffort": c.reasoningEffort,
		},
	}
	for i, msg := range msgs {
		request.Messages = append(request.Messages, aguiMessage{
			ID:      fmt.Sprintf("editor-message-%d", i+1),
			Role:    msg.Role,
			Content: msg.Content,
		})
	}

	body, err := json.Marshal(request)
	if err != nil {
		return "", fmt.Errorf("сборка AG-UI запроса: %w", err)
	}
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, c.endpoint, bytes.NewReader(body))
	if err != nil {
		return "", fmt.Errorf("создание AG-UI запроса: %w", err)
	}
	req.Header.Set("Accept", "text/event-stream")
	req.Header.Set("Content-Type", "application/json")
	// agent-codex intentionally accepts only this internal header, not a generic
	// Authorization header, so the token cannot be confused with a user session.
	req.Header.Set("X-OpenBot-Agent-Token", c.token)

	resp, err := c.http.Do(req)
	if err != nil {
		return "", fmt.Errorf("запрос к AG-UI: %w", err)
	}
	defer resp.Body.Close()
	if resp.StatusCode >= 400 {
		raw, readErr := io.ReadAll(io.LimitReader(resp.Body, 2048))
		if readErr != nil {
			return "", fmt.Errorf("AG-UI вернул %d: тело не прочитано", resp.StatusCode)
		}
		return "", aguiResponseError(resp.StatusCode, raw)
	}

	text, runFailed, err := readAGUI(resp.Body)
	if err != nil {
		return "", fmt.Errorf("чтение AG-UI: %w", err)
	}
	if runFailed {
		return "", fmt.Errorf("AG-UI не смог завершить правку")
	}
	if strings.TrimSpace(text) == "" {
		return "", fmt.Errorf("AG-UI вернул пустой ответ")
	}
	return strings.TrimSpace(text), nil
}

func readAGUI(body io.Reader) (string, bool, error) {
	scanner := bufio.NewScanner(body)
	scanner.Buffer(make([]byte, 4096), 8<<20)
	var data strings.Builder
	var anonymousText strings.Builder
	var runFailed bool
	var currentMessageID string
	messageText := make(map[string]*strings.Builder)
	messageOrder := make([]string, 0)

	ensureMessage := func(messageID string) *strings.Builder {
		if existing, ok := messageText[messageID]; ok {
			return existing
		}
		var created strings.Builder
		messageText[messageID] = &created
		messageOrder = append(messageOrder, messageID)
		return &created
	}

	consume := func() error {
		if data.Len() == 0 {
			return nil
		}
		var event aguiEvent
		if err := json.Unmarshal([]byte(data.String()), &event); err != nil {
			return fmt.Errorf("неверное событие: %w", err)
		}
		switch event.Type {
		case "TEXT_MESSAGE_START":
			if event.Role == "" || event.Role == "assistant" {
				currentMessageID = event.MessageID
				if currentMessageID != "" {
					ensureMessage(currentMessageID)
				}
			}
		case "TEXT_MESSAGE_CONTENT":
			messageID := event.MessageID
			if messageID == "" {
				messageID = currentMessageID
			}
			if messageID == "" {
				anonymousText.WriteString(event.Delta)
			} else {
				ensureMessage(messageID).WriteString(event.Delta)
			}
		case "TEXT_MESSAGE_END":
			messageID := event.MessageID
			if messageID == "" {
				messageID = currentMessageID
			}
			if messageID == currentMessageID {
				currentMessageID = ""
			}
		case "RUN_ERROR":
			runFailed = true
		}
		data.Reset()
		return nil
	}

	for scanner.Scan() {
		line := scanner.Text()
		switch {
		case line == "":
			if err := consume(); err != nil {
				return "", false, err
			}
		case strings.HasPrefix(line, ":"):
			continue
		case strings.HasPrefix(line, "data:"):
			data.WriteString(strings.TrimSpace(strings.TrimPrefix(line, "data:")))
		}
	}
	if err := scanner.Err(); err != nil {
		return "", false, err
	}
	if err := consume(); err != nil {
		return "", false, err
	}
	for i := len(messageOrder) - 1; i >= 0; i-- {
		if candidate := messageText[messageOrder[i]]; candidate != nil && strings.TrimSpace(candidate.String()) != "" {
			return candidate.String(), runFailed, nil
		}
	}
	return anonymousText.String(), runFailed, nil
}

func aguiResponseError(status int, body []byte) error {
	var response struct {
		InvalidFields []string `json:"invalidFields"`
	}
	if status == http.StatusBadRequest && json.Unmarshal(body, &response) == nil {
		response.InvalidFields = safeAGUIFields(response.InvalidFields)
	}
	if len(response.InvalidFields) > 0 {
		return fmt.Errorf("AG-UI отклонил контракт запроса (HTTP %d; поля: %s)",
			status, strings.Join(response.InvalidFields, ", "))
	}
	if status == http.StatusBadRequest {
		return fmt.Errorf("AG-UI отклонил контракт запроса (HTTP %d)", status)
	}
	return fmt.Errorf("AG-UI вернул HTTP %d", status)
}

func safeAGUIFields(fields []string) []string {
	allowed := map[string]struct{}{
		"threadId": {}, "runId": {}, "parentRunId": {}, "state": {}, "messages": {},
		"tools": {}, "context": {}, "forwardedProps": {}, "resume": {},
	}
	result := make([]string, 0, len(fields))
	for _, field := range fields {
		if _, ok := allowed[field]; ok {
			result = append(result, field)
		}
	}
	return result
}
