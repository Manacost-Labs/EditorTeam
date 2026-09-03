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

// ResponseError сохраняет HTTP-код Python-сайдкара для совместимого proxy.
type ResponseError struct {
	Status int
	Body   string
}

func (e *ResponseError) Error() string {
	return fmt.Sprintf("анализатор вернул %d: %s", e.Status, e.Body)
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
	Warnings         []Violation    `json:"warnings,omitempty"`
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
	Depth            string              `json:"depth,omitempty"`
	Protected        []string            `json:"protected"`
	Replace          []map[string]string `json:"replace"`
	Keep             []string            `json:"keep"`
	Typography       map[string]any      `json:"typography"`
	SectionsRequired []string            `json:"sections_required"`
	Sections         []SkeletonSection   `json:"sections,omitempty"`
	MinWords         int                 `json:"min_words,omitempty"`
	RequireClasses   bool                `json:"require_classes,omitempty"`
	Form             map[string]any      `json:"form,omitempty"`
	Corrections      []map[string]string `json:"corrections,omitempty"` // правки автора: было → стало → почему
	Norms            map[string]any      `json:"norms"`
	Editorial        map[string]any      `json:"editorial"`
	ReaderQuality    map[string]any      `json:"reader_quality,omitempty"`
	CorpusVersion    string              `json:"corpus_version"`

	// Только для глубины «переплавка»: модель должна видеть, как автор
	// звучит, а не одни числа норм.
	Skeleton            *Skeleton      `json:"skeleton,omitempty"`
	VoiceSignature      string         `json:"voice_signature,omitempty"`
	StyleExamples       []StyleExample `json:"style_examples,omitempty"`
	StyleExamplesSource string         `json:"style_examples_source,omitempty"`
	Markers             *MarkerLists   `json:"markers,omitempty"`
	RhythmInstruction   []string       `json:"rhythm_instruction,omitempty"`
	PromptBudget        map[string]any `json:"prompt_budget,omitempty"`
}

// SkeletonSection — раздел жанра как данные: назначение и порядок берутся
// из config/profiles, а не из прозы в Go.
type SkeletonSection struct {
	ID       string   `json:"id"`
	Title    string   `json:"title"`
	Variants []string `json:"variants,omitempty"`
	Purpose  string   `json:"purpose,omitempty"`
	MinWords *int     `json:"min_words,omitempty"`
	Order    int      `json:"order,omitempty"`
	Required bool     `json:"required"`
}

type Skeleton struct {
	Profile        string            `json:"profile"`
	Sections       []SkeletonSection `json:"sections"`
	Opening        map[string]any    `json:"opening,omitempty"`
	Closing        map[string]any    `json:"closing,omitempty"`
	RequireClasses bool              `json:"require_classes,omitempty"`
	MinWords       int               `json:"min_words,omitempty"`
}

// StyleExample — абзац автора из архива: только форма, факты устарели.
type StyleExample struct {
	Role  string `json:"role"`
	Name  string `json:"name"`
	Text  string `json:"text"`
	Score any    `json:"score,omitempty"`
}

type MarkerEntry struct {
	Name     string   `json:"name"`
	Examples []string `json:"examples"`
	Fix      string   `json:"fix,omitempty"`
}

type MarkerLists struct {
	Remove  []MarkerEntry `json:"remove"`
	Rewrite []MarkerEntry `json:"rewrite"`
	Review  []MarkerEntry `json:"review"`
}

type ValidationContext struct {
	Mode              string
	Depth             string
	DeclaredMissing   []string
	EvidenceRequested bool
	ClaimsBefore      []map[string]any
	ClaimsAfter       []map[string]any
	CurrentPatch      string
	CurrentMetaEpoch  string
}

// RulesContext — что нужно сайдкару, чтобы собрать правила: editorial mode,
// глубина правки и исходник (для подбора образцов манеры при переплавке).
type RulesContext struct {
	Mode  string
	Depth string
	Text  string
}

// Outline — план переплавки, который модель возвращает первым проходом.
type Outline struct {
	Sections        []OutlineSection `json:"sections"`
	MissingSections []string         `json:"missing_sections"`
	Notes           []string         `json:"notes,omitempty"`
}

type OutlineSection struct {
	ID     string   `json:"id"`
	Title  string   `json:"title"`
	Claims []string `json:"claims"`
}

type OutlineVerdict struct {
	OK         bool        `json:"ok"`
	Violations []Violation `json:"violations"`
	Warnings   []Violation `json:"warnings,omitempty"`
	Normalized *Outline    `json:"normalized,omitempty"`
	Profile    string      `json:"profile,omitempty"`
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

// Forward возвращает сырой JSON совместимого endpoint'а. Go-шлюз использует
// его для сохранения контрактов Python без копирования всех полей в типы.
func (c *Client) Forward(ctx context.Context, path string, in any) (json.RawMessage, error) {
	body, err := json.Marshal(in)
	if err != nil {
		return nil, fmt.Errorf("сборка запроса: %w", err)
	}
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, c.base+path, bytes.NewReader(body))
	if err != nil {
		return nil, err
	}
	req.Header.Set("Content-Type", "application/json")
	resp, err := c.http.Do(req)
	if err != nil {
		return nil, fmt.Errorf("анализатор недоступен (%s): %w", c.base, err)
	}
	defer resp.Body.Close()
	raw, err := io.ReadAll(io.LimitReader(resp.Body, 8<<20))
	if err != nil {
		return nil, err
	}
	if resp.StatusCode >= 400 {
		return nil, &ResponseError{Status: resp.StatusCode, Body: string(raw)}
	}
	return json.RawMessage(raw), nil
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

// Validate — затвор. Смысловые и защитные нарушения блокируют правку;
// стилевые метрики внутри рабочего диапазона возвращаются как review warnings.
func (c *Client) Validate(ctx context.Context, before, after, game, profile string) (*Verdict, error) {
	return c.ValidateWithContext(ctx, before, after, game, profile, ValidationContext{Mode: "GUIDE"})
}

func (c *Client) ValidateWithContext(ctx context.Context, before, after, game, profile string, edit ValidationContext) (*Verdict, error) {
	var v Verdict
	if edit.Mode == "" {
		edit.Mode = "GUIDE"
	}
	if edit.Depth == "" {
		edit.Depth = "обычная"
	}
	payload := map[string]any{
		"before": before, "after": after, "game": game, "profile": profile,
		"mode": edit.Mode, "depth": edit.Depth, "declared_missing": edit.DeclaredMissing,
		"evidence_requested": edit.EvidenceRequested,
		"claims_before":      edit.ClaimsBefore, "claims_after": edit.ClaimsAfter,
		"current_patch": edit.CurrentPatch, "current_meta_epoch": edit.CurrentMetaEpoch,
	}
	err := c.post(ctx, "/validate", payload, &v)
	return &v, err
}

func (c *Client) Rules(ctx context.Context, game, profile string) (*Rules, error) {
	return c.RulesWithMode(ctx, game, profile, "GUIDE")
}

func (c *Client) RulesWithMode(ctx context.Context, game, profile, mode string) (*Rules, error) {
	return c.RulesWithContext(ctx, game, profile, RulesContext{Mode: mode})
}

func (c *Client) RulesWithContext(ctx context.Context, game, profile string, rc RulesContext) (*Rules, error) {
	var r Rules
	if rc.Mode == "" {
		rc.Mode = "GUIDE"
	}
	if rc.Depth == "" {
		rc.Depth = "обычная"
	}
	payload := map[string]any{"game": game, "profile": profile, "mode": rc.Mode, "depth": rc.Depth}
	if rc.Text != "" {
		payload["text"] = rc.Text
	}
	err := c.post(ctx, "/rules", payload, &r)
	return &r, err
}

// ValidateOutline проверяет план переплавки против скелета профиля и
// исходника: обязательные разделы, честное «нет материала», карты и числа,
// которых в исходнике не было.
func (c *Client) ValidateOutline(ctx context.Context, outline Outline, source, game, profile string) (*OutlineVerdict, error) {
	var v OutlineVerdict
	err := c.post(ctx, "/outline/validate", map[string]any{
		"outline": outline, "source": source, "game": game, "profile": profile,
	}, &v)
	return &v, err
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
