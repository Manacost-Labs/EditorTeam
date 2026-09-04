// Package pipeline разделяет анализ, генерацию и QA редактора.
//
// Последовательность одного запроса:
//
//	preflight исходника → draft edit → postflight кандидата → source-aware critic
//	→ объединение findings → targeted repair → повторный postflight → повторный
//	critic → final guards → acceptance.
//
// Repair выполняется не больше двух раз и получает только актуальные,
// исправимые findings последнего postflight и critic.
package pipeline

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"log/slog"
	"math"
	"net"
	"os"
	"strings"
	"time"

	"github.com/Manacost-Labs/EditorTeam/go/internal/analyzer"
	"github.com/Manacost-Labs/EditorTeam/go/internal/analyzers"
	"github.com/Manacost-Labs/EditorTeam/go/internal/guards"
	"github.com/Manacost-Labs/EditorTeam/go/internal/llm"
	"github.com/Manacost-Labs/EditorTeam/go/internal/rules"
)

const PromptVersion = "editorteam-go-v2"

// MaxRepairs ограничивает число targeted repair за один запрос.
const MaxRepairs = 2

// Причины отклонения результата. Клиент получает их в rejection_reasons.
const (
	ReasonCriticInvalid          = "critic_invalid_response"
	ReasonCriticUnavailable      = "critic_unavailable"
	ReasonCriticTimeout          = "critic_timeout"
	ReasonCriticRejected         = "critic_rejected"
	ReasonChecksIncomplete       = "checks_incomplete"
	ReasonProtectedEntityChanged = "protected_entity_changed"
	ReasonHardFinding            = "hard_finding"
	ReasonRepairExhausted        = "repair_exhausted"
	ReasonNoImprovement          = "no_measurable_improvement"
)

// Status описывает исход запроса независимо от причин отклонения.
const (
	StatusEdited    = "edited"
	StatusUnchanged = "unchanged"
	StatusRejected  = "rejected"
	StatusDryRun    = "dry_run"
)

type requestIDKey struct{}

// WithRequestID кладёт идентификатор запроса в контекст для structured logging.
func WithRequestID(ctx context.Context, id string) context.Context {
	if id == "" {
		return ctx
	}
	return context.WithValue(ctx, requestIDKey{}, id)
}

// RequestIDFrom возвращает идентификатор запроса из контекста или пустую строку.
func RequestIDFrom(ctx context.Context) string {
	id, _ := ctx.Value(requestIDKey{}).(string)
	return id
}

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

// Scores — оценки critic по шкале 0–10. Они заполняются только из валидного
// ответа critic; ScoresValid в Result отличает настоящую оценку от нулей.
type Scores struct {
	FactualPreservation int `json:"factual_preservation"`
	MeaningPreservation int `json:"meaning_preservation"`
	Clarity             int `json:"clarity"`
	Structure           int `json:"structure"`
	Usefulness          int `json:"usefulness"`
	NaturalRussian      int `json:"natural_russian"`
	AuthorVoice         int `json:"author_voice"`
	Terminology         int `json:"terminology"`
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

// Improvement — конкретное улучшение, которое critic смог назвать. Без
// хотя бы одного улучшения изменённый текст не принимается.
type Improvement struct {
	Category string `json:"category"`
	Before   string `json:"before,omitempty"`
	After    string `json:"after,omitempty"`
	Reason   string `json:"reason"`
}

type Result struct {
	Text                     string              `json:"text"`
	Mode                     string              `json:"mode"`
	Status                   string              `json:"status"`
	Changes                  []Change            `json:"changes,omitempty"`
	FactualRisks             []string            `json:"factual_risks,omitempty"`
	QAFindings               []analyzers.Finding `json:"qa_findings,omitempty"`
	ProtectedEntitiesChanged []string            `json:"protected_entities_changed,omitempty"`
	Scores                   Scores              `json:"scores"`
	ScoresValid              bool                `json:"scores_valid"`
	CriticVerdict            string              `json:"critic_verdict,omitempty"`
	Improvements             []Improvement       `json:"improvements,omitempty"`
	Regressions              []string            `json:"regressions,omitempty"`
	Accepted                 bool                `json:"accepted"`
	RejectionReasons         []string            `json:"rejection_reasons,omitempty"`
	Provider                 string              `json:"provider,omitempty"`
	Model                    string              `json:"model,omitempty"`
	PromptVersion            string              `json:"prompt_version"`
	PromptVariant            string              `json:"prompt_variant"`
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
	PromptVariant    string
	AllowUnavailable bool
	// Log получает только служебные поля стадий: request_id, stage, provider,
	// model, duration_ms, error_kind, attempt. Текст статьи не логируется.
	Log *slog.Logger
}

func (s *Service) logger() *slog.Logger {
	if s.Log != nil {
		return s.Log
	}
	return slog.New(slog.NewTextHandler(io.Discard, nil))
}

func (s *Service) logStage(ctx context.Context, stage string, start time.Time, attempt int, errKind string) {
	model := ""
	if s.LLM != nil {
		model = s.LLM.Model()
	}
	s.logger().Info("stage",
		"request_id", RequestIDFrom(ctx), "stage", stage, "provider", s.Provider, "model", model,
		"duration_ms", time.Since(start).Milliseconds(), "error_kind", errKind, "attempt", attempt)
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

func (s *Service) HealthDetails(ctx context.Context) map[string]analyzers.HealthDetail {
	result := map[string]analyzers.HealthDetail{}
	for _, check := range s.Analyzers {
		detailed, ok := check.(analyzers.DetailedHealthAnalyzer)
		if !ok {
			continue
		}
		result[check.Name()] = detailed.DetailedHealth(ctx)
	}
	return result
}

func New(l llm.Completer, rulesClient *analyzer.Client, provider string, checks ...analyzers.Analyzer) *Service {
	return &Service{LLM: l, RulesClient: rulesClient, Provider: provider, PromptVariant: "candidate", Analyzers: checks}
}

func (s *Service) SetAllowUnavailable(allow bool) { s.AllowUnavailable = allow }

func (s *Service) SetPromptVariant(variant string) { s.PromptVariant = variant }

// Run выполняет полный цикл. Ошибка возвращается только когда безопасно
// вернуть исходник нельзя: пустой текст, неизвестный режим, недоступные
// правила или отказ модели на этапе draft. Невалидный critic, неудачный
// repair и любые findings превращаются в отклонение с исходным текстом.
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
	result := &Result{Text: req.Text, Mode: mode, Provider: s.Provider, PromptVersion: PromptVersion, PromptVariant: s.PromptVariant}
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
	base := analyzers.Input{Game: req.Game, Profile: req.Profile, Mode: req.EditorialMode, Depth: depthFor(mode), Language: req.Language, CurrentPatch: req.CurrentPatch, CurrentMeta: req.CurrentMetaEpoch, ClaimsBefore: claims}

	// 1. Preflight исходника: находки видны модели, но только исправимые.
	preInput := base
	preInput.Text = req.Text
	pre := s.runChecks(ctx, preInput)
	result.QAFindings = append(result.QAFindings, pre.findings...)
	result.ChecksComplete = pre.complete
	result.SkippedAnalyzers = append(result.SkippedAnalyzers, pre.skipped...)
	if qa := messagesOf(repairable(pre.findings)); len(qa) > 0 {
		bundle = bundle.WithQA(qa)
	}

	// 2. Editorial analysis и draft.
	analysis := s.editorialAnalysis(ctx, req, bundle)
	result.Analysis = analysis
	result.FactualRisks = append(result.FactualRisks, analysis.FactualRisks...)
	candidate := req.Text
	if s.LLM != nil {
		started := time.Now()
		draft, err := s.rewrite(ctx, req, bundle, analysis)
		if err != nil {
			s.logStage(ctx, "draft", started, 1, errorKind(ctx, err))
			return nil, err
		}
		s.logStage(ctx, "draft", started, 1, "")
		candidate = draft
	}
	result.Attempts = 1

	// 3. Postflight → critic → repair, не больше MaxRepairs циклов repair.
	var (
		post     checkRun
		guard    guards.Report
		critic   criticResult
		criticOK bool
		reasons  []string
		repairs  int
	)
	for {
		postInput := base
		postInput.Text, postInput.Before, postInput.After, postInput.ClaimsAfter = candidate, req.Text, candidate, claims
		started := time.Now()
		post = s.runChecks(ctx, postInput)
		s.logStage(ctx, "postflight", started, result.Attempts, "")
		guard = guards.Compare(req.Text, candidate)
		findings := append(append([]analyzers.Finding{}, post.findings...), guardFindings(guard)...)

		criticOK = true
		if s.LLM != nil {
			var cerr *criticError
			critic, cerr = s.critic(ctx, bundle, criticInput{
				Source: req.Text, Candidate: candidate, Diff: diff(req.Text, candidate), Mode: mode,
				Analysis: analysis, SourceClaims: bundle.SourceClaims, ProtectedEntities: bundle.ProtectedEntities,
				ToolFindings: repairable(findings),
			}, result.Attempts)
			if cerr != nil {
				criticOK = false
				reasons = append(reasons, cerr.Kind)
				break
			}
			findings = append(findings, critic.Findings...)
			if critic.Verdict == "reject" {
				reasons = append(reasons, ReasonCriticRejected)
				break
			}
		}
		todo := repairable(findings)
		needRepair := len(todo) > 0 || (s.LLM != nil && critic.RepairRequired)
		if s.LLM == nil || mode == "proofread" || !needRepair {
			break
		}
		if repairs >= MaxRepairs {
			if critic.RepairRequired || hasHardFinding(todo) {
				reasons = append(reasons, ReasonRepairExhausted)
			}
			break
		}
		started = time.Now()
		repaired, err := s.repair(ctx, bundle, req.Text, candidate, todo)
		if err != nil {
			s.logStage(ctx, "repair", started, result.Attempts+1, errorKind(ctx, err))
			if critic.RepairRequired || hasHardFinding(todo) {
				reasons = append(reasons, ReasonRepairExhausted)
			}
			break
		}
		repairs++
		result.Attempts++
		s.logStage(ctx, "repair", started, result.Attempts, "")
		candidate = repaired
	}

	// 4. Final guards и acceptance.
	result.QAFindings = mergeFindings(append(append(append(result.QAFindings, post.findings...), guardFindings(guard)...), critic.Findings...))
	if !post.complete {
		result.ChecksComplete = false
	}
	result.SkippedAnalyzers = appendUnique(result.SkippedAnalyzers, post.skipped...)
	result.ProtectedEntitiesChanged = append(result.ProtectedEntitiesChanged, guard.Changed...)
	result.FactualRisks = append(result.FactualRisks, guard.Risks...)
	for _, item := range guard.Missing {
		result.FactualRisks = append(result.FactualRisks, "пропало "+item.Kind+": "+item.Value)
	}
	for _, item := range guard.Added {
		result.FactualRisks = append(result.FactualRisks, "появилось новое "+item)
	}
	if s.LLM != nil && criticOK {
		result.Scores = critic.Scores
		result.ScoresValid = true
		result.CriticVerdict = critic.Verdict
		result.Improvements = critic.Improvements
		result.Regressions = critic.Regressions
		if hasHardFinding(critic.Findings) {
			reasons = append(reasons, ReasonCriticRejected)
		}
		// Принимается только последний verdict accept без repair_required.
		if critic.Verdict == "repair" || critic.RepairRequired {
			reasons = append(reasons, ReasonRepairExhausted)
		}
		// Изменённый текст без названного улучшения — не улучшение.
		if candidate != req.Text && len(critic.Improvements) == 0 {
			reasons = append(reasons, ReasonNoImprovement)
		}
	}
	if s.LLM != nil && !criticOK {
		// Без валидной оценки результат нельзя считать проверенным.
		result.ChecksComplete = false
	}
	if s.LLM != nil && candidate == req.Text {
		// Модель не нашла, что править: это честный результат, но не правка.
		reasons = append(reasons, ReasonNoImprovement)
	}
	if guard.HasHardChanges() {
		reasons = append(reasons, ReasonProtectedEntityChanged)
	}
	if hasHardFinding(post.findings) {
		reasons = append(reasons, ReasonHardFinding)
	}
	if !result.ChecksComplete && !s.AllowUnavailable {
		reasons = append(reasons, ReasonChecksIncomplete)
	}
	result.RejectionReasons = uniqueStrings(reasons)
	// Dry-run без модели никогда не выдаёт accepted=true: правки не было.
	result.Accepted = s.LLM != nil && len(result.RejectionReasons) == 0
	switch {
	case s.LLM == nil:
		result.Status = StatusDryRun
	case result.Accepted:
		result.Status = StatusEdited
	case candidate == req.Text:
		result.Status = StatusUnchanged
	case len(result.RejectionReasons) == 1 && result.RejectionReasons[0] == ReasonNoImprovement:
		result.Status = StatusUnchanged
	default:
		result.Status = StatusRejected
	}
	if result.Accepted {
		result.Text = candidate
		result.Changes = diff(req.Text, candidate)
	} else {
		result.Text = req.Text
		result.Changes = nil
	}
	return result, nil
}

// errorKind классифицирует сбой модели: таймаут контекста или клиента —
// critic_timeout, всё остальное (сеть, HTTP-ошибка, пустой ответ) —
// critic_unavailable. Для не-critic стадий имена те же, чтобы логи были
// однородными.
func errorKind(ctx context.Context, err error) string {
	if err == nil {
		return ""
	}
	if errors.Is(ctx.Err(), context.DeadlineExceeded) || errors.Is(err, context.DeadlineExceeded) || errors.Is(err, os.ErrDeadlineExceeded) {
		return ReasonCriticTimeout
	}
	var timeout interface{ Timeout() bool }
	if errors.As(err, &timeout) && timeout.Timeout() {
		return ReasonCriticTimeout
	}
	var netErr net.Error
	if errors.As(err, &netErr) && netErr.Timeout() {
		return ReasonCriticTimeout
	}
	return ReasonCriticUnavailable
}

type checkRun struct {
	findings []analyzers.Finding
	complete bool
	skipped  []string
}

func (s *Service) runChecks(ctx context.Context, in analyzers.Input) checkRun {
	run := checkRun{complete: true}
	for _, check := range s.Analyzers {
		if check == nil {
			continue
		}
		checkResult, err := check.Analyze(ctx, in)
		if err != nil {
			run.complete = false
			run.findings = append(run.findings, analyzers.Finding{Analyzer: check.Name(), RuleID: "analyzer_unavailable", Severity: "info", Message: err.Error(), Tags: []string{"analyzer_unavailable"}})
			run.skipped = append(run.skipped, check.Name())
			continue
		}
		degraded := false
		for _, item := range checkResult.Findings {
			if item.RuleID == "analyzer_degraded" {
				degraded = true
			}
			if item.Analyzer == "" {
				item.Analyzer = check.Name()
			}
			item.Severity = normalizeSeverity(item.Severity)
			if item.RuleID == "hunspell.unknown" {
				// Неизвестное словарю слово — справка редактору, а не правка:
				// авторский сленг и allowlist игры не должны «исправляться».
				item.Severity = "info"
			}
			if item.Evidence == "" {
				item.Evidence = item.Context
			}
			run.findings = append(run.findings, item)
		}
		if checkResult.Skipped || checkResult.Error != "" {
			run.complete = false
			run.skipped = append(run.skipped, check.Name())
			message := checkResult.Error
			if message == "" {
				message = "проверка пропущена"
			}
			if !degraded {
				run.findings = append(run.findings, analyzers.Finding{Analyzer: checkResult.Analyzer, RuleID: "analyzer_unavailable", Severity: "info", Message: message, Tags: []string{"analyzer_unavailable"}})
			}
		}
	}
	run.skipped = uniqueStrings(run.skipped)
	return run
}

// guardFindings превращает отчёт защиты сущностей в findings, чтобы repair
// мог вернуть пропавшее число или ссылку до финальной проверки.
func guardFindings(guard guards.Report) []analyzers.Finding {
	var out []analyzers.Finding
	for _, item := range guard.Changed {
		out = append(out, analyzers.Finding{Analyzer: "guards", RuleID: "protected_entity_changed", Severity: "error", Message: "изменена защищённая сущность: " + item + "; верните форму исходника", Evidence: item})
	}
	for _, item := range guard.Added {
		out = append(out, analyzers.Finding{Analyzer: "guards", RuleID: "protected_entity_added", Severity: "warning", Message: "появилась сущность, которой нет в исходнике: " + item, Evidence: item})
	}
	return out
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

// findingKey объединяет дубликаты: analyzer + rule_id + line + normalized evidence.
func findingKey(item analyzers.Finding) string {
	evidence := item.Evidence
	if evidence == "" {
		evidence = item.Context
	}
	if evidence == "" {
		evidence = item.Message
	}
	return strings.Join([]string{item.Analyzer, item.RuleID, fmt.Sprint(item.Line), strings.ToLower(guards.NormalizeWhitespace(evidence))}, "|")
}

func mergeFindings(items []analyzers.Finding) []analyzers.Finding {
	seen := map[string]struct{}{}
	out := make([]analyzers.Finding, 0, len(items))
	for _, item := range items {
		key := findingKey(item)
		if _, ok := seen[key]; ok {
			continue
		}
		seen[key] = struct{}{}
		out = append(out, item)
	}
	return out
}

// repairable оставляет только то, что модель может исправить: без
// analyzer_unavailable и analyzer_degraded, без информационных находок,
// без пустых сообщений и без дубликатов.
func repairable(items []analyzers.Finding) []analyzers.Finding {
	out := make([]analyzers.Finding, 0, len(items))
	for _, item := range mergeFindings(items) {
		if item.RuleID == "analyzer_unavailable" || item.RuleID == "analyzer_degraded" || hasTag(item, "analyzer_unavailable") || hasTag(item, "analyzer_degraded") {
			continue
		}
		if item.Severity == "info" || strings.TrimSpace(item.Message) == "" {
			continue
		}
		out = append(out, item)
	}
	return out
}

func hasTag(item analyzers.Finding, tag string) bool {
	for _, value := range item.Tags {
		if value == tag {
			return true
		}
	}
	return false
}

func messagesOf(items []analyzers.Finding) []string {
	out := make([]string, 0, len(items))
	for _, item := range items {
		out = append(out, item.Message)
	}
	return out
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

// criticInput — данные для critic. Они уходят JSON-ом в user message, а не
// инструкциями в system prompt.
type criticInput struct {
	Source            string              `json:"source"`
	Candidate         string              `json:"candidate"`
	Diff              []Change            `json:"diff"`
	Mode              string              `json:"mode"`
	Analysis          Analysis            `json:"analysis"`
	SourceClaims      []map[string]any    `json:"source_claims"`
	ProtectedEntities []string            `json:"protected_entities"`
	ToolFindings      []analyzers.Finding `json:"tool_findings"`
}

type criticResult struct {
	Verdict        string              `json:"verdict"`
	Scores         Scores              `json:"scores"`
	Improvements   []Improvement       `json:"improvements"`
	Regressions    []string            `json:"regressions"`
	Findings       []analyzers.Finding `json:"findings"`
	RepairRequired bool                `json:"repair_required"`
}

// criticError — типизированный сбой critic: Kind совпадает с причиной
// отклонения, которую увидит клиент.
type criticError struct {
	Kind string
	Err  error
}

func (e *criticError) Error() string { return e.Kind + ": " + e.Err.Error() }

const criticInstruction = "\nТы critic. Не переписывай текст и не предлагай свою версию статьи. " +
	"В сообщении пользователя JSON с полями source, candidate, diff, mode, analysis, source_claims, protected_entities и tool_findings: " +
	"оцени именно изменение candidate относительно source. Верни только JSON вида " +
	`{"verdict":"accept|repair|reject","scores":{"factual_preservation":0,"meaning_preservation":0,"clarity":0,"structure":0,"usefulness":0,"natural_russian":0,"author_voice":0,"terminology":0},"improvements":[{"category":"clarity","before":"","after":"","reason":""}],"regressions":[],"findings":[{"rule_id":"","severity":"info|warning|error|blocker","message":"","line":0}],"repair_required":false}` +
	". Каждая оценка — целое число от 0 до 10. improvements — конкретные места, где candidate лучше source, с причиной; " +
	"если текст изменён, но назвать улучшение нечем, оставь improvements пустым: такой candidate не примут. regressions — места, где candidate хуже source. " +
	"Правила согласованности: verdict accept требует repair_required=false и не допускает error или blocker; verdict repair требует repair_required=true и хотя бы одно исправимое finding; verdict reject — когда изменение нельзя принять. " +
	"В findings указывай только конкретные исправимые места; error и blocker — только для потери смысла, факта или защищённой сущности. Хороший текст не трогают: для него verdict accept с пустыми findings."

var scoreKeys = []string{"factual_preservation", "meaning_preservation", "clarity", "structure", "usefulness", "natural_russian", "author_voice", "terminology"}

// parseCritic строго разбирает ответ critic: JSON-объект, verdict из
// allowlist, все восемь оценок как целые числа в диапазоне 0–10.
func parseCritic(text string) (criticResult, error) {
	var raw struct {
		Verdict        string              `json:"verdict"`
		Scores         map[string]*float64 `json:"scores"`
		Improvements   []Improvement       `json:"improvements"`
		Regressions    []string            `json:"regressions"`
		Findings       []analyzers.Finding `json:"findings"`
		RepairRequired bool                `json:"repair_required"`
	}
	if err := json.Unmarshal([]byte(stripJSON(text)), &raw); err != nil {
		return criticResult{}, fmt.Errorf("ответ critic не JSON: %w", err)
	}
	out := criticResult{Regressions: raw.Regressions, RepairRequired: raw.RepairRequired}
	for _, item := range raw.Improvements {
		if strings.TrimSpace(item.Category) == "" || strings.TrimSpace(item.Reason) == "" {
			continue
		}
		out.Improvements = append(out.Improvements, item)
	}
	switch strings.ToLower(strings.TrimSpace(raw.Verdict)) {
	case "accept", "repair", "reject":
		out.Verdict = strings.ToLower(strings.TrimSpace(raw.Verdict))
	default:
		return criticResult{}, fmt.Errorf("verdict %q не из списка accept, repair, reject", raw.Verdict)
	}
	values := map[string]int{}
	for _, key := range scoreKeys {
		value, ok := raw.Scores[key]
		if !ok || value == nil {
			return criticResult{}, fmt.Errorf("оценка %s отсутствует", key)
		}
		if *value < 0 || *value > 10 || *value != math.Trunc(*value) {
			return criticResult{}, fmt.Errorf("оценка %s=%v вне диапазона 0–10", key, *value)
		}
		values[key] = int(*value)
	}
	out.Scores = Scores{
		FactualPreservation: values["factual_preservation"], MeaningPreservation: values["meaning_preservation"],
		Clarity: values["clarity"], Structure: values["structure"], Usefulness: values["usefulness"],
		NaturalRussian: values["natural_russian"], AuthorVoice: values["author_voice"], Terminology: values["terminology"],
	}
	for _, item := range raw.Findings {
		if strings.TrimSpace(item.Message) == "" {
			continue
		}
		item.Analyzer = "critic"
		item.Severity = normalizeSeverity(item.Severity)
		out.Findings = append(out.Findings, item)
	}
	// Согласованность verdict, repair_required и findings: противоречивый
	// ответ невалиден, а не «почти принят».
	switch out.Verdict {
	case "accept":
		if out.RepairRequired {
			return criticResult{}, errors.New("verdict accept противоречит repair_required=true")
		}
		if hasHardFinding(out.Findings) {
			return criticResult{}, errors.New("verdict accept противоречит error или blocker в findings")
		}
	case "repair":
		if !out.RepairRequired {
			return criticResult{}, errors.New("verdict repair требует repair_required=true")
		}
		if len(repairable(out.Findings)) == 0 {
			return criticResult{}, errors.New("verdict repair без исправимых findings")
		}
	}
	return out, nil
}

func (s *Service) editorialAnalysis(ctx context.Context, req Request, bundle rules.RuleBundle) Analysis {
	if s.LLM == nil {
		return Analysis{}
	}
	text, err := s.complete(ctx, []llm.Message{{Role: "system", Content: s.systemPrompt(bundle.Prompt() + "\nПроанализируй текст, но не переписывай его. Верни только JSON с thesis, audience, genre, paragraphs, weak_spots, repetitions, unclear, template_phrases, missing_links и factual_risks.")}, {Role: "user", Content: req.Text}})
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
	system := s.systemPrompt(bundle.Prompt() + "\nАНАЛИЗ (не добавляй факты из него):\n" + string(raw) + "\nВерни только готовый текст. Режим " + req.Mode + ". Сохрани названия, числа, ссылки, отрицания, осторожность, разметку и голос автора. Не добавляй новые карты, факты или выводы. Если править нечего, верни текст без изменений: хороший текст не трогают.")
	return s.complete(ctx, []llm.Message{{Role: "system", Content: system}, {Role: "user", Content: req.Text}})
}

// critic вызывает модель с JSON-данными в user message. Невалидный ответ
// повторяется один раз с текстом ошибки разбора; второй невалидный ответ —
// critic_invalid_response. Таймаут — critic_timeout, сетевой или HTTP-сбой —
// critic_unavailable. Во всех случаях pipeline возвращает исходник без
// HTTP-ошибки.
func (s *Service) critic(ctx context.Context, bundle rules.RuleBundle, in criticInput, attempt int) (criticResult, *criticError) {
	payload, err := json.Marshal(in)
	if err != nil {
		return criticResult{}, &criticError{Kind: ReasonCriticInvalid, Err: err}
	}
	messages := []llm.Message{
		{Role: "system", Content: s.systemPrompt(bundle.Prompt() + criticInstruction)},
		{Role: "user", Content: string(payload)},
	}
	started := time.Now()
	text, err := s.complete(ctx, messages)
	if err != nil {
		kind := errorKind(ctx, err)
		s.logStage(ctx, "critic", started, attempt, kind)
		return criticResult{}, &criticError{Kind: kind, Err: err}
	}
	out, parseErr := parseCritic(text)
	if parseErr == nil {
		s.logStage(ctx, "critic", started, attempt, "")
		return out, nil
	}
	s.logStage(ctx, "critic", started, attempt, ReasonCriticInvalid)
	retry := append(append([]llm.Message{}, messages...),
		llm.Message{Role: "assistant", Content: text},
		llm.Message{Role: "user", Content: "Ответ не разобран: " + parseErr.Error() + ". Верни только исправленный JSON по схеме из инструкции, без пояснений и без текста статьи."},
	)
	started = time.Now()
	text, err = s.complete(ctx, retry)
	if err != nil {
		kind := errorKind(ctx, err)
		s.logStage(ctx, "critic_retry", started, attempt, kind)
		return criticResult{}, &criticError{Kind: kind, Err: err}
	}
	out, parseErr = parseCritic(text)
	if parseErr != nil {
		s.logStage(ctx, "critic_retry", started, attempt, ReasonCriticInvalid)
		return criticResult{}, &criticError{Kind: ReasonCriticInvalid, Err: parseErr}
	}
	s.logStage(ctx, "critic_retry", started, attempt, "")
	return out, nil
}

// repair получает source, candidate и актуальные findings JSON-ом и должен
// вернуть полный текст, исправив только отмеченные места.
func (s *Service) repair(ctx context.Context, bundle rules.RuleBundle, source, candidate string, findings []analyzers.Finding) (string, error) {
	payload, err := json.Marshal(map[string]any{"source": source, "candidate": candidate, "findings": findings})
	if err != nil {
		return "", err
	}
	system := s.systemPrompt(bundle.WithQA(messagesOf(findings)).Prompt() +
		"\nИсправь в candidate только места из QA_FINDINGS: они переданы в поле findings JSON в сообщении пользователя. " +
		"Не переписывай весь текст и не меняй факты, числа, ссылки, отрицания и названия; source дан только для сверки. " +
		"Верни полный исправленный текст без пояснений.")
	return s.complete(ctx, []llm.Message{{Role: "system", Content: system}, {Role: "user", Content: string(payload)}})
}

func (s *Service) systemPrompt(prompt string) string {
	variant := s.PromptVariant
	if variant == "" {
		variant = "candidate"
	}
	instruction := "Следуй базовым правилам редакции без дополнительных эвристик."
	if variant == "candidate" {
		instruction = "Редактируй минимально: сначала сохрани факты, структуру Markdown и голос автора, затем улучшай ясность."
	}
	return "PROMPT_VARIANT: " + variant + "\n" + instruction + "\n" + prompt
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
