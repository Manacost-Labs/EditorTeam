// Package analyzer — клиент к Python-сайдкару с анализаторами.
//
// Проверки остались на Python не по инерции: русской морфологии для Go
// практически нет, а без неё теряется распознавание падежей и коротких
// имён. Go оркеструет, Python измеряет.
package analyzer

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"time"
)

type Client struct {
	base string
	http *http.Client
}

func New(base string, timeout time.Duration) *Client {
	return &Client{base: base, http: &http.Client{Timeout: timeout}}
}

type Violation struct {
	Kind    string `json:"kind"`
	Signal  string `json:"signal,omitempty"`
	Was     int    `json:"was,omitempty"`
	Now     int    `json:"now,omitempty"`
	Message string `json:"message"`
}

type Verdict struct {
	Accepted         bool           `json:"accepted"`
	Violations       []Violation    `json:"violations"`
	Metrics          map[string]any `json:"metrics"`
	NormsProvisional bool           `json:"norms_provisional"`
}

type Report struct {
	Profile          string           `json:"profile"`
	Game             string           `json:"game"`
	Summary          map[string]int   `json:"summary"`
	Metrics          map[string]any   `json:"metrics"`
	Findings         []map[string]any `json:"findings"`
	Skipped          []string         `json:"analyzers_skipped"`
	Notes            []string         `json:"notes"`
	NormsProvisional bool             `json:"norms_provisional"`
}

type Rules struct {
	Game             string              `json:"game"`
	Profile          string              `json:"profile"`
	Protected        []string            `json:"protected"`
	Replace          []map[string]string `json:"replace"`
	Keep             []string            `json:"keep"`
	Typography       map[string]any      `json:"typography"`
	SectionsRequired []string            `json:"sections_required"`
	Norms            map[string]any      `json:"norms"`
}

func (c *Client) post(ctx context.Context, path string, in, out any) error {
	body, err := json.Marshal(in)
	if err != nil {
		return fmt.Errorf("сборка запроса: %w", err)
	}
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, c.base+path,
		bytes.NewReader(body))
	if err != nil {
		return err
	}
	req.Header.Set("Content-Type", "application/json")

	resp, err := c.http.Do(req)
	if err != nil {
		return fmt.Errorf("анализатор недоступен (%s): %w", c.base, err)
	}
	defer resp.Body.Close()

	raw, err := io.ReadAll(io.LimitReader(resp.Body, 8<<20))
	if err != nil {
		return err
	}
	if resp.StatusCode >= 400 {
		return fmt.Errorf("анализатор вернул %d: %s", resp.StatusCode, string(raw))
	}
	return json.Unmarshal(raw, out)
}

func (c *Client) Analyze(ctx context.Context, text, game, profile string) (*Report, error) {
	var r Report
	err := c.post(ctx, "/analyze",
		map[string]string{"text": text, "game": game, "profile": profile}, &r)
	return &r, err
}

// Validate — затвор. Главная проверка сервиса: правка принимается, только
// если не потеряла голос, защищённые элементы и ритм.
func (c *Client) Validate(ctx context.Context, before, after, game, profile string) (*Verdict, error) {
	var v Verdict
	err := c.post(ctx, "/validate",
		map[string]string{"before": before, "after": after,
			"game": game, "profile": profile}, &v)
	return &v, err
}

func (c *Client) Rules(ctx context.Context, game, profile string) (*Rules, error) {
	var r Rules
	err := c.post(ctx, "/rules",
		map[string]string{"game": game, "profile": profile}, &r)
	return &r, err
}

func (c *Client) Health(ctx context.Context) error {
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, c.base+"/health", nil)
	if err != nil {
		return err
	}
	resp, err := c.http.Do(req)
	if err != nil {
		return fmt.Errorf("анализатор недоступен (%s): %w", c.base, err)
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		return fmt.Errorf("анализатор нездоров: %d", resp.StatusCode)
	}
	return nil
}
