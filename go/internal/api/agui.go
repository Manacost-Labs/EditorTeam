package api

import (
	"context"
	"crypto/subtle"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"strings"
	"time"

	"github.com/Manacost-Labs/EditorTeam/go/internal/editor"
)

// RunInput is the small part of RunAgentInput the editor needs. The remaining AG-UI fields are
// deliberately ignored: the editor is a text-in/text-out worker and must not treat a forwarded
// tool, state or arbitrary field as an instruction.
type runInput struct {
	ThreadID string       `json:"threadId"`
	RunID    string       `json:"runId"`
	Messages []runMessage `json:"messages"`
}

type runMessage struct {
	Role    string          `json:"role"`
	Content json.RawMessage `json:"content"`
}

type textPart struct {
	Type string `json:"type"`
	Text string `json:"text"`
}

func (s *Server) agui(w http.ResponseWriter, r *http.Request) {
	if s.cfg.AgentToken != "" && !bearerMatches(r.Header.Get("Authorization"), s.cfg.AgentToken) {
		writeErr(w, http.StatusUnauthorized, "требуется токен редактора")
		return
	}

	var input runInput
	if !s.decode(w, r, &input) {
		return
	}

	text, ok := latestUserText(input.Messages)
	if !ok {
		writeErr(w, http.StatusBadRequest, "нужно пользовательское сообщение с текстом")
		return
	}

	req := editorRequest(text)
	if strings.TrimSpace(req.Text) == "" {
		writeErr(w, http.StatusBadRequest, "после режима редактуры не осталось текста")
		return
	}
	ctx, cancel := context.WithTimeout(r.Context(), s.cfg.RequestTimeout)
	defer cancel()

	w.Header().Set("Content-Type", "text/event-stream; charset=utf-8")
	w.Header().Set("Cache-Control", "no-cache")
	w.Header().Set("Connection", "keep-alive")
	w.Header().Set("X-Accel-Buffering", "no")
	w.WriteHeader(http.StatusOK)
	flusher, _ := w.(http.Flusher)

	threadID := input.ThreadID
	if threadID == "" {
		threadID = "editor-thread"
	}
	runID := input.RunID
	if runID == "" {
		runID = "editor-run"
	}
	if !writeSSE(w, flusher, map[string]any{
		"type": "RUN_STARTED", "threadId": threadID, "runId": runID,
	}) {
		return
	}

	resultCh := make(chan editOutcome, 1)
	go func() {
		result, err := s.ed.Edit(ctx, req)
		resultCh <- editOutcome{result: result, err: err}
	}()

	// A long model request should not look like a dead connection to a reverse proxy. SSE comments
	// are transport keep-alives, not conversation events, so they never reach the transcript.
	ticker := time.NewTicker(15 * time.Second)
	defer ticker.Stop()

	var outcome editOutcome
	for {
		select {
		case outcome = <-resultCh:
			goto finished
		case <-ticker.C:
			if !writeSSEComment(w, flusher, "редактор работает") {
				cancel()
				return
			}
		case <-r.Context().Done():
			cancel()
			return
		}
	}

finished:
	if outcome.err != nil {
		writeSSE(w, flusher, map[string]any{
			"type": "RUN_ERROR", "message": editorError(outcome.err),
		})
		return
	}

	messageID := "editor-reply-" + runID
	if !writeSSE(w, flusher, map[string]any{
		"type": "TEXT_MESSAGE_START", "messageId": messageID, "role": "assistant",
	}) {
		return
	}
	if !writeSSE(w, flusher, map[string]any{
		"type": "TEXT_MESSAGE_CONTENT", "messageId": messageID,
		"delta": renderedResult(outcome.result),
	}) {
		return
	}
	if !writeSSE(w, flusher, map[string]any{
		"type": "TEXT_MESSAGE_END", "messageId": messageID,
	}) {
		return
	}
	writeSSE(w, flusher, map[string]any{
		"type": "RUN_FINISHED", "threadId": threadID, "runId": runID,
	})
}

func bearerMatches(header, token string) bool {
	want := "Bearer " + token
	if len(header) != len(want) {
		return false
	}
	return subtle.ConstantTimeCompare([]byte(header), []byte(want)) == 1
}

type editOutcome struct {
	result *editor.Result
	err    error
}

func latestUserText(messages []runMessage) (string, bool) {
	for i := len(messages) - 1; i >= 0; i-- {
		if messages[i].Role != "user" {
			continue
		}
		text := contentText(messages[i].Content)
		if strings.TrimSpace(text) != "" {
			return text, true
		}
	}
	return "", false
}

func contentText(raw json.RawMessage) string {
	var text string
	if json.Unmarshal(raw, &text) == nil {
		return text
	}

	var parts []textPart
	if json.Unmarshal(raw, &parts) != nil {
		return ""
	}
	var b strings.Builder
	for _, part := range parts {
		if part.Type == "" || part.Type == "text" {
			b.WriteString(part.Text)
		}
	}
	return b.String()
}

func editorRequest(text string) editor.Request {
	mode := "обычная"
	trimmed := strings.TrimLeft(text, " \t\r\n")
	if i := strings.IndexByte(trimmed, '\n'); i >= 0 {
		candidate := strings.TrimSpace(trimmed[:i])
		switch candidate {
		case "лёгкая", "обычная", "глубокая":
			mode = candidate
			text = trimmed[i+1:]
		}
	}
	return editor.Request{
		Text:    text,
		Game:    "hearthstone",
		Profile: "constructed-guide",
		Mode:    mode,
	}
}

func renderedResult(result *editor.Result) string {
	if result == nil {
		return "Редактор не вернул результат."
	}

	var b strings.Builder
	b.WriteString(result.Text)
	b.WriteString("\n\n---\nОтчёт редактора\n")
	b.WriteString("Изменения:\n")
	if len(result.Changes) == 0 {
		b.WriteString("- нет: текст оставлен без изменений\n")
	} else {
		for _, change := range result.Changes {
			switch change.Kind {
			case "changed":
				b.WriteString(fmt.Sprintf("- строка %d: %s\n+ строка %d: %s\n",
					change.Line, quoteDiff(change.Before), change.Line, quoteDiff(change.After)))
			case "added":
				b.WriteString(fmt.Sprintf("+ строка %d: %s\n", change.Line, quoteDiff(change.After)))
			case "removed":
				b.WriteString(fmt.Sprintf("- строка %d: %s\n", change.Line, quoteDiff(change.Before)))
			case "omitted":
				b.WriteString("- остальные изменения скрыты в кратком отчёте\n")
			}
		}
	}
	b.WriteString("Сохранено:\n")
	if len(result.Preserved) == 0 {
		b.WriteString("- нет данных\n")
	} else {
		for _, item := range result.Preserved {
			b.WriteString("- " + item + "\n")
		}
	}
	b.WriteString("Статус: ")
	if result.Accepted {
		b.WriteString("правка принята")
	} else {
		b.WriteString("правка отклонена, возвращён исходный текст")
	}
	if len(result.Attempts) > 0 {
		b.WriteString(fmt.Sprintf(" (%d %s)", len(result.Attempts), pluralAttempts(len(result.Attempts))))
	}
	b.WriteString("\n")
	if reasons := rejectionReasons(result.Attempts); !result.Accepted && len(reasons) > 0 {
		b.WriteString("Причины отказа:\n")
		for _, reason := range reasons {
			b.WriteString("- " + reason + "\n")
		}
	}
	if result.SaveStatus != "" {
		b.WriteString("Сохранение: " + result.SaveStatus + "\n")
	}
	if len(result.Caveats) > 0 {
		b.WriteString("Примечание: " + strings.Join(result.Caveats, " ") + "\n")
	}
	return b.String()
}

func rejectionReasons(attempts []editor.Attempt) []string {
	seen := make(map[string]struct{})
	reasons := make([]string, 0)
	for _, attempt := range attempts {
		for _, violation := range attempt.Violations {
			reason := strings.TrimSpace(violation.Message)
			if reason == "" {
				continue
			}
			if _, exists := seen[reason]; exists {
				continue
			}
			seen[reason] = struct{}{}
			reasons = append(reasons, reason)
		}
	}
	return reasons
}

func quoteDiff(s string) string {
	return "«" + s + "»"
}

func pluralAttempts(n int) string {
	if n%10 == 1 && n%100 != 11 {
		return "попытка"
	}
	if n%10 >= 2 && n%10 <= 4 && (n%100 < 12 || n%100 > 14) {
		return "попытки"
	}
	return "попыток"
}

func editorError(err error) string {
	if err == nil {
		return "редактор не вернул результат"
	}
	return fmt.Sprintf("редактор: %s", err)
}

func writeSSE(w http.ResponseWriter, flusher http.Flusher, event map[string]any) bool {
	payload, err := json.Marshal(event)
	if err != nil {
		return false
	}
	if _, err := io.WriteString(w, "data: "); err != nil {
		return false
	}
	if _, err := w.Write(payload); err != nil {
		return false
	}
	if _, err := io.WriteString(w, "\n\n"); err != nil {
		return false
	}
	if flusher != nil {
		flusher.Flush()
	}
	return true
}

func writeSSEComment(w http.ResponseWriter, flusher http.Flusher, comment string) bool {
	if _, err := io.WriteString(w, ": "+comment+"\n\n"); err != nil {
		return false
	}
	if flusher != nil {
		flusher.Flush()
	}
	return true
}
