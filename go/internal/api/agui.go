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
	"github.com/Manacost-Labs/EditorTeam/go/internal/llm"
)

// RunInput is the small part of RunAgentInput the editor needs. Only the two
// read-only Google Docs tools and the signed OpenBot routing fields are later
// forwarded; arbitrary tools and props never reach the model.
type runInput struct {
	ThreadID       string         `json:"threadId"`
	RunID          string         `json:"runId"`
	Messages       []runMessage   `json:"messages"`
	Tools          []runTool      `json:"tools"`
	ForwardedProps map[string]any `json:"forwardedProps"`
}

type runMessage struct {
	Role    string          `json:"role"`
	Content json.RawMessage `json:"content"`
}

type textPart struct {
	Type string `json:"type"`
	Text string `json:"text"`
}

type runTool struct {
	Name        string         `json:"name"`
	Description string         `json:"description"`
	Parameters  map[string]any `json:"parameters"`
}

var googleDocumentReadTools = map[string]struct{}{
	"mcp__google-drive__read_google_document":          {},
	"mcp__google-drive__read_google_document_edit_map": {},
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

	text, ok := editorInputText(input)
	if !ok {
		writeErr(w, http.StatusBadRequest, "нужно пользовательское сообщение с текстом")
		return
	}

	req := editorRequest(text)
	req.LLMContext = editorLLMContext(input)
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
	if editor.GoogleDocumentURL(req.Text) && !hasGoogleDocumentReadTool(req.LLMContext.Tools) {
		writeSSE(w, flusher, map[string]any{
			"type":    "RUN_ERROR",
			"message": "Для ссылки Google Docs Главному редактору нужен grant Google Docs — чтение. Выдайте его этому Боту или вставьте текст документа.",
		})
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
	if outcome.result.Accepted && outcome.result.GoogleDocumentID != "" && outcome.result.SourceText != "" && outcome.result.Text != outcome.result.SourceText {
		run, _ := input.ForwardedProps["openbotRun"].(string)
		if s.edits == nil || strings.TrimSpace(run) == "" {
			outcome.result.SaveStatus = "правка проверена, но безопасное подтверждение Google Docs не настроено; документ не изменён"
		} else {
			prepared, prepareErr := s.edits.PrepareGoogleDocumentEdit(ctx, run, outcome.result.GoogleDocumentID, outcome.result.SourceText, outcome.result.Text)
			if prepareErr != nil {
				outcome.result.SaveStatus = "правка проверена, но предложение сохранения не создано: " + prepareErr.Error()
			} else {
				outcome.result.ReviewPath = prepared.ReviewPath
				outcome.result.SaveStatus = fmt.Sprintf("подготовлено %d изменений; Google Docs пока не изменён", prepared.EditCount)
			}
		}
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

// editorInputText keeps a governed Bot-to-Bot handoff typed all the way to this specialist. A
// general AG-UI agent receives a prose envelope explaining who asked, the task and the expected
// result. Editing that envelope would corrupt the request instead of editing its task, so OpenBot
// also supplies the exact task as server-authored run context. Ordinary user turns continue to use
// the latest user message.
func editorInputText(input runInput) (string, bool) {
	raw, handedOff := input.ForwardedProps["openbotHandoff"]
	if !handedOff {
		return latestUserText(input.Messages)
	}
	envelope, ok := raw.(map[string]any)
	if !ok {
		return "", false
	}
	task, ok := envelope["task"].(string)
	if !ok || strings.TrimSpace(task) == "" {
		return "", false
	}
	return task, true
}

func editorLLMContext(input runInput) llm.RequestContext {
	tools := make([]llm.Tool, 0, len(input.Tools))
	allowed := make(map[string]struct{})
	for _, tool := range input.Tools {
		if _, ok := googleDocumentReadTools[tool.Name]; !ok {
			continue
		}
		tools = append(tools, llm.Tool{
			Name:        tool.Name,
			Description: tool.Description,
			Parameters:  tool.Parameters,
		})
		allowed[tool.Name] = struct{}{}
	}

	props := make(map[string]any)
	if run, ok := input.ForwardedProps["openbotRun"].(string); ok && strings.TrimSpace(run) != "" {
		props["openbotRun"] = run
	}
	if raw, ok := input.ForwardedProps["openbotDeploymentTools"].([]any); ok {
		deploymentTools := make([]string, 0, len(raw))
		for _, candidate := range raw {
			name, ok := candidate.(string)
			if !ok {
				continue
			}
			if _, ok := allowed[name]; ok {
				deploymentTools = append(deploymentTools, name)
			}
		}
		if len(deploymentTools) > 0 {
			props["openbotDeploymentTools"] = deploymentTools
		}
	}
	return llm.RequestContext{Tools: tools, ForwardedProps: props}
}

func hasGoogleDocumentReadTool(tools []llm.Tool) bool {
	for _, tool := range tools {
		if tool.Name == "mcp__google-drive__read_google_document" {
			return true
		}
	}
	return false
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
	mode := editor.DepthDefault
	trimmed := strings.TrimLeft(text, " \t\r\n")
	if i := strings.IndexByte(trimmed, '\n'); i >= 0 {
		// первая строка — режим: «Переплавка», «легкая:» и «ЛЁГКАЯ» тоже понимаются
		if canon, ok := editor.NormalizeDepth(trimmed[:i]); ok {
			mode = canon
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
	b.WriteString("\n\n---\n> **Проверка редактора:** ")
	if result.Accepted {
		b.WriteString("правка принята")
	} else {
		b.WriteString("правка отклонена, возвращён исходный текст")
	}
	if len(result.Attempts) > 0 {
		b.WriteString(fmt.Sprintf(" · %d %s", len(result.Attempts), pluralAttempts(len(result.Attempts))))
	}
	if result.Depth == editor.DepthRewrite {
		b.WriteString(" · переплавка")
	}
	if result.Depth == editor.DepthRewrite && len(result.MissingTitles) > 0 {
		b.WriteString("\n>\n> **Не хватает в исходнике:**\n")
		for _, title := range result.MissingTitles {
			b.WriteString("> - " + title + " — раздел не написан, чтобы ничего не выдумывать\n")
		}
	}
	b.WriteString("\n>\n> **Изменения:**\n")
	if result.Depth == editor.DepthRewrite && result.Accepted {
		b.WriteString("> - Текст пересобран целиком (переплавка); построчный дифф не приводится\n")
	} else if len(result.Changes) == 0 {
		b.WriteString("> - Нет: текст оставлен без изменений\n")
	} else {
		for _, change := range result.Changes {
			switch change.Kind {
			case "changed":
				b.WriteString(fmt.Sprintf("> - Строка %d: %s → %s\n",
					change.Line, quoteDiff(change.Before), quoteDiff(change.After)))
			case "added":
				b.WriteString(fmt.Sprintf("> - Добавлена строка %d: %s\n", change.Line, quoteDiff(change.After)))
			case "removed":
				b.WriteString(fmt.Sprintf("> - Удалена строка %d: %s\n", change.Line, quoteDiff(change.Before)))
			case "omitted":
				b.WriteString("> - Остальные изменения скрыты в кратком отчёте\n")
			}
		}
	}
	b.WriteString(">\n> **Сохранено при правке:**\n")
	if len(result.Preserved) == 0 {
		b.WriteString("> - Нет данных\n")
	} else {
		for _, item := range result.Preserved {
			b.WriteString("> - " + item + "\n")
		}
	}
	if reasons := rejectionReasons(result.Attempts); !result.Accepted && len(reasons) > 0 {
		b.WriteString(">\n> **Причины отказа:**\n")
		for _, reason := range reasons {
			b.WriteString("> - " + reason + "\n")
		}
	}
	if result.SaveStatus != "" {
		b.WriteString(">\n> **Сохранение:** " + result.SaveStatus + "\n")
	}
	if result.ReviewPath != "" {
		b.WriteString(">\n> [Проверить и сохранить в Google Docs](" + result.ReviewPath + ")\n")
	}
	if len(result.Caveats) > 0 {
		b.WriteString(">\n> **Примечание:** " + strings.Join(result.Caveats, " ") + "\n")
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
