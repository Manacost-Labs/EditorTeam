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
	Type    string `json:"type"`
	Delta   string `json:"delta"`
	Message string `json:"message"`
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
		return "", fmt.Errorf("AG-UI вернул %d: %s", resp.StatusCode, truncate(string(raw), 200))
	}

	text, runError, err := readAGUI(resp.Body)
	if err != nil {
		return "", fmt.Errorf("чтение AG-UI: %w", err)
	}
	if runError != "" {
		return "", fmt.Errorf("AG-UI: %s", truncate(runError, 300))
	}
	if strings.TrimSpace(text) == "" {
		return "", fmt.Errorf("AG-UI вернул пустой ответ")
	}
	return strings.TrimSpace(text), nil
}

func readAGUI(body io.Reader) (string, string, error) {
	scanner := bufio.NewScanner(body)
	scanner.Buffer(make([]byte, 4096), 8<<20)
	var data strings.Builder
	var text strings.Builder
	var runError string

	consume := func() error {
		if data.Len() == 0 {
			return nil
		}
		var event aguiEvent
		if err := json.Unmarshal([]byte(data.String()), &event); err != nil {
			return fmt.Errorf("неверное событие: %w", err)
		}
		switch event.Type {
		case "TEXT_MESSAGE_CONTENT":
			text.WriteString(event.Delta)
		case "RUN_ERROR":
			runError = event.Message
		}
		data.Reset()
		return nil
	}

	for scanner.Scan() {
		line := scanner.Text()
		switch {
		case line == "":
			if err := consume(); err != nil {
				return "", "", err
			}
		case strings.HasPrefix(line, ":"):
			continue
		case strings.HasPrefix(line, "data:"):
			data.WriteString(strings.TrimSpace(strings.TrimPrefix(line, "data:")))
		}
	}
	if err := scanner.Err(); err != nil {
		return "", "", err
	}
	if err := consume(); err != nil {
		return "", "", err
	}
	return text.String(), runError, nil
}
