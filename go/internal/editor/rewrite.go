package editor

// Переплавка — не правка, а пересборка. Исходник здесь только источник
// фактов: карты, числа, советы, оговорки. Форма строится заново: структура —
// по скелету жанра из профиля, голос и ритм — по норме автора и образцам из
// его архива. Два прохода: сначала план (JSON), который сайдкар проверяет на
// полноту и невыдумывание, потом проза по плану, которую проверяет затвор
// переплавки — против нормы автора и утверждений исходника.

import (
	"context"
	"encoding/json"
	"fmt"
	"strings"

	"github.com/Manacost-Labs/EditorTeam/go/internal/analyzer"
	"github.com/Manacost-Labs/EditorTeam/go/internal/llm"
)

const outlineAttempts = 2

func (s *Service) rewrite(ctx context.Context, req Request, rules *analyzer.Rules,
	claims []map[string]any, res *Result) (*Result, error) {
	source := req.Text
	googleURL := isGoogleDocumentURL(req.Text)
	if googleURL && hasGoogleDocumentReadTool(req.LLMContext.Tools) {
		text, err := s.resolveSource(ctx, req)
		if err != nil {
			return nil, err
		}
		source = text
	}

	// Проход 1: план. Модель раскладывает тезисы исходника по скелету жанра и
	// честно называет разделы, для которых материала нет.
	declared := append([]string{}, req.DeclaredMissing...)
	outline := s.planOutline(ctx, req, rules, source, res)
	if outline != nil {
		res.Outline = outline
		for _, id := range outline.MissingSections {
			if !containsString(declared, id) {
				declared = append(declared, id)
			}
		}
	}
	res.MissingSections = declared
	res.MissingTitles = sectionTitles(rules, declared)

	// Проход 2: проза по плану, затвор — против нормы автора.
	system := buildRewritePrompt(rules, claims, outline)
	user := "ИСХОДНИК (источник фактов, не формы):\n" + source
	if outline != nil {
		raw, _ := json.Marshal(outline)
		user += "\n\nПЛАН (следуй порядку и заголовкам; разделы из missing_sections не пиши):\n" + string(raw)
	}
	messages := []llm.Message{
		{Role: "system", Content: system},
		{Role: "user", Content: user},
	}

	for attempt := 1; attempt <= s.maxAttempts; attempt++ {
		out, err := s.complete(ctx, messages, req.LLMContext)
		if err != nil {
			return nil, fmt.Errorf("переплавка, попытка %d: %w", attempt, err)
		}
		candidate := stripFence(out.Text)

		verdict, err := s.an.ValidateWithContext(ctx, source, candidate, req.Game, req.Profile,
			analyzer.ValidationContext{
				Mode: req.EditorialMode, Depth: DepthRewrite, DeclaredMissing: declared,
				EvidenceRequested: req.EvidenceRequested,
				ClaimsBefore:      claims, ClaimsAfter: claims,
				CurrentPatch: req.CurrentPatch, CurrentMetaEpoch: req.CurrentMetaEpoch,
			})
		if err != nil {
			return nil, fmt.Errorf("проверка переплавки %d: %w", attempt, err)
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
			res.Text, res.Accepted, res.Verdict = source, false, verdict
			res.Caveats = append(res.Caveats,
				"переплавка не прошла проверку за "+plural(s.maxAttempts)+
					" — возвращён исходный текст")
			break
		}
		messages = append(messages,
			llm.Message{Role: "assistant", Content: candidate},
			llm.Message{Role: "user", Content: retryPromptFor(DepthRewrite, verdict.Violations)},
		)
	}

	report, err := s.an.AnalyzeWithMode(ctx, res.Text, req.Game, req.Profile,
		req.EditorialMode, req.EvidenceRequested)
	if err == nil {
		res.Report = report
	}
	res.Changes = summarizeChanges(source, res.Text)
	if res.Accepted {
		res.Preserved = append(preservedSummary(rules),
			"факты только из исходника: карты, числа и советы сверены покрытием утверждений")
		res.SaveStatus = "результат возвращён в чат; исходный текст не перезаписывался"
		if googleURL && source != req.Text {
			res.SaveStatus = "результат возвращён в чат; сохранение в Google Docs ещё не подтверждено"
			res.SourceText = source
			res.GoogleDocumentID, _ = GoogleDocumentID(req.Text)
		}
	} else {
		res.Preserved = []string{"исходный текст возвращён без изменений"}
		res.SaveStatus = "переплавка не сохранена: проверка не пройдена, возвращён исходный текст"
	}
	if len(res.MissingTitles) > 0 {
		res.Caveats = append(res.Caveats,
			"в исходнике нет материала для разделов: "+strings.Join(res.MissingTitles, ", ")+
				" — они не написаны, чтобы ничего не выдумывать")
	}
	return res, nil
}

// planOutline получает план от модели и проверяет его сайдкаром. Провал плана
// не останавливает переплавку: прозу тогда пишут без плана, а структуру
// решает затвор. Возвращает nil, если план не удался.
func (s *Service) planOutline(ctx context.Context, req Request, rules *analyzer.Rules,
	source string, res *Result) *analyzer.Outline {
	messages := []llm.Message{
		{Role: "system", Content: buildOutlinePrompt(rules)},
		{Role: "user", Content: source},
	}
	for attempt := 1; attempt <= outlineAttempts; attempt++ {
		out, err := s.complete(ctx, messages, req.LLMContext)
		if err != nil {
			res.Caveats = append(res.Caveats, "план не получен: "+err.Error()+" — переплавка без плана")
			return nil
		}
		parsed, perr := parseOutline(out.Text)
		var retry string
		if perr != nil {
			retry = "План не разобран как JSON (" + perr.Error() + "). Верни ТОЛЬКО JSON по схеме, без пояснений."
		} else {
			verdict, err := s.an.ValidateOutline(ctx, *parsed, source, req.Game, req.Profile)
			if err != nil {
				res.Caveats = append(res.Caveats, "план не проверен: "+err.Error()+" — переплавка без плана")
				return nil
			}
			if verdict.OK {
				if verdict.Normalized != nil {
					return verdict.Normalized
				}
				return parsed
			}
			retry = outlineRetryPrompt(verdict.Violations)
		}
		if attempt == outlineAttempts {
			break
		}
		messages = append(messages,
			llm.Message{Role: "assistant", Content: out.Text},
			llm.Message{Role: "user", Content: retry},
		)
	}
	res.Caveats = append(res.Caveats, "план не прошёл проверку: переплавка выполнена без плана")
	return nil
}

// resolveSource читает документ Google Docs через выданный read-only grant и
// возвращает его текст. Переплавке нужен исходник целиком до первого прохода.
func (s *Service) resolveSource(ctx context.Context, req Request) (string, error) {
	system := "Ссылка в сообщении — это адрес исходного документа. Вызови read_google_document по ID из ссылки " +
		"и верни весь текст документа целиком, без URL, служебного заголовка, каталога вкладок и пояснений. " +
		"Не пересказывай и не сокращай документ.\n"
	messages := []llm.Message{
		{Role: "system", Content: system},
		{Role: "user", Content: req.Text},
	}
	for attempt := 1; attempt <= s.maxAttempts; attempt++ {
		out, err := s.complete(ctx, messages, req.LLMContext)
		if err != nil {
			return "", fmt.Errorf("чтение документа, попытка %d: %w", attempt, err)
		}
		if out.SourceText != "" {
			return out.SourceText, nil
		}
		messages = append(messages,
			llm.Message{Role: "assistant", Content: out.Text},
			llm.Message{Role: "user", Content: "Ссылка ведёт на Google Docs. Сначала обязательно вызови read_google_document, затем верни полный текст документа без служебной оболочки."},
		)
	}
	return "", fmt.Errorf("не удалось получить содержимое Google Docs через выданный read-only grant")
}

// parseOutline вынимает JSON плана из ответа модели: между первой «{» и
// последней «}», обрамление кода снимается.
func parseOutline(text string) (*analyzer.Outline, error) {
	raw := stripFence(text)
	start, end := strings.Index(raw, "{"), strings.LastIndex(raw, "}")
	if start < 0 || end <= start {
		return nil, fmt.Errorf("в ответе нет JSON-объекта")
	}
	var outline analyzer.Outline
	if err := json.Unmarshal([]byte(raw[start:end+1]), &outline); err != nil {
		return nil, err
	}
	if len(outline.Sections) == 0 && len(outline.MissingSections) == 0 {
		return nil, fmt.Errorf("план пустой: ни разделов, ни missing_sections")
	}
	if outline.MissingSections == nil {
		outline.MissingSections = []string{}
	}
	return &outline, nil
}

func sectionTitles(rules *analyzer.Rules, ids []string) []string {
	titles := make([]string, 0, len(ids))
	for _, id := range ids {
		title := id
		for _, sec := range skeletonSections(rules) {
			if sec.ID == id {
				title = sec.Title
				break
			}
		}
		titles = append(titles, title)
	}
	return titles
}

func skeletonSections(rules *analyzer.Rules) []analyzer.SkeletonSection {
	if rules.Skeleton != nil && len(rules.Skeleton.Sections) > 0 {
		return rules.Skeleton.Sections
	}
	if len(rules.Sections) > 0 {
		return rules.Sections
	}
	out := make([]analyzer.SkeletonSection, 0, len(rules.SectionsRequired))
	for i, title := range rules.SectionsRequired {
		out = append(out, analyzer.SkeletonSection{ID: title, Title: title, Order: i + 1, Required: true})
	}
	return out
}

func containsString(items []string, want string) bool {
	for _, item := range items {
		if item == want {
			return true
		}
	}
	return false
}

// ── Промпты переплавки ─────────────────────────────────────────────────

func writeSkeleton(b *strings.Builder, rules *analyzer.Rules) {
	sections := skeletonSections(rules)
	if len(sections) == 0 {
		return
	}
	b.WriteString("СКЕЛЕТ ЖАНРА (профиль " + rules.Profile + "; порядок обязателен)\n")
	n := 0
	for _, sec := range sections {
		if !sec.Required {
			continue
		}
		n++
		line := fmt.Sprintf("%d. %s", n, sec.Title)
		if sec.Purpose != "" {
			line += " — " + sec.Purpose
		}
		b.WriteString(line + "\n")
	}
	optional := []string{}
	for _, sec := range sections {
		if !sec.Required {
			item := sec.Title
			if sec.Purpose != "" {
				item += " (" + sec.Purpose + ")"
			}
			optional = append(optional, item)
		}
	}
	if len(optional) > 0 {
		b.WriteString("Необязательные, если есть материал: " + strings.Join(optional, "; ") + "\n")
	}
	b.WriteString("Заголовки — markdown (##), короткие, как у автора: «Муллиган», «Стратегия игры», «Матч-апы».\n")
	if rules.MinWords > 0 {
		b.WriteString(fmt.Sprintf("Объём материала у автора — от %d слов; не растягивай пустым, но и не сжимай в конспект.\n", rules.MinWords))
	}
	b.WriteString("Раздел, для которого в исходнике нет материала, не выдумывать: он объявлен в плане отсутствующим и не пишется.\n\n")
}

// writeForm — форма подачи из профиля: у автора данные объясняются прозой,
// таблиц в гайдах нет, кодов — по числу рекомендуемых сборок.
func writeForm(b *strings.Builder, rules *analyzer.Rules) {
	b.WriteString("ФОРМА ПОДАЧИ\n")
	tables, codes := 1, 4
	if v, ok := rules.Form["tables_max"].(float64); ok {
		tables = int(v)
	}
	if v, ok := rules.Form["codes_max"].(float64); ok {
		codes = int(v)
	}
	if tables == 0 {
		b.WriteString("- таблиц нет: данные объясняются прозой, как у автора\n")
	} else {
		b.WriteString(fmt.Sprintf("- не больше %d таблицы на весь текст; остальные данные — прозой, как у автора\n", tables))
	}
	b.WriteString(fmt.Sprintf("- кодов колод не больше %d, только для рекомендуемых сборок; остальные колоды называются без кода\n", codes))
	if rules.Form["grade_labels"] == "forbidden" {
		b.WriteString("- никаких оценочных букв «S/A/B» и строк «Положение: A−»: где колода стоит и когда её брать, говорится словами\n")
	}
	b.WriteString("- разбор класса или колоды — связный абзац, а не список «плюс / минус / итог»\n")
	b.WriteString("- одна цифра — один раз; повтор процента в другом разделе заменяется ссылкой на вывод\n\n")
}

func writeVoice(b *strings.Builder, rules *analyzer.Rules) {
	b.WriteString("ГОЛОС АВТОРА (норма корпуса, не цель для подгонки)\n")
	b.WriteString("- предложение в среднем 14,9 слова, но разброс большой: короткая фраза рядом с длинным периодом (отношение разброса к среднему 0,51);\n")
	b.WriteString("- абзац — около двух предложений; полотна нет;\n")
	b.WriteString("- разговор с читателем на «вы»; совет — глаголом: «оставляйте», «не спешите», а не «стоит оставить»;\n")
	b.WriteString("- «Но» и «Однако» в начале предложения, «хотя» и «зато» как авторская оговорка;\n")
	b.WriteString("- пояснение в скобках, когда термин вводится;\n")
	b.WriteString("- юмора нет, прогнозов и «время покажет» нет, призывов подписаться нет;\n")
	b.WriteString("- к числам отношение осторожное: категории («тир-1», «растёт с повышением рангов») чаще процентов, но числа исходника не выбрасывать.\n")
	if rules.VoiceSignature != "" {
		b.WriteString("\nПОЧЕРК АВТОРА (из слепка манеры)\n")
		b.WriteString(rules.VoiceSignature + "\n")
	}
	if len(rules.RhythmInstruction) > 0 {
		b.WriteString("\nРИТМ\n")
		for _, line := range rules.RhythmInstruction {
			b.WriteString("- " + line + "\n")
		}
	}
	b.WriteString("\n")
}

func writeStyleExamples(b *strings.Builder, rules *analyzer.Rules) {
	if len(rules.StyleExamples) == 0 {
		return
	}
	b.WriteString("ОБРАЗЦЫ ГОЛОСА (СТИЛЬ, НЕ ФАКТ)\n")
	b.WriteString("Это абзацы автора из старых гайдов. Бери отсюда интонацию, построение фраз, обращение к читателю и ритм. ")
	b.WriteString("Карты, числа и советы из образцов устарели и относятся к другим колодам — не переноси их в текст.\n")
	for _, ex := range rules.StyleExamples {
		role := ex.Role
		if role == "" {
			role = "образец"
		}
		b.WriteString("[" + role + "] " + strings.TrimSpace(ex.Text) + "\n")
	}
	b.WriteString("\n")
}

func writeMarkerLists(b *strings.Builder, rules *analyzer.Rules) {
	if rules.Markers == nil {
		return
	}
	join := func(entries []analyzer.MarkerEntry, withFix bool) []string {
		out := make([]string, 0, len(entries))
		for _, e := range entries {
			if len(e.Examples) == 0 {
				continue
			}
			item := "«" + strings.Join(e.Examples, "», «") + "»"
			if withFix && e.Fix != "" {
				item = e.Name + " (" + item + ") → " + e.Fix
			}
			out = append(out, item)
		}
		return out
	}
	if remove := join(rules.Markers.Remove, false); len(remove) > 0 {
		b.WriteString("НИКОГДА (вырезается без потерь): " + strings.Join(remove, ", ") + "\n")
	}
	if rewrite := join(rules.Markers.Rewrite, true); len(rewrite) > 0 {
		b.WriteString("НЕ ПИСАТЬ, ВМЕСТО ЭТОГО — ФАКТ:\n")
		for _, item := range rewrite {
			b.WriteString("- " + item + "\n")
		}
	}
	if review := join(rules.Markers.Review, false); len(review) > 0 {
		b.WriteString("НЕ БОЛЬШЕ ОДНОГО НА АБЗАЦ: " + strings.Join(review, ", ") + "\n")
	}
	b.WriteString("\n")
}

func buildRewritePrompt(rules *analyzer.Rules, claims []map[string]any, outline *analyzer.Outline) string {
	var b strings.Builder
	b.WriteString("Ты переплавляешь черновик по игре " + rules.Game + " в чистовой текст в манере автора. ")
	b.WriteString("Верни ТОЛЬКО текст, без пояснений и без разметки кода.\n\n")

	b.WriteString("ГЛАВНОЕ ПРАВИЛО\n")
	b.WriteString("Исходник — источник фактов, а не формы. Каждая карта, число, совет, оговорка и вывод берутся только из него.\n")
	b.WriteString("Форму строй заново: структура — по скелету жанра, ритм и голос — по норме автора и образцам ниже.\n")
	b.WriteString("Ничего не добавляй от себя: ни карт, ни чисел, ни матч-апов, ни выводов, ни переходов «как известно».\n")
	b.WriteString("Отрицания и условия исходника сохраняются: «не оставляйте» не превращается в «оставляйте», «иногда» — в «всегда».\n")
	b.WriteString("Канцелярит и рамки черновика («в данной статье», «важно понимать», «подводя итог») не переносятся — их место занимает прямой совет.\n")
	b.WriteString("Источник — не шаблон структуры: набор и порядок разделов берутся из скелета жанра, а не из оглавления исходника. ")
	b.WriteString("Исследовательский отчёт с таблицами, рейтингами и методикой превращается в рассказ автора: от предмета к выбору читателя.\n")
	b.WriteString("Каждая цифра звучит один раз — там, где читатель принимает решение. В других разделах ссылайся на вывод, не повторяя число.\n")
	if outline == nil {
		b.WriteString("Плана нет: разложи тезисы исходника по скелету сам; раздел без материала пропусти и не выдумывай.\n")
	}
	b.WriteString("\n")

	writeSkeleton(&b, rules)
	writeForm(&b, rules)
	writeVoice(&b, rules)
	writeStyleExamples(&b, rules)
	writeProtected(&b, rules, false)
	writeEditorialMode(&b, rules)
	writeClaimContract(&b, claims)
	writeReplaceKeep(&b, rules)
	writeCorrections(&b, rules)
	writeTypography(&b, rules)
	writeForbiddenPhrases(&b)
	writeMarkerLists(&b, rules)
	return b.String()
}

func buildOutlinePrompt(rules *analyzer.Rules) string {
	var b strings.Builder
	b.WriteString("Ты готовишь план переплавки черновика по игре " + rules.Game + " в гайд в манере автора. ")
	b.WriteString("Верни ТОЛЬКО JSON, без пояснений и без разметки кода.\n\n")
	b.WriteString("ГЛАВНОЕ ПРАВИЛО\n")
	b.WriteString("Тезисы берутся только из исходника: карты, числа, советы, оговорки — своими словами, но без новых фактов.\n")
	b.WriteString("Раздел, для которого в исходнике нет материала, — в missing_sections, не в sections. Выдумывать нельзя.\n\n")
	writeSkeleton(&b, rules)
	b.WriteString("СХЕМА ОТВЕТА\n")
	b.WriteString(`{"sections":[{"id":"builds","title":"Сборки","claims":["один тезис исходника с названиями карт и числами как в исходнике"]}],` +
		`"missing_sections":["id разделов без материала"],"notes":["что в исходнике спорно или неполно"]}` + "\n")
	b.WriteString("id разделов: ")
	ids := []string{}
	for _, sec := range skeletonSections(rules) {
		ids = append(ids, sec.ID)
	}
	b.WriteString(strings.Join(ids, ", ") + ". Каждый обязательный раздел — либо в sections с тезисами, либо в missing_sections.\n")
	return b.String()
}

func outlineRetryPrompt(vs []analyzer.Violation) string {
	var b strings.Builder
	b.WriteString("План не принят. Проверка нашла:\n\n")
	for _, v := range vs {
		b.WriteString("- " + v.Message + "\n")
	}
	b.WriteString("\nВерни план заново, ТОЛЬКО JSON по схеме. Раздел без материала — в missing_sections; ")
	b.WriteString("карты и числа — только из исходника.")
	return b.String()
}

// retryPromptFor подбирает повторный запрос под глубину: переплавке нужны
// другие подсказки, чем правке.
func retryPromptFor(depth string, vs []analyzer.Violation) string {
	if depth != DepthRewrite {
		return retryPrompt(vs)
	}
	var b strings.Builder
	b.WriteString("Переплавка не принята. Проверка нашла:\n\n")
	hints := map[string]string{}
	for _, v := range vs {
		b.WriteString("- " + v.Message + "\n")
		switch {
		case strings.HasPrefix(v.Kind, "voice_"):
			hints["voice"] = "обращайся к читателю на «вы» и советуй глаголом; оговаривайся через «но» и «хотя»"
		case strings.HasPrefix(v.Kind, "rhythm_"):
			hints["rhythm"] = "чередуй короткие фразы с длинными, не выравнивай длину предложений"
		case strings.HasPrefix(v.Kind, "markers_"):
			hints["markers"] = "убери перечисленные шаблонные фразы, не заменяя их другими рамками"
		case v.Kind == "structure_missing":
			hints["structure"] = "добавь раздел из материала исходника; если материала нет — не выдумывай, а сообщи об этом"
		case v.Kind == "CLAIM_COVERAGE_LOST" || v.Kind == "protected_lost" || v.Kind == "FACTUAL_SEMANTIC_DRIFT":
			hints["claims"] = "верни пропавшие карты, числа и отрицания дословно из исходника; ничего не добавляй"
		case v.Kind == "CERTAINTY_DRIFT":
			hints["certainty"] = "не усиливай совет: «можно» не становится «обязательно»"
		}
	}
	b.WriteString("\nПерепиши заново по тому же плану. ")
	for _, key := range []string{"claims", "structure", "voice", "rhythm", "markers", "certainty"} {
		if hint, ok := hints[key]; ok {
			b.WriteString(strings.ToUpper(hint[:2]) + hint[2:] + ". ")
		}
	}
	b.WriteString("Только текст, без пояснений.")
	return b.String()
}
