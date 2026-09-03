// Package languagetool — небольшой HTTP-клиент для LanguageTool Server.
package languagetool

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/url"
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
	return &Client{base: strings.TrimRight(base, "/"), http: &http.Client{Timeout: timeout}, maxBody: 4 << 20}
}

func (c *Client) URL() string {
	if c == nil {
		return ""
	}
	return c.base
}

type Options struct {
	EnabledRules  []string
	DisabledRules []string
}

type response struct {
	Matches []match `json:"matches"`
}
type match struct {
	Message string `json:"message"`
	Rule    struct {
		ID string `json:"id"`
	} `json:"rule"`
	Replacements []struct {
		Value string `json:"value"`
	} `json:"replacements"`
	Offset  int `json:"offset"`
	Length  int `json:"length"`
	Context struct {
		Text   string `json:"text"`
		Offset int    `json:"offset"`
		Length int    `json:"length"`
	} `json:"context"`
	Sentence string `json:"sentence"`
	Type     struct {
		TypeName string `json:"typeName"`
	} `json:"type"`
}

// Check не исправляет текст, а возвращает только предложения LanguageTool.
func (c *Client) Check(ctx context.Context, text, language string, opts Options) ([]finding.Finding, error) {
	if c == nil || c.base == "" {
		return nil, nil
	}
	if language == "" {
		language = "ru-RU"
	}
	switch strings.ToLower(language) {
	case "ru":
		language = "ru-RU"
	case "en":
		language = "en-US"
	case "pl":
		language = "pl-PL"
	}
	form := url.Values{}
	form.Set("text", text)
	form.Set("language", language)
	if len(opts.EnabledRules) > 0 {
		form.Set("enabledRules", strings.Join(opts.EnabledRules, ","))
	}
	if len(opts.DisabledRules) > 0 {
		form.Set("disabledRules", strings.Join(opts.DisabledRules, ","))
	}
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, c.base+"/v2/check", strings.NewReader(form.Encode()))
	if err != nil {
		return nil, err
	}
	req.Header.Set("Content-Type", "application/x-www-form-urlencoded")
	resp, err := c.http.Do(req)
	if err != nil {
		return nil, fmt.Errorf("LanguageTool недоступен: %w", err)
	}
	defer resp.Body.Close()
	raw, err := io.ReadAll(io.LimitReader(resp.Body, c.maxBody))
	if err != nil {
		return nil, err
	}
	if resp.StatusCode >= 400 {
		return nil, fmt.Errorf("LanguageTool вернул %d: %s", resp.StatusCode, truncate(string(raw), 300))
	}
	var parsed response
	if err := json.Unmarshal(raw, &parsed); err != nil {
		return nil, fmt.Errorf("ответ LanguageTool не JSON: %w", err)
	}
	findings := make([]finding.Finding, 0, len(parsed.Matches))
	for _, item := range parsed.Matches {
		sev := "warning"
		if strings.Contains(strings.ToLower(item.Type.TypeName), "typographical") {
			sev = "suggestion"
		}
		suggestions := make([]string, 0, len(item.Replacements))
		for _, r := range item.Replacements {
			suggestions = append(suggestions, r.Value)
			if len(suggestions) == 5 {
				break
			}
		}
		line := 1 + strings.Count(item.Sentence[:min(item.Offset, len(item.Sentence))], "\n")
		column := item.Offset + 1
		findings = append(findings, finding.Finding{Analyzer: "languagetool", RuleID: item.Rule.ID, Severity: sev,
			Message: item.Message, Suggestions: suggestions, Line: line, Column: column, Offset: item.Offset, Length: item.Length,
			Context: item.Context.Text})
	}
	return findings, nil
}

func min(a, b int) int {
	if a < b {
		return a
	}
	return b
}
func truncate(s string, n int) string {
	if len(s) <= n {
		return s
	}
	return s[:n] + "…"
}
