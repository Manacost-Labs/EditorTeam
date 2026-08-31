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
	EditorialMode    string           `json:"editorial_mode"`
	CorpusVersion    string           `json:"corpus_version"`
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
	Editorial        map[string]any      `json:"editorial"`
	CorpusVersion    string              `json:"corpus_version"`
}

type ValidationContext struct {
	Mode              string
	EvidenceRequested bool
	ClaimsBefore      []map[string]any
	ClaimsAfter       []map[string]any
	CurrentPatch      string
	CurrentMetaEpoch  string
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
	return c.AnalyzeWithMode(ctx, text, game, profile, "GUIDE", false)
}

func (c *Client) AnalyzeWithMode(ctx context.Context, text, game, profile, mode string, evidenceRequested bool) (*Report, error) {
	var r Report
	err := c.post(ctx, "/analyze",
		map[string]any{"text": text, "game": game, "profile": profile,
			"mode": mode, "evidence_requested": evidenceRequested}, &r)
	return &r, err
}

// Validate — затвор. Главная проверка сервиса: правка принимается, только
// если не потеряла голос, защищённые элементы и ритм.
func (c *Client) Validate(ctx context.Context, before, after, game, profile string) (*Verdict, error) {
	return c.ValidateWithContext(ctx, before, after, game, profile, ValidationContext{Mode: "GUIDE"})
}

func (c *Client) ValidateWithContext(ctx context.Context, before, after, game, profile string, edit ValidationContext) (*Verdict, error) {
	var v Verdict
	if edit.Mode == "" {
		edit.Mode = "GUIDE"
	}
	payload := map[string]any{
		"before": before, "after": after, "game": game, "profile": profile,
		"mode": edit.Mode, "evidence_requested": edit.EvidenceRequested,
		"claims_before": edit.ClaimsBefore, "claims_after": edit.ClaimsAfter,
		"current_patch": edit.CurrentPatch, "current_meta_epoch": edit.CurrentMetaEpoch,
	}
	err := c.post(ctx, "/validate", payload, &v)
	return &v, err
}

func (c *Client) Rules(ctx context.Context, game, profile string) (*Rules, error) {
	return c.RulesWithMode(ctx, game, profile, "GUIDE")
}

func (c *Client) RulesWithMode(ctx context.Context, game, profile, mode string) (*Rules, error) {
	var r Rules
	err := c.post(ctx, "/rules",
		map[string]string{"game": game, "profile": profile, "mode": mode}, &r)
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
