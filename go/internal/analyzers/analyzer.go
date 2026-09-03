// Package analyzers содержит единый контракт для проверок редактора.
//
// Анализатор не меняет текст. Он возвращает находки, которые pipeline
// объединяет с семантическим затвором и показывает редактору.
package analyzers

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os/exec"
	"path/filepath"
	"strings"
	"time"

	"github.com/Manacost-Labs/EditorTeam/go/internal/analyzer"
	"github.com/Manacost-Labs/EditorTeam/go/internal/finding"
	"github.com/Manacost-Labs/EditorTeam/go/internal/hunspell"
	languageTool "github.com/Manacost-Labs/EditorTeam/go/internal/language_tool"
	"github.com/Manacost-Labs/EditorTeam/go/internal/markdownlint"
	"github.com/Manacost-Labs/EditorTeam/go/internal/natasha"
	"github.com/Manacost-Labs/EditorTeam/go/internal/vale"
)

// Input — общий вход для всех анализаторов.
type Input struct {
	Text            string
	Before          string
	After           string
	Game            string
	Profile         string
	Mode            string
	Depth           string
	Language        string
	CurrentPatch    string
	CurrentMeta     string
	ClaimsBefore    []map[string]any
	ClaimsAfter     []map[string]any
	DeclaredMissing []string
}

// Имена AnalyzerInput/AnalyzerResult оставлены как публичные синонимы для
// adapters из внешних пакетов; короткие Input/Result сохраняют читаемость в
// текущем коде.
type AnalyzerInput = Input

type Finding = finding.Finding

type Result struct {
	Analyzer string         `json:"analyzer"`
	Findings []Finding      `json:"findings,omitempty"`
	Metrics  map[string]any `json:"metrics,omitempty"`
	Skipped  bool           `json:"skipped,omitempty"`
	Error    string         `json:"error,omitempty"`
}

type AnalyzerResult = Result

type Analyzer interface {
	Name() string
	Health(context.Context) error
	Analyze(context.Context, Input) (Result, error)
}

// NativeGoAnalyzer выполняет дешёвые проверки, не требующие морфологии.
type NativeGoAnalyzer struct{}

func (NativeGoAnalyzer) Name() string { return "native-go" }

func (NativeGoAnalyzer) Health(context.Context) error { return nil }

func (NativeGoAnalyzer) Analyze(_ context.Context, in Input) (Result, error) {
	r := Result{Analyzer: "native-go", Metrics: map[string]any{}}
	lower := strings.ToLower(in.Text)
	if strings.Contains(lower, "подарок") || strings.Contains(lower, "подарки") {
		r.Findings = append(r.Findings, Finding{Analyzer: r.Analyzer, RuleID: "terminology.dark-gifts-generic", Severity: "suggestion", Message: "используйте официальное название «Темные дары» или «Темный дар»"})
	}
	if strings.Contains(lower, "племя") || strings.Contains(lower, "племени") || strings.Contains(lower, "племен") {
		r.Findings = append(r.Findings, Finding{Analyzer: r.Analyzer, RuleID: "terminology.minion-type", Severity: "suggestion", Message: "для Battlegrounds используйте «тип существа», а не «племя»"})
	}
	return r, nil
}

// PythonAnalyzerAdapter сначала использует совместимый HTTP-сайдкар. Если
// задан Script, он может безопасно вызвать один из старых скриптов напрямую.
type PythonAnalyzerAdapter struct {
	Client *analyzer.Client
	Script string
	// LegacyScripts перечислены явно, чтобы миграция не потеряла ни одну
	// текущую проверку. При HTTP-режиме их вызывает server.py одним отчётом.
	LegacyScripts []string
	Python        string
	Timeout       time.Duration
	MaxBytes      int64
}

var LegacyScripts = []string{"claims.py", "cards.py", "semantic_diff.py", "rewrite_gate.py", "consistency.py", "structure.py", "clarity.py", "soul.py", "rhythm.py"}

func (p *PythonAnalyzerAdapter) Name() string { return "python" }

func (p *PythonAnalyzerAdapter) Health(ctx context.Context) error {
	if p == nil {
		return errors.New("Python-анализатор не настроен")
	}
	if p.Client != nil {
		return p.Client.Health(ctx)
	}
	if p.Script != "" {
		return nil
	}
	return errors.New("Python-сайдкар не настроен")
}

func (p *PythonAnalyzerAdapter) Analyze(ctx context.Context, in Input) (Result, error) {
	if p == nil {
		return Result{Analyzer: "python", Skipped: true}, nil
	}
	if p.Script != "" {
		return p.runScript(ctx, in)
	}
	if p.Client == nil {
		return Result{Analyzer: "python", Skipped: true, Error: "Python-сайдкар не настроен"}, nil
	}
	var raw analyzer.Report
	if in.Before != "" || in.After != "" {
		verdict, err := p.Client.ValidateWithContext(ctx, in.Before, in.After, in.Game, in.Profile, analyzer.ValidationContext{
			Mode: in.Mode, Depth: in.Depth, CurrentPatch: in.CurrentPatch, CurrentMetaEpoch: in.CurrentMeta,
			ClaimsBefore: in.ClaimsBefore, ClaimsAfter: in.ClaimsAfter, DeclaredMissing: in.DeclaredMissing,
		})
		if err != nil {
			return Result{Analyzer: "python"}, err
		}
		return verdictResult(verdict), nil
	}
	rep, err := p.Client.AnalyzeWithMode(ctx, in.Text, in.Game, in.Profile, in.Mode, false)
	if err != nil {
		return Result{Analyzer: "python"}, err
	}
	raw = *rep
	result := Result{Analyzer: "python", Metrics: raw.Metrics}
	for _, f := range raw.Findings {
		result.Findings = append(result.Findings, Finding{
			Analyzer: "python", RuleID: stringValue(f, "id"), Severity: severityValue(f),
			Message: stringValue(f, "message"), Context: stringValue(f, "evidence"),
			Line: intValue(f, "line"), Field: stringValue(f, "category"),
		})
	}
	return result, nil
}

func verdictResult(v *analyzer.Verdict) Result {
	r := Result{Analyzer: "python", Metrics: v.Metrics}
	for _, item := range append(append([]analyzer.Violation{}, v.Violations...), v.Warnings...) {
		sev := "error"
		if containsViolation(v.Warnings, item) {
			sev = "warning"
		}
		r.Findings = append(r.Findings, Finding{Analyzer: "python", RuleID: item.Kind, Severity: sev, Message: item.Message, Field: item.Signal})
	}
	return r
}

func containsViolation(items []analyzer.Violation, want analyzer.Violation) bool {
	for _, item := range items {
		if item.Kind == want.Kind && item.Message == want.Message {
			return true
		}
	}
	return false
}

func (p *PythonAnalyzerAdapter) runScript(ctx context.Context, in Input) (Result, error) {
	python := p.Python
	if python == "" {
		python = "python3"
	}
	timeout := p.Timeout
	if timeout <= 0 {
		timeout = 20 * time.Second
	}
	ctx, cancel := context.WithTimeout(ctx, timeout)
	defer cancel()
	payload, err := json.Marshal(in)
	if err != nil {
		return Result{Analyzer: "python"}, err
	}
	cmd := exec.CommandContext(ctx, python, p.Script, "--format", "json")
	cmd.Stdin = bytes.NewReader(payload)
	var out limitedBuffer
	max := p.MaxBytes
	if max <= 0 {
		max = 4 << 20
	}
	out.max = max
	cmd.Stdout, cmd.Stderr = &out, &out
	err = cmd.Run()
	if errors.Is(ctx.Err(), context.DeadlineExceeded) {
		return Result{Analyzer: "python"}, ctx.Err()
	}
	if err != nil {
		return Result{Analyzer: "python"}, fmt.Errorf("%s: %w: %s", filepath.Base(p.Script), err, truncate(out.String(), 400))
	}
	var parsed Result
	if err := json.Unmarshal(out.Bytes(), &parsed); err != nil {
		return Result{Analyzer: "python"}, fmt.Errorf("ответ %s не JSON: %w", filepath.Base(p.Script), err)
	}
	if parsed.Analyzer == "" {
		parsed.Analyzer = filepath.Base(p.Script)
	}
	return parsed, nil
}

// LanguageToolAnalyzer адаптирует HTTP-клиент к общему контракту.
type LanguageToolAnalyzer struct {
	Client   *languageTool.Client
	Language string
}

func (l *LanguageToolAnalyzer) Name() string { return "languagetool" }

func (l *LanguageToolAnalyzer) Health(context.Context) error {
	if l == nil || l.Client == nil || l.Client.URL() == "" {
		return errors.New("LanguageTool не настроен")
	}
	return nil
}

func (l *LanguageToolAnalyzer) Analyze(ctx context.Context, in Input) (Result, error) {
	if l == nil || l.Client == nil || l.Client.URL() == "" {
		return Result{Analyzer: "languagetool", Skipped: true}, nil
	}
	lang := in.Language
	if lang == "" {
		lang = l.Language
	}
	if lang == "" {
		lang = "ru-RU"
	}
	findings, err := l.Client.Check(ctx, in.Text, lang, languageTool.Options{})
	if err != nil {
		return Result{Analyzer: "languagetool", Error: err.Error()}, nil
	}
	return Result{Analyzer: "languagetool", Findings: findings}, nil
}

// ValeAnalyzer адаптирует внешний CLI. Отсутствие Vale является skip, а не
// ошибкой всего редактора: сервис можно запускать в минимальном контейнере.
type ValeAnalyzer struct{ Runner *vale.Runner }

func (v *ValeAnalyzer) Name() string { return "vale" }

func (v *ValeAnalyzer) Health(context.Context) error {
	if v == nil || v.Runner == nil {
		return errors.New("Vale не настроен")
	}
	if _, err := exec.LookPath(v.Runner.Binary); err != nil {
		return vale.ErrNotInstalled
	}
	return nil
}

func (v *ValeAnalyzer) Analyze(ctx context.Context, in Input) (Result, error) {
	if v == nil || v.Runner == nil {
		return Result{Analyzer: "vale", Skipped: true}, nil
	}
	findings, err := v.Runner.Check(ctx, in.Text)
	if err != nil {
		if errors.Is(err, vale.ErrNotInstalled) {
			return Result{Analyzer: "vale", Skipped: true, Error: err.Error()}, nil
		}
		return Result{Analyzer: "vale", Error: err.Error()}, nil
	}
	return Result{Analyzer: "vale", Findings: findings}, nil
}

// NatashaAnalyzer подключает Razdel/Natasha sidecar. Неработающий sidecar
// явно помечается как skipped/error и не превращает текст в «проверенный».
type NatashaAnalyzer struct {
	Client  *natasha.Client
	Game    string
	Profile string
}

// MarkdownlintAnalyzer проверяет только разметку и никогда не меняет текст.
type MarkdownlintAnalyzer struct{ Runner *markdownlint.Runner }

func (m *MarkdownlintAnalyzer) Name() string { return "markdownlint" }

func (m *MarkdownlintAnalyzer) Health(context.Context) error {
	if m == nil || m.Runner == nil {
		return errors.New("markdownlint не настроен")
	}
	if _, err := exec.LookPath(m.Runner.Binary); err != nil {
		return markdownlint.ErrNotInstalled
	}
	return nil
}

func (m *MarkdownlintAnalyzer) Analyze(ctx context.Context, in Input) (Result, error) {
	if m == nil || m.Runner == nil {
		return Result{Analyzer: "markdownlint", Skipped: true, Error: "markdownlint не настроен"}, nil
	}
	findings, err := m.Runner.Check(ctx, in.Text)
	if err != nil {
		if errors.Is(err, markdownlint.ErrNotInstalled) {
			return Result{Analyzer: "markdownlint", Skipped: true, Error: err.Error()}, nil
		}
		return Result{Analyzer: "markdownlint", Error: err.Error()}, nil
	}
	return Result{Analyzer: "markdownlint", Findings: findings}, nil
}

// HunspellAnalyzer выдаёт подсказки, но не исправляет текст автоматически.
type HunspellAnalyzer struct{ Runner *hunspell.Runner }

func (h *HunspellAnalyzer) Name() string { return "hunspell" }

func (h *HunspellAnalyzer) Health(context.Context) error {
	if h == nil || h.Runner == nil {
		return errors.New("Hunspell не настроен")
	}
	if _, err := exec.LookPath(h.Runner.Binary); err != nil {
		return hunspell.ErrNotInstalled
	}
	return nil
}

func (h *HunspellAnalyzer) Analyze(ctx context.Context, in Input) (Result, error) {
	if h == nil || h.Runner == nil {
		return Result{Analyzer: "hunspell", Skipped: true, Error: "Hunspell не настроен"}, nil
	}
	findings, err := h.Runner.Check(ctx, in.Text)
	if err != nil {
		if errors.Is(err, hunspell.ErrNotInstalled) || errors.Is(err, hunspell.ErrDictionaryUnavailable) {
			return Result{Analyzer: "hunspell", Skipped: true, Error: err.Error()}, nil
		}
		return Result{Analyzer: "hunspell", Error: err.Error()}, nil
	}
	return Result{Analyzer: "hunspell", Findings: findings}, nil
}

func (n *NatashaAnalyzer) Name() string { return "natasha-razdel" }

func (n *NatashaAnalyzer) Health(ctx context.Context) error {
	if n == nil || n.Client == nil {
		return errors.New("NLP-сайдкар не настроен")
	}
	return n.Client.Health(ctx)
}

func (n *NatashaAnalyzer) Analyze(ctx context.Context, in Input) (Result, error) {
	if n == nil || n.Client == nil || n.Client.URL() == "" {
		return Result{Analyzer: "natasha-razdel", Skipped: true, Error: "NATASHA_URL не задан"}, nil
	}
	game, profile := in.Game, in.Profile
	if game == "" {
		game = n.Game
	}
	if profile == "" {
		profile = n.Profile
	}
	out, err := n.Client.Analyze(ctx, natasha.Input{Text: in.Text, Language: in.Language, Game: game, Profile: profile})
	if err != nil {
		return Result{Analyzer: "natasha-razdel", Error: err.Error()}, nil
	}
	return Result{Analyzer: "natasha-razdel", Findings: out.Findings, Metrics: map[string]any{"sentences": len(out.Sentences), "tokens": len(out.Tokens), "entities": len(out.Entities), "cached": out.Cached}}, nil
}

type limitedBuffer struct {
	bytes.Buffer
	max int64
}

func (b *limitedBuffer) Write(p []byte) (int, error) {
	if b.max > 0 && int64(b.Len()+len(p)) > b.max {
		return 0, io.ErrShortBuffer
	}
	return b.Buffer.Write(p)
}

func stringValue(m map[string]any, key string) string {
	v, _ := m[key].(string)
	return v
}
func intValue(m map[string]any, key string) int {
	switch v := m[key].(type) {
	case int:
		return v
	case float64:
		return int(v)
	}
	return 0
}
func severityValue(m map[string]any) string {
	if v := stringValue(m, "severity"); v != "" {
		return v
	}
	return "warning"
}
func truncate(s string, n int) string {
	if len(s) <= n {
		return s
	}
	return s[:n] + "…"
}
