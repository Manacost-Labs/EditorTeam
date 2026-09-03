// Package natasha — клиент к необязательному Python NLP-сайдкару.
package natasha

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"strings"
	"time"

	"github.com/Manacost-Labs/EditorTeam/go/internal/finding"
)

type Client struct {
	base    string
	http    *http.Client
	maxBody int64
}

func New(base string, timeout time.Duration) *Client {
	if timeout <= 0 {
		timeout = 8 * time.Second
	}
	return &Client{base: strings.TrimRight(base, "/"), http: &http.Client{Timeout: timeout}, maxBody: 8 << 20}
}

func (c *Client) URL() string {
	if c == nil {
		return ""
	}
	return c.base
}

type Input struct {
	Text     string `json:"text"`
	Language string `json:"language,omitempty"`
	Game     string `json:"game,omitempty"`
	Profile  string `json:"profile,omitempty"`
}

type Response struct {
	Sentences  []Span            `json:"sentences"`
	Tokens     []Span            `json:"tokens"`
	Paragraphs []Span            `json:"paragraphs"`
	Entities   []Entity          `json:"entities"`
	Terms      []Entity          `json:"terms,omitempty"`
	Findings   []finding.Finding `json:"findings"`
	Meta       map[string]any    `json:"meta,omitempty"`
	Cached     bool              `json:"cached,omitempty"`
}

type Span struct {
	Text   string         `json:"text"`
	Offset int            `json:"offset"`
	Length int            `json:"length"`
	Line   int            `json:"line"`
	Column int            `json:"column"`
	Lemma  string         `json:"lemma,omitempty"`
	POS    string         `json:"pos,omitempty"`
	Morph  map[string]any `json:"morph,omitempty"`
}

type Entity struct {
	Text   string `json:"text"`
	Type   string `json:"type,omitempty"`
	Kind   string `json:"kind,omitempty"`
	Offset int    `json:"offset"`
	Length int    `json:"length"`
	Line   int    `json:"line"`
	Column int    `json:"column"`
}

func (c *Client) Analyze(ctx context.Context, in Input) (*Response, error) {
	if c == nil || c.base == "" {
		return nil, fmt.Errorf("NATASHA_URL не задан")
	}
	body, err := json.Marshal(in)
	if err != nil {
		return nil, fmt.Errorf("сборка запроса NLP: %w", err)
	}
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, c.base+"/analyze", bytes.NewReader(body))
	if err != nil {
		return nil, err
	}
	req.Header.Set("Content-Type", "application/json")
	resp, err := c.http.Do(req)
	if err != nil {
		return nil, fmt.Errorf("NLP-сайдкар недоступен: %w", err)
	}
	defer resp.Body.Close()
	raw, err := io.ReadAll(io.LimitReader(resp.Body, c.maxBody))
	if err != nil {
		return nil, err
	}
	if resp.StatusCode >= 400 {
		return nil, fmt.Errorf("NLP-сайдкар вернул %d: %s", resp.StatusCode, truncate(string(raw), 300))
	}
	var out Response
	if err := json.Unmarshal(raw, &out); err != nil {
		return nil, fmt.Errorf("ответ NLP-сайдкара не JSON: %w", err)
	}
	return &out, nil
}

func (c *Client) Health(ctx context.Context) error {
	if c == nil || c.base == "" {
		return fmt.Errorf("NATASHA_URL не задан")
	}
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, c.base+"/health", nil)
	if err != nil {
		return err
	}
	resp, err := c.http.Do(req)
	if err != nil {
		return fmt.Errorf("NLP-сайдкар недоступен: %w", err)
	}
	defer resp.Body.Close()
	if resp.StatusCode >= 400 {
		return fmt.Errorf("NLP-сайдкар вернул %d", resp.StatusCode)
	}
	return nil
}

func truncate(s string, n int) string {
	if len(s) <= n {
		return s
	}
	return s[:n] + "…"
}
