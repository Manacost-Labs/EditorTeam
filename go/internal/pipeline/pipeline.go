// Package pipeline разделяет анализ, генерацию и QA редактора.
package pipeline

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"strings"

	"github.com/Manacost-Labs/EditorTeam/go/internal/analyzer"
	"github.com/Manacost-Labs/EditorTeam/go/internal/analyzers"
	"github.com/Manacost-Labs/EditorTeam/go/internal/guards"
	"github.com/Manacost-Labs/EditorTeam/go/internal/llm"
	"github.com/Manacost-Labs/EditorTeam/go/internal/rules"
)

const PromptVersion = "editorteam-go-v1"

type Request struct {
	Text             string           `json:"text"`
	Mode             string           `json:"mode"` // proofread | edit | rewrite
	Game             string           `json:"game,omitempty"`
	Profile          string           `json:"profile,omitempty"`
	Language         string           `json:"language,omitempty"`
	EditorialMode    string           `json:"editorial_mode,omitempty"`
	CurrentPatch     string           `json:"current_patch,omitempty"`
	CurrentMetaEpoch string           `json:"current_meta_epoch,omitempty"`
	Claims           []map[string]any `json:"source_claims,omitempty"`
}

type Scores struct {
	Clarity     int `json:"clarity"`
	Structure   int `json:"structure"`
	Usefulness  int `json:"usefulness"`
	Specificity int `json:"specificity"`
	Voice       int `json:"voice"`
	Accuracy    int `json:"accuracy"`
	Terminology int `json:"terminology"`
}

type Analysis struct {
	Thesis          string   `json:"thesis"`
	Audience        string   `json:"audience"`
	Genre           string   `json:"genre"`
	Paragraphs      []string `json:"paragraphs"`
	WeakSpots       []string `json:"weak_spots"`
	Repetitions     []string `json:"repetitions"`
	Unclear         []string `json:"unclear"`
	TemplatePhrases []string `json:"template_phrases"`
	MissingLinks    []string `json:"missing_links"`
	FactualRisks    []string `json:"factual_risks"`
}

type Result struct {
	Text                     string              `json:"text"`
	Mode                     string              `json:"mode"`
	Changes                  []Change            `json:"changes,omitempty"`
	FactualRisks             []string            `json:"factual_risks,omitempty"`
	QAFindings               []analyzers.Finding `json:"qa_findings,omitempty"`
	ProtectedEntitiesChanged []string            `json:"protected_entities_changed,omitempty"`
	Scores                   Scores              `json:"scores"`
	Accepted                 bool                `json:"accepted"`
	Provider                 string              `json:"provider,omitempty"`
	Model                    string              `json:"model,omitempty"`
	PromptVersion            string              `json:"prompt_version"`
	Analysis                 Analysis            `json:"analysis,omitempty"`
	Attempts                 int                 `json:"attempts"`
	ChecksComplete           bool                `json:"checks_complete"`
	SkippedAnalyzers         []string            `json:"skipped_analyzers,omitempty"`
}

type Change struct {
	Line   int    `json:"line"`
	Before string `json:"before,omitempty"`
	After  string `json:"after,omitempty"`
}

type Service struct {
	LLM              llm.Completer
	RulesClient      *analyzer.Client
	Analyzers        []analyzers.Analyzer
	Provider         string
	AllowUnavailable bool
}

// Health возвращает состояние каждого подключенного анализатора. Неисправный
// optional tool не роняет /health всего сервиса, но его статус виден клиенту.
func (s *Service) Health(ctx context.Context) map[string]string {
	result := map[string]string{}
	for _, check := range s.Analyzers {
		if check == nil {
			continue
		}
		if err := check.Health(ctx); err != nil {
			result[check.Name()] = err.Error()
		} else {
			result[check.Name()] = "ok"
		}
	}
	return result
}

func New(l llm.Completer, rulesClient *analyzer.Client, provider string, checks ...analyzers.Analyzer) *Service {
	return &Service{LLM: l, RulesClient: rulesClient, Provider: provider, Analyzers: checks}
}

func (s *Service) SetAllowUnavailable(allow bool) { s.AllowUnavailable = allow }

func (s *Service) Run(ctx context.Context, req Request) (*Result, error) {
	if strings.TrimSpace(req.Text) == "" {
		return nil, errors.New("поле text пустое")
	}
	mode := normalizeMode(req.Mode)
	if mode == "" {
		return nil, fmt.Errorf("неизвестный mode %q: proofread, edit или rewrite", req.Mode)
	}
	if req.Game == "" {
		req.Game = "hearthstone"
	}
	if req.EditorialMode == "" {
		req.EditorialMode = "GUIDE"
	}
	if req.Language == "" {
		req.Language = "ru-RU"
	}

	var pyRules *analyzer.Rules
	if s.RulesClient != nil {
		got, err := s.RulesClient.RulesWithContext(ctx, req.Game, req.Profile, analyzer.RulesContext{Mode: req.EditorialMode, Depth: depthFor(mode), Text: req.Text})
		if err != nil {
			return nil, fmt.Errorf("правила: %w", err)
		}
		pyRules = got
	}
	result := &Result{Text: req.Text, Mode: mode, Provider: s.Provider, PromptVersion: PromptVersion}
	if s.LLM != nil {
		result.Model = s.LLM.Model()
	}

	protected := guards.Extract(req.Text)
	claims := req.Claims
	if len(claims) == 0 {
		claims = claimsFromEntities(protected)
	}
	bundle := rules.Build(pyRules, req.EditorialMode, depthFor(mode), req.Language, claims)
	for _, entity := range protected {
		if len(bundle.ProtectedEntities) >= 64 {
			break
		}
		bundle.ProtectedEntities = append(bundle.ProtectedEntities, entity.Kind+": "+entity.Value)
	}
	preFindings, preComplete, preSkipped := s.runChecks(ctx, analyzers.Input{Text: req.Text, Game: req.Game, Profile: req.Profile, Mode: req.EditorialMode, Depth: depthFor(mode), Language: req.Language, CurrentPatch: req.CurrentPatch, CurrentMeta: req.CurrentMetaEpoch, ClaimsBefore: claims})
	result.QAFindings = append(result.QAFindings, preFindings...)
	result.ChecksComplete = preComplete
	result.SkippedAnalyzers = append(result.SkippedAnalyzers, preSkipped...)
	if len(preFindings) > 0 {
		qa := make([]string, 0, len(preFindings))
		for _, item := range preFindings {
			qa = append(qa, item.Message)
		}
		bundle = bundle.WithQA(qa)
	}
	analysis := s.editorialAnalysis(ctx, req, bundle)
	result.Analysis = analysis
	result.FactualRisks = append(result.FactualRisks, analysis.FactualRisks...)
	candidate := req.Text
	var err error
	if s.LLM != nil {
		candidate, err = s.rewrite(ctx, req, bundle, analysis)
		if err != nil {
			return nil, err
		}
	}
	result.Attempts = 1

	// Critic и targeted repair не больше двух циклов. Critic не переписывает
	// текст: он возвращает только оценки и адреса проблем.
	for cycle := 0; cycle < 2 && s.LLM != nil; cycle++ {
		critic, err := s.critic(ctx, req, bundle, candidate)
		if err != nil {
			return nil, err
		}
		result.Scores = critic.Scores
		result.QAFindings = append(result.QAFindings, critic.Findings...)
		if len(critic.Findings) == 0 || mode == "proofread" {
			break
		}
		candidate, err = s.repair(ctx, req, bundle, candidate, critic.Findings)
		if err != nil {
			return nil, err
		}
		result.Attempts++
	}
	if result.Scores == (Scores{}) {
		result.Scores = Scores{Clarity: 5, Structure: 5, Usefulness: 5, Specificity: 5, Voice: 5, Accuracy: 5, Terminology: 5}
	}
	result.Text = candidate
	result.Changes = diff(req.Text, candidate)

	postFindings, postComplete, postSkipped := s.runChecks(ctx, analyzers.Input{Text: candidate, Before: req.Text, After: candidate, Game: req.Game, Profile: req.Profile, Mode: req.EditorialMode, Depth: depthFor(mode), Language: req.Language, CurrentPatch: req.CurrentPatch, CurrentMeta: req.CurrentMetaEpoch, ClaimsBefore: claims, ClaimsAfter: claims})
	result.QAFindings = append(result.QAFindings, postFindings...)
	if !postComplete {
		result.ChecksComplete = false
	}
	result.SkippedAnalyzers = appendUnique(result.SkippedAnalyzers, postSkipped...)
	guard := guards.Compare(req.Text, candidate)
	for _, item := range guard.Changed {
		result.ProtectedEntitiesChanged = append(result.ProtectedEntitiesChanged, item)
	}
	for _, item := range guard.Risks {
		result.FactualRisks = append(result.FactualRisks, item)
	}
	for _, item := range guard.Missing {
		result.FactualRisks = append(result.FactualRisks, "пропало "+item.Kind+": "+item.Value)
	}
	for _, item := range guard.Added {
		result.FactualRisks = append(result.FactualRisks, "появилось новое "+item)
	}
	result.Accepted = !guard.HasHardChanges() && !hasHardFinding(result.QAFindings)
	if !result.ChecksComplete && !s.AllowUnavailable {
		result.Accepted = false
	}
	if !result.Accepted {
		result.Text = req.Text
		result.Changes = nil
	}
	return result, nil
}

func (s *Service) runChecks(ctx context.Context, in analyzers.Input) ([]analyzers.Finding, bool, []string) {
	complete := true
	var findings []analyzers.Finding
	var skipped []string
	for _, check := range s.Analyzers {
		if check == nil {
			continue
		}
		checkResult, err := check.Analyze(ctx, in)
		if err != nil {
			complete = false
			findings = append(findings, analyzers.Finding{Analyzer: check.Name(), RuleID: "analyzer_unavailable", Severity: "info", Message: err.Error(), Tags: []string{"analyzer_unavailable"}})
			skipped = append(skipped, check.Name())
			continue
		}
		for _, item := range checkResult.Findings {
			item.Severity = normalizeSeverity(item.Severity)
			if item.Evidence == "" {
				item.Evidence = item.Context
			}
			findings = append(findings, item)
		}
		if checkResult.Skipped || checkResult.Error != "" {
			complete = false
			skipped = append(skipped, check.Name())
			message := checkResult.Error
			if message == "" {
				message = "проверка пропущена"
			}
			findings = append(findings, analyzers.Finding{Analyzer: checkResult.Analyzer, RuleID: "analyzer_unavailable", Severity: "info", Message: message, Tags: []string{"analyzer_unavailable"}})
		}
	}
	return findings, complete, uniqueStrings(skipped)
}

func normalizeSeverity(value string) string {
	switch strings.ToLower(strings.TrimSpace(value)) {
	case "blocker", "fatal":
		return "blocker"
	case "error":
		return "error"
	case "warning", "likely", "review", "suggestion":
		return "warning"
	default:
		return "info"
	}
}

func hasHardFinding(items []analyzers.Finding) bool {
	for _, item := range items {
		if item.Severity == "error" || item.Severity == "blocker" || item.Severity == "fatal" {
			return true
		}
	}
	return false
}

func uniqueStrings(values []string) []string {
	seen := map[string]struct{}{}
	out := []string{}
	for _, value := range values {
		if value == "" {
			continue
		}
		if _, ok := seen[value]; ok {
			continue
		}
		seen[value] = struct{}{}
		out = append(out, value)
	}
	return out
}

func appendUnique(values []string, more ...string) []string {
	return uniqueStrings(append(values, more...))
}

type criticResult struct {
	Scores   Scores              `json:"scores"`
	Findings []analyzers.Finding `json:"findings"`
}

func (s *Service) editorialAnalysis(ctx context.Context, req Request, bundle rules.RuleBundle) Analysis {
	if s.LLM == nil {
		return Analysis{}
	}
	text, err := s.complete(ctx, []llm.Message{{Role: "system", Content: bundle.Prompt() + "\nПроанализируй текст, но не переписывай его. Верни только JSON с thesis, audience, genre, paragraphs, weak_spots, repetitions, unclear, template_phrases, missing_links и factual_risks."}, {Role: "user", Content: req.Text}})
	if err != nil {
		return Analysis{FactualRisks: []string{"анализ не разобран: " + err.Error()}}
	}
	var out Analysis
	if json.Unmarshal([]byte(stripJSON(text)), &out) != nil {
		out = Analysis{WeakSpots: []string{"модель вернула анализ не в JSON"}}
	}
	return out
}

func (s *Service) rewrite(ctx context.Context, req Request, bundle rules.RuleBundle, analysis Analysis) (string, error) {
	raw, _ := json.Marshal(analysis)
	system := bundle.Prompt() + "\nАНАЛИЗ (не добавляй факты из него):\n" + string(raw) + "\nВерни только готовый текст. Режим " + req.Mode + ". Сохрани названия, числа, ссылки, отрицания, осторожность, разметку и голос автора. Не добавляй новые карты, факты или выводы."
	return s.complete(ctx, []llm.Message{{Role: "system", Content: system}, {Role: "user", Content: req.Text}})
}

func (s *Service) critic(ctx context.Context, req Request, bundle rules.RuleBundle, candidate string) (criticResult, error) {
	text, err := s.complete(ctx, []llm.Message{{Role: "system", Content: bundle.Prompt() + "\nТы critic. Не переписывай текст. Верни JSON {scores:{clarity,structure,usefulness,specificity,voice,accuracy,terminology}, findings:[{rule_id,severity,message,line}]} . Указывай только конкретные исправимые места."}, {Role: "user", Content: candidate}})
	if err != nil {
		return criticResult{}, err
	}
	var out criticResult
	if err := json.Unmarshal([]byte(stripJSON(text)), &out); err != nil {
		return criticResult{}, nil
	}
	return out, nil
}

func (s *Service) repair(ctx context.Context, req Request, bundle rules.RuleBundle, candidate string, findings []analyzers.Finding) (string, error) {
	raw, _ := json.Marshal(findings)
	qa := make([]string, 0, len(findings))
	for _, item := range findings {
		qa = append(qa, item.Message)
	}
	return s.complete(ctx, []llm.Message{{Role: "system", Content: bundle.WithQA(qa).Prompt() + "\nИсправь только отмеченные места, не переписывай весь текст и не меняй факты. Верни полный текст без пояснений. QA_FINDINGS: " + string(raw)}, {Role: "user", Content: candidate}})
}

func (s *Service) complete(ctx context.Context, messages []llm.Message) (string, error) {
	return s.LLM.Complete(ctx, messages, 0)
}
func normalizeMode(v string) string {
	switch strings.ToLower(strings.TrimSpace(v)) {
	case "proofread", "легкая", "лёгкая":
		return "proofread"
	case "edit", "обычная", "глубокая":
		return "edit"
	case "rewrite", "переплавка":
		return "rewrite"
	}
	return ""
}
func depthFor(mode string) string {
	if mode == "rewrite" {
		return "переплавка"
	}
	return "обычная"
}
func stripJSON(s string) string {
	s = strings.TrimSpace(s)
	if i := strings.Index(s, "{"); i >= 0 {
		if j := strings.LastIndex(s, "}"); j > i {
			return s[i : j+1]
		}
	}
	return s
}
func claimsFromEntities(entities []guards.Entity) []map[string]any {
	claims := make([]map[string]any, 0, len(entities))
	for i, entity := range entities {
		claims = append(claims, map[string]any{
			"claim_id":   fmt.Sprintf("protected-%d", i+1),
			"meaning":    map[string]any{"entity": entity.Value, "kind": entity.Kind},
			"confidence": "high",
		})
	}
	return claims
}
func diff(before, after string) []Change {
	if before == after {
		return nil
	}
	b, a := strings.Split(before, "\n"), strings.Split(after, "\n")
	n := len(b)
	if len(a) > n {
		n = len(a)
	}
	out := []Change{}
	for i := 0; i < n; i++ {
		var x, y string
		if i < len(b) {
			x = b[i]
		}
		if i < len(a) {
			y = a[i]
		}
		if x != y {
			out = append(out, Change{Line: i + 1, Before: x, After: y})
		}
	}
	return out
}
