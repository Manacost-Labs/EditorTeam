package openbot

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"regexp"
	"strings"
	"time"
)

var preparedEditIDPattern = regexp.MustCompile(`^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$`)

type PreparedGoogleDocumentEdit struct {
	ID         string `json:"id"`
	State      string `json:"state"`
	ExpiresAt  string `json:"expiresAt"`
	EditCount  int    `json:"editCount"`
	ReviewPath string `json:"reviewPath"`
}

type GoogleDocumentEditPreparer interface {
	PrepareGoogleDocumentEdit(ctx context.Context, run, documentID, sourceText, candidateText string) (*PreparedGoogleDocumentEdit, error)
}

type Client struct {
	endpoint string
	token    string
	http     *http.Client
}

func New(baseURL, token string, timeout time.Duration) *Client {
	return &Client{
		endpoint: strings.TrimRight(baseURL, "/") + "/internal/editor/google-doc-edits",
		token:    token,
		http:     &http.Client{Timeout: timeout},
	}
}

func (c *Client) PrepareGoogleDocumentEdit(ctx context.Context, run, documentID, sourceText, candidateText string) (*PreparedGoogleDocumentEdit, error) {
	body, err := json.Marshal(map[string]string{
		"run": run, "documentId": documentID, "sourceText": sourceText, "candidateText": candidateText,
	})
	if err != nil {
		return nil, fmt.Errorf("сборка предложения: %w", err)
	}
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, c.endpoint, bytes.NewReader(body))
	if err != nil {
		return nil, fmt.Errorf("создание запроса: %w", err)
	}
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("X-OpenBot-Agent-Token", c.token)
	resp, err := c.http.Do(req)
	if err != nil {
		return nil, fmt.Errorf("TeamBot недоступен")
	}
	defer resp.Body.Close()
	raw, readErr := io.ReadAll(io.LimitReader(resp.Body, 4096))
	if readErr != nil {
		return nil, fmt.Errorf("ответ TeamBot не прочитан")
	}
	if resp.StatusCode >= 400 {
		var refusal struct {
			Error string `json:"error"`
		}
		if json.Unmarshal(raw, &refusal) == nil && strings.TrimSpace(refusal.Error) != "" {
			return nil, fmt.Errorf("%s", strings.TrimSpace(refusal.Error))
		}
		return nil, fmt.Errorf("TeamBot отклонил подготовку (HTTP %d)", resp.StatusCode)
	}
	var prepared PreparedGoogleDocumentEdit
	if json.Unmarshal(raw, &prepared) != nil ||
		prepared.State != "pending" ||
		!preparedEditIDPattern.MatchString(prepared.ID) ||
		prepared.ReviewPath != "/editor/google-doc-edits/"+prepared.ID ||
		prepared.EditCount < 1 || prepared.EditCount > 30 {
		return nil, fmt.Errorf("TeamBot вернул неверное предложение правки")
	}
	return &prepared, nil
}
