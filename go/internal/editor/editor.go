// Package editor — цикл правки с затвором.
//
// Модель правит текст, анализаторы проверяют результат. Правка принимается,
// только если сохранила смысл и защищённые элементы. Ритм, сокращение и
// локальные сигналы голоса внутри рабочего диапазона требуют просмотра, но
// не подменяют редакторское решение. Не прошла — модель получает конкретную
// причину и пробует снова.
//
// Это и есть смысл сервиса. Без затвора он был бы прокси к провайдеру.
package editor

import (
	"context"
	"encoding/json"
	"fmt"
	"net/url"
	"regexp"
	"strings"

	"github.com/Manacost-Labs/EditorTeam/go/internal/analyzer"
	"github.com/Manacost-Labs/EditorTeam/go/internal/llm"
)

// Глубина правки. Первые три — правка авторского текста с исходником как
// образцом; «переплавка» — пересборка плохого текста, где исходник только
// источник фактов, а образец голоса — корпус автора.
var Depths = []string{"лёгкая", "обычная", "глубокая", "переплавка"}

const (
	DepthDefault = "обычная"
	DepthRewrite = "переплавка"
)

// NormalizeDepth принимает «Переплавка», «легкая:» или «ЛЁГКАЯ» и возвращает
// каноническое слово. Второе значение false — глубина неизвестна.
func NormalizeDepth(value string) (string, bool) {
	raw := strings.ToLower(strings.Trim(strings.TrimSpace(value), " \t:.—–-"))
	folded := strings.ReplaceAll(raw, "ё", "е")
	for _, depth := range Depths {
		if folded == strings.ReplaceAll(depth, "ё", "е") {
			return depth, true
		}
	}
	return "", false
}

type Request struct {
	Text              string             `json:"text"`
	Game              string             `json:"game"`
	Profile           string             `json:"profile"`
	Mode              string             `json:"mode"`           // лёгкая | обычная | глубокая | переплавка
	EditorialMode     string             `json:"editorial_mode"` // GUIDE | ANALYSIS | REPORT
	EvidenceRequested bool               `json:"evidence_requested"`
	Claims            []map[string]any   `json:"claims,omitempty"`
	CurrentPatch      string             `json:"current_patch,omitempty"`
	CurrentMetaEpoch  string             `json:"current_meta_epoch,omitempty"`
	DeclaredMissing   []string           `json:"declared_missing,omitempty"` // разделы без материала в исходнике
	LLMContext        llm.RequestContext `json:"-"`
}

type Attempt struct {
	N          int                  `json:"n"`
	Accepted   bool                 `json:"accepted"`
	Violations []analyzer.Violation `json:"violations,omitempty"`
	Warnings   []analyzer.Violation `json:"warnings,omitempty"`
}

type Change struct {
	Kind   string `json:"kind"` // changed | added | removed | moved | omitted
	Line   int    `json:"line"`
	Before string `json:"before,omitempty"`
	After  string `json:"after,omitempty"`
}

type Result struct {
	Text             string            `json:"text"`
	Accepted         bool              `json:"accepted"`
	Attempts         []Attempt         `json:"attempts"`
	Changes          []Change          `json:"changes"`
	Preserved        []string          `json:"preserved"`
	Saved            bool              `json:"saved"`
	SaveStatus       string            `json:"save_status"`
	Verdict          *analyzer.Verdict `json:"verdict"`
	Report           *analyzer.Report  `json:"report"`
	Model            string            `json:"model"`
	Caveats          []string          `json:"caveats,omitempty"`
	SourceText       string            `json:"-"`
	GoogleDocumentID string            `json:"-"`
	ReviewPath       string            `json:"review_path,omitempty"`

	// Переплавка: глубина, план и разделы, для которых в исходнике не было
	// материала, — они честно не написаны, а не выдуманы.
	Depth           string            `json:"depth,omitempty"`
	Outline         *analyzer.Outline `json:"outline,omitempty"`
	MissingSections []string          `json:"missing_sections,omitempty"`
	MissingTitles   []string          `json:"missing_titles,omitempty"`
}

type Service struct {
	llm         llm.Completer
	an          *analyzer.Client
	maxAttempts int
}

func New(l llm.Completer, a *analyzer.Client, maxAttempts int) *Service {
	if maxAttempts < 1 {
		maxAttempts = 1
	}
	return &Service{llm: l, an: a, maxAttempts: maxAttempts}
}

// Edit прогоняет текст через цикл «правка → проверка → повтор».
func (s *Service) Edit(ctx context.Context, req Request) (*Result, error) {
	if req.EditorialMode == "" {
		req.EditorialMode = "GUIDE"
	}
	if req.Mode == "" {
		req.Mode = DepthDefault
	}
	if canon, ok := NormalizeDepth(req.Mode); ok {
		req.Mode = canon
	}
	rulesText := ""
	if req.Mode == DepthRewrite && !isGoogleDocumentURL(req.Text) {
		rulesText = req.Text // для подбора образцов манеры по темам исходника
	}
	rules, err := s.an.RulesWithContext(ctx, req.Game, req.Profile,
		analyzer.RulesContext{Mode: req.EditorialMode, Depth: req.Mode, Text: rulesText})
	if err != nil {
		return nil, fmt.Errorf("правила: %w", err)
	}

	res := &Result{
		Model:     s.llm.Model(),
		Changes:   []Change{},
		Preserved: []string{},
		Depth:     req.Mode,
	}
	if prov, ok := rules.Norms["provisional"].(bool); ok && prov {
		res.Caveats = append(res.Caveats,
			"нормы для этой игры заимствованы: оценки голоса и ритма — ориентир, не эталон")
	}

	claims := safeClaims(req.Claims)
	if req.Mode == DepthRewrite {
		return s.rewrite(ctx, req, rules, claims, res)
	}
	system := buildSystemPromptContext(rules, req.Mode, claims)
	if isGoogleDocumentURL(req.Text) && hasGoogleDocumentReadTool(req.LLMContext.Tools) {
		system += "\nИСТОЧНИК GOOGLE DOCS\n"
		system += "Ссылка в сообщении — это адрес исходного документа, а не текст для правки. "
		system += "Сначала вызови read_google_document по ID из ссылки. Затем верни весь текст документа целиком, "
		system += "без URL, служебного заголовка, каталога вкладок и пояснений. Не пересказывай и не сокращай документ.\n"
	}
	messages := []llm.Message{
		{Role: "system", Content: system},
		{Role: "user", Content: req.Text},
	}

	validationText := req.Text
	sourceLoaded := false
	googleURL := isGoogleDocumentURL(req.Text)
	for attempt := 1; attempt <= s.maxAttempts; attempt++ {
		out, err := s.complete(ctx, messages, req.LLMContext)
		if err != nil {
			return nil, fmt.Errorf("попытка %d: %w", attempt, err)
		}
		if out.SourceText != "" && !sourceLoaded && googleURL {
			validationText = out.SourceText
			sourceLoaded = true
			// Retries should repair the document prose, not ask the model to
			// fetch the URL again and risk another summary.
			messages[1].Content = validationText
		}
		if googleURL && !sourceLoaded {
			if attempt == s.maxAttempts {
				return nil, fmt.Errorf("не удалось получить содержимое Google Docs через выданный read-only grant")
			}
			messages = append(messages,
				llm.Message{Role: "assistant", Content: out.Text},
				llm.Message{Role: "user", Content: "Ссылка ведёт на Google Docs. Сначала обязательно вызови read_google_document, затем верни полный текст документа без служебной оболочки."},
			)
			continue
		}
		candidate := stripFence(out.Text)

		verdict, err := s.an.ValidateWithContext(ctx, validationText, candidate, req.Game, req.Profile,
			analyzer.ValidationContext{
				Mode: req.EditorialMode, EvidenceRequested: req.EvidenceRequested,
				ClaimsBefore: claims, ClaimsAfter: claims,
				CurrentPatch: req.CurrentPatch, CurrentMetaEpoch: req.CurrentMetaEpoch,
			})
		if err != nil {
			return nil, fmt.Errorf("проверка попытки %d: %w", attempt, err)
		}

		res.Attempts = append(res.Attempts, Attempt{
			N: attempt, Accepted: verdict.Accepted, Violations: verdict.Violations,
			Warnings: verdict.Warnings,
		})

		if verdict.Accepted {
			res.Text, res.Accepted, res.Verdict = candidate, true, verdict
			for _, warning := range verdict.Warnings {
				res.Caveats = append(res.Caveats, "нужна редакторская проверка: "+warning.Message)
			}
			break
		}

		if attempt == s.maxAttempts {
			// Отдаём исходник, а не последнюю неудачную правку: испорченный
			// текст хуже неправленого, и решать должен человек.
			res.Text, res.Accepted, res.Verdict = validationText, false, verdict
			res.Caveats = append(res.Caveats,
				"правка не прошла проверку за "+plural(s.maxAttempts)+
					" — возвращён исходный текст")
			break
		}

		messages = append(messages,
			llm.Message{Role: "assistant", Content: candidate},
			llm.Message{Role: "user", Content: retryPrompt(verdict.Violations)},
		)
	}

	report, err := s.an.AnalyzeWithMode(ctx, res.Text, req.Game, req.Profile,
		req.EditorialMode, req.EvidenceRequested)
	if err == nil {
		res.Report = report
	}
	res.Changes = summarizeChanges(validationText, res.Text)
	if res.Accepted {
		res.Preserved = preservedSummary(rules)
		if googleURL && sourceLoaded {
			res.SaveStatus = "результат возвращён в чат; сохранение в Google Docs ещё не подтверждено"
			res.SourceText = validationText
			res.GoogleDocumentID, _ = GoogleDocumentID(req.Text)
		} else {
			res.SaveStatus = "результат возвращён в чат; исходный текст не перезаписывался"
		}
	} else {
		res.Preserved = []string{"исходный текст возвращён без изменений"}
		res.SaveStatus = "правка не сохранена: проверка не пройдена, возвращён исходный текст"
	}
	return res, nil
}

type completionResult struct {
	Text       string
	SourceText string
}

func (s *Service) complete(ctx context.Context, messages []llm.Message, request llm.RequestContext) (completionResult, error) {
	if contextual, ok := s.llm.(llm.ContextCompleter); ok {
		result, err := contextual.CompleteWithContext(ctx, messages, 0, request)
		return completionResult{Text: result.Text, SourceText: result.SourceText}, err
	}
	text, err := s.llm.Complete(ctx, messages, 0)
	return completionResult{Text: text}, err
}

var googleDocumentURLPattern = regexp.MustCompile(`^/document/d/[A-Za-z0-9_-]+/edit/?$`)

func isGoogleDocumentURL(value string) bool {
	parsed, err := url.Parse(strings.TrimSpace(value))
	return err == nil && parsed.Scheme == "https" && parsed.Host == "docs.google.com" && googleDocumentURLPattern.MatchString(parsed.Path)
}

// GoogleDocumentURL reports whether a value is a canonical Google Docs
// editor URL. Query parameters are ignored for detection; the document id is
// still extracted by the connector, never fetched by this service.
func GoogleDocumentURL(value string) bool {
	return isGoogleDocumentURL(value)
}

// GoogleDocumentID extracts an id only from the same strict canonical URL accepted by the editor.
func GoogleDocumentID(value string) (string, bool) {
	parsed, err := url.Parse(strings.TrimSpace(value))
	if err != nil || parsed.Scheme != "https" || parsed.Host != "docs.google.com" || !googleDocumentURLPattern.MatchString(parsed.Path) {
		return "", false
	}
	parts := strings.Split(strings.Trim(parsed.Path, "/"), "/")
	if len(parts) != 4 || parts[0] != "document" || parts[1] != "d" || parts[3] != "edit" || parts[2] == "" {
		return "", false
	}
	return parts[2], true
}

func hasGoogleDocumentReadTool(tools []llm.Tool) bool {
	for _, tool := range tools {
		if tool.Name == "mcp__google-drive__read_google_document" {
			return true
		}
	}
	return false
}

func buildSystemPrompt(r *analyzer.Rules, mode string) string {
	return buildSystemPromptContext(r, mode, nil)
}

func buildSystemPromptContext(r *analyzer.Rules, mode string, claims []map[string]any) string {
	var b strings.Builder
	b.WriteString("Ты редактируешь русскоязычный текст по игре ")
	b.WriteString(r.Game)
	b.WriteString(". Верни ТОЛЬКО отредактированный текст, без пояснений и без разметки кода.\n\n")

	b.WriteString("ГЛАВНОЕ ПРАВИЛО\n")
	b.WriteString("Правка — не улучшение по умолчанию. Меняй только то, что нужно менять:\n")
	b.WriteString("ошибки, согласование, двусмысленность, слышимый повтор, тяжёлую конструкцию,\n")
	b.WriteString("слова из списка замен, абзац-полотно, сломанную логическую связку,\n")
	b.WriteString("пустую шаблонную рамку и разнобой одного термина.\n")
	b.WriteString("Всё остальное оставь как есть — в том числе то, что написал бы иначе.\n\n")

	b.WriteString("КАК ВЫБИРАТЬ ПРАВКУ\n")
	b.WriteString("Начинай с исходника: оставить → починить локально → пересобрать.\n")
	b.WriteString("Пересобирай только тогда, когда локальный ремонт не решает точную проблему читателя.\n")
	b.WriteString("Кандидат остаётся, если устраняет эту проблему, сохраняет смысл, конкретику и голос,\n")
	b.WriteString("не создаёт нового дефекта и тот же результат нельзя получить меньшим изменением.\n")
	b.WriteString("Если новая формулировка просто другая, верни исходную. Метрики — сигнал для проверки, не цель.\n\n")

	switch mode {
	case "лёгкая":
		b.WriteString("РЕЖИМ: лёгкая. Только ошибки. Ни одного стилистического изменения.\n\n")
	case "глубокая":
		b.WriteString("РЕЖИМ: глубокая. Можно менять порядок абзацев и заголовки.\n\n")
	default:
		b.WriteString("РЕЖИМ: обычная. Ошибки, тяжёлые конструкции, словарь, абзацы.\n\n")
	}

	writeProtected(&b, r, true)
	writeEditorialMode(&b, r)
	writeClaimContract(&b, claims)
	writeReplaceKeep(&b, r)
	writeCorrections(&b, r)
	writeTypography(&b, r)
	writeForbiddenPhrases(&b)
	b.WriteString("Сокращение больше 5% требует аудита каждого удаления, но не запрещает удалить точный повтор или пустую рамку.\n")
	return b.String()
}

// writeCorrections — журнал правок автора: за что его редактора уже поправили.
// Это самый точный источник манеры: не норма корпуса, а прямое «так не пишу».
func writeCorrections(b *strings.Builder, r *analyzer.Rules) {
	if len(r.Corrections) == 0 {
		return
	}
	b.WriteString("ПРАВКИ АВТОРА (было → стало; так автор поправлял редактора раньше)\n")
	for _, c := range r.Corrections {
		line := "- " + c["was"] + " → " + c["became"]
		if why := c["reason"]; why != "" {
			line += " (" + why + ")"
		}
		b.WriteString(line + "\n")
	}
	b.WriteString("\n")
}

// Общие блоки промпта: их читают и правка, и переплавка, поэтому текст живёт
// в одном месте.

func writeProtected(b *strings.Builder, r *analyzer.Rules, authorVoice bool) {
	b.WriteString("НЕ ТРОГАЙ НИКОГДА\n")
	b.WriteString("- названия из игры, числа, проценты, характеристики, коды\n")
	b.WriteString("- факты, выводы и позицию автора\n")
	if authorVoice {
		b.WriteString("- разговорные вставки, шутки, обращения к читателю, скобки\n")
		b.WriteString("- «Но» и «Однако» в начале предложения — не склеивай с предыдущим\n")
		b.WriteString("- императив читателю («оставляйте», «держите») — не обезличивай\n")
		b.WriteString("- «хотя» и «зато» — это авторская оговорка, а не лишнее слово\n")
		b.WriteString("- неровный ритм: короткая фраза рядом с длинной остаётся как есть\n")
	}
	if len(r.Protected) > 0 {
		b.WriteString("- слова: " + strings.Join(r.Protected, ", ") + "\n")
	}
	b.WriteString("\n")
}

func writeEditorialMode(b *strings.Builder, r *analyzer.Rules) {
	editorialMode, _ := r.Editorial["mode"].(string)
	if editorialMode == "" {
		editorialMode = "GUIDE"
	}
	b.WriteString("РЕДАКЦИОННЫЙ РЕЖИМ: " + editorialMode + "\n")
	if editorialMode == "GUIDE" {
		b.WriteString("Evidence определяет ЧТО сказать, но в финальном гайде остаётся за кулисами.\n")
		b.WriteString("Не добавляй ссылки на реплеи, выборки, HSGuru, Reddit, сообщество или «анализ показывает».\n")
		b.WriteString("Давай читателю прямой игровой совет; полезные авторские числа не удаляй.\n")
	}
	b.WriteString("Style memory нужна только для голоса, ритма, терминов и структуры; старые советы не являются game knowledge.\n\n")
}

func writeClaimContract(b *strings.Builder, claims []map[string]any) {
	if len(claims) == 0 {
		return
	}
	raw, _ := json.Marshal(claims)
	b.WriteString("GUIDE CLAIM CONTRACT (скрытая метаинформация, не цитировать):\n")
	b.Write(raw)
	b.WriteString("\nМожно менять форму, порядок слов и ритм; нельзя менять action, card, context и confidence.\n\n")
}

func writeReplaceKeep(b *strings.Builder, r *analyzer.Rules) {
	if len(r.Replace) > 0 {
		b.WriteString("ЗАМЕНЯЙ\n")
		for _, p := range r.Replace {
			b.WriteString("- " + p["from"] + " → " + p["to"] + "\n")
		}
		b.WriteString("\n")
	}
	if len(r.Keep) > 0 {
		b.WriteString("НЕ ЗАМЕНЯЙ, это авторские слова: ")
		b.WriteString(strings.Join(r.Keep, ", ") + "\n\n")
	}
}

func writeTypography(b *strings.Builder, r *analyzer.Rules) {
	b.WriteString("ОФОРМЛЕНИЕ\n")
	if yo, ok := r.Typography["yo"].(map[string]any); ok {
		if yo["decision"] == "remove" {
			b.WriteString("- букву ё не ставить: «ее», «еще», «все же». Исключение — официальные названия\n")
		}
	}
	if q, ok := r.Typography["quotes"].(map[string]any); ok {
		if q["decision"] == "straight" {
			b.WriteString("- кавычки прямые\n")
		}
	}
	b.WriteString("- названия в кавычки не берутся\n")
	b.WriteString("- архетипы и билды без дефиса, оба слова с заглавной\n\n")
}

func writeForbiddenPhrases(b *strings.Builder) {
	b.WriteString("ЗАПРЕЩЕНО ДОБАВЛЯТЬ\n")
	b.WriteString("«стоит отметить», «важно понимать», «давайте разберёмся», «подведём итог»,\n")
	b.WriteString("конструкцию «не просто X, а Y», новые факты, числа и выводы.\n")
}

// safeClaims не передаёт модели backstage sources и replay analytics.
// Она видит только неизменяемый смысловой контракт.
func safeClaims(claims []map[string]any) []map[string]any {
	out := make([]map[string]any, 0, len(claims))
	for _, claim := range claims {
		clean := map[string]any{}
		for _, key := range []string{"claim_id", "meaning", "confidence", "patch", "meta_epoch"} {
			if value, ok := claim[key]; ok {
				clean[key] = value
			}
		}
		out = append(out, clean)
	}
	return out
}

func retryPrompt(vs []analyzer.Violation) string {
	var b strings.Builder
	b.WriteString("Правка не принята. Проверка нашла:\n\n")
	for _, v := range vs {
		b.WriteString("- " + v.Message + "\n")
	}
	b.WriteString("\nВерни текст заново. Сохрани всё, что перечислено выше: ")
	b.WriteString("верни на место живые обороты и защищённые элементы, ")
	b.WriteString("не выравнивай длину предложений. ")
	b.WriteString("Исправь только настоящие ошибки. Только текст, без пояснений.")
	return b.String()
}

// plural склоняет «попытка» — сообщение читает человек.
func plural(n int) string {
	word := "попыток"
	switch {
	case n%10 == 1 && n%100 != 11:
		word = "попытку"
	case n%10 >= 2 && n%10 <= 4 && (n%100 < 12 || n%100 > 14):
		word = "попытки"
	}
	return fmt.Sprintf("%d %s", n, word)
}

// stripFence снимает обрамление ```…```, если модель его добавила.
func stripFence(s string) string {
	s = strings.TrimSpace(s)
	if !strings.HasPrefix(s, "```") {
		return s
	}
	if i := strings.Index(s, "\n"); i >= 0 {
		s = s[i+1:]
	}
	if i := strings.LastIndex(s, "```"); i >= 0 {
		s = s[:i]
	}
	return strings.TrimSpace(s)
}
