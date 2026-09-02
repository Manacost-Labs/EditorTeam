package editor

import (
	"context"
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/Manacost-Labs/EditorTeam/go/internal/analyzer"
	"github.com/Manacost-Labs/EditorTeam/go/internal/llm"
)

// capturedAnalyzer — сайдкар переплавки: отдаёт правила со скелетом и
// образцами, проверяет план и запоминает тела запросов к /validate.
type capturedAnalyzer struct {
	mu        sync.Mutex
	validates []map[string]any
	outlines  []map[string]any
	outlineOK bool
	verdicts  []analyzer.Verdict
	i         int
}

func rewriteRules() analyzer.Rules {
	minWords := 60
	return analyzer.Rules{
		Game: "hearthstone", Profile: "constructed-guide", Depth: DepthRewrite,
		Protected: []string{"ОТК"},
		Replace:   []map[string]string{{"from": "дека", "to": "колода"}},
		Keep:      []string{"винрейт"},
		Norms:     map[string]any{"provisional": false},
		MinWords:  600,
		Skeleton: &analyzer.Skeleton{
			Profile: "constructed-guide",
			Sections: []analyzer.SkeletonSection{
				{ID: "builds", Title: "Сборки", Purpose: "две-три сборки", MinWords: &minWords, Order: 1, Required: true},
				{ID: "mulligan", Title: "Муллиган", Purpose: "что оставлять", MinWords: &minWords, Order: 2, Required: true},
				{ID: "matchups", Title: "Матч-апы", Purpose: "каждый класс", MinWords: &minWords, Order: 3, Required: true},
				{ID: "conclusion", Title: "Заключение", Order: 4, Required: false},
			},
		},
		Form:           map[string]any{"tables_max": float64(1), "codes_max": float64(4), "grade_labels": "forbidden"},
		VoiceSignature: "Характерное: зачин «Герой гайда — …»",
		StyleExamples: []analyzer.StyleExample{
			{Role: "муллиган", Name: "Агро Шаман", Text: "Играйте очень агрессивно и рискуйте. Размены оставляйте противнику."},
		},
		Markers: &analyzer.MarkerLists{
			Remove:  []analyzer.MarkerEntry{{Name: "Остатки чат-ответа", Examples: []string{"надеюсь, это поможет"}}},
			Rewrite: []analyzer.MarkerEntry{{Name: "Безличный оборот", Examples: []string{"стоит отметить"}, Fix: "вернуть действующее лицо"}},
			Review:  []analyzer.MarkerEntry{{Name: "Промо-лексика", Examples: []string{"впечатляющий"}}},
		},
		RhythmInstruction: []string{"не подгоняй предложения к одной длине"},
	}
}

func (c *capturedAnalyzer) server(t *testing.T) *httptest.Server {
	t.Helper()
	return httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		body, _ := io.ReadAll(r.Body)
		var payload map[string]any
		_ = json.Unmarshal(body, &payload)
		c.mu.Lock()
		defer c.mu.Unlock()
		switch r.URL.Path {
		case "/rules":
			rules := rewriteRules()
			if depth, _ := payload["depth"].(string); depth != DepthRewrite {
				rules.Skeleton, rules.StyleExamples, rules.Markers = nil, nil, nil
			}
			_ = json.NewEncoder(w).Encode(rules)
		case "/outline/validate":
			c.outlines = append(c.outlines, payload)
			if c.outlineOK {
				_ = json.NewEncoder(w).Encode(analyzer.OutlineVerdict{OK: true})
			} else {
				_ = json.NewEncoder(w).Encode(analyzer.OutlineVerdict{OK: false, Violations: []analyzer.Violation{
					{Kind: "OUTLINE_INVALID", Message: "обязательный раздел «Муллиган» ни в плане, ни в missing_sections"},
				}})
			}
		case "/validate":
			c.validates = append(c.validates, payload)
			v := c.verdicts[min(c.i, len(c.verdicts)-1)]
			c.i++
			_ = json.NewEncoder(w).Encode(v)
		case "/analyze":
			_ = json.NewEncoder(w).Encode(analyzer.Report{Profile: "constructed-guide"})
		default:
			w.WriteHeader(404)
		}
	}))
}

// countingLLM отдаёт ответы по очереди и считает вызовы.
func countingLLM(t *testing.T, replies ...string) (*httptest.Server, *int) {
	t.Helper()
	calls := 0
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		reply := replies[min(calls, len(replies)-1)]
		calls++
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write([]byte(`{"choices":[{"message":{"role":"assistant","content":` + mustJSON(reply) + `}}]}`))
	}))
	return srv, &calls
}

const outlineJSON = `{"sections":[{"id":"builds","title":"Сборки","claims":["две сборки"]},` +
	`{"id":"mulligan","title":"Муллиган","claims":["ищите Боевой якорррь"]}],"missing_sections":["matchups"],"notes":[]}`

func TestRewriteRunsOutlineThenProse(t *testing.T) {
	an := &capturedAnalyzer{outlineOK: true, verdicts: []analyzer.Verdict{{Accepted: true}}}
	anSrv := an.server(t)
	defer anSrv.Close()
	lm, calls := countingLLM(t, "```json\n"+outlineJSON+"\n```", "## Сборки\nВы берёте две сборки.\n\n## Муллиган\nИщите Боевой якорррь.")
	defer lm.Close()

	res, err := newService(t, anSrv.URL, lm.URL, 3).Edit(context.Background(),
		Request{Text: "Стоит отметить, что сборок две. Ищите Боевой якорррь.", Game: "hearthstone",
			Profile: "constructed-guide", Mode: "Переплавка"})
	if err != nil {
		t.Fatal(err)
	}
	if !res.Accepted || res.Depth != DepthRewrite {
		t.Fatalf("переплавка должна пройти: %+v", res)
	}
	if *calls != 2 {
		t.Fatalf("ожидались два вызова модели (план и проза), было %d", *calls)
	}
	if res.Outline == nil || len(res.Outline.Sections) != 2 {
		t.Fatalf("план должен сохраниться в результате: %+v", res.Outline)
	}
	if len(an.outlines) != 1 {
		t.Fatalf("план должен быть проверен сайдкаром один раз, было %d", len(an.outlines))
	}
	if len(an.validates) != 1 {
		t.Fatalf("ожидалась одна проверка прозы, было %d", len(an.validates))
	}
	payload := an.validates[0]
	if payload["depth"] != DepthRewrite {
		t.Fatalf("в /validate должна уйти глубина переплавки: %v", payload["depth"])
	}
	declared, _ := payload["declared_missing"].([]any)
	if len(declared) != 1 || declared[0] != "matchups" {
		t.Fatalf("объявленные отсутствующими разделы должны дойти до затвора: %v", payload["declared_missing"])
	}
	if len(res.MissingTitles) != 1 || res.MissingTitles[0] != "Матч-апы" {
		t.Fatalf("отсутствующий раздел должен быть назван заголовком: %v", res.MissingTitles)
	}
	if !strings.Contains(strings.Join(res.Caveats, " "), "нет материала для разделов: Матч-апы") {
		t.Fatalf("человеку нужно сказать, чего не хватает в исходнике: %v", res.Caveats)
	}
	if !strings.Contains(strings.Join(res.Preserved, " "), "покрытием утверждений") {
		t.Fatalf("отчёт должен объяснить, чем защищены факты: %v", res.Preserved)
	}
}

func TestRewriteFallsBackToProseWhenOutlineInvalid(t *testing.T) {
	an := &capturedAnalyzer{outlineOK: false, verdicts: []analyzer.Verdict{{Accepted: true}}}
	anSrv := an.server(t)
	defer anSrv.Close()
	lm, calls := countingLLM(t, outlineJSON, outlineJSON, "## Сборки\nПроза без плана.")
	defer lm.Close()

	res, err := newService(t, anSrv.URL, lm.URL, 3).Edit(context.Background(),
		Request{Text: "Исходник.", Game: "hearthstone", Profile: "constructed-guide", Mode: DepthRewrite})
	if err != nil {
		t.Fatal(err)
	}
	if *calls != 3 {
		t.Fatalf("две попытки плана и одна проза: ожидалось 3 вызова, было %d", *calls)
	}
	if res.Outline != nil {
		t.Fatal("невалидный план не должен попадать в результат")
	}
	if !strings.Contains(strings.Join(res.Caveats, " "), "без плана") {
		t.Fatalf("переплавка без плана должна быть названа: %v", res.Caveats)
	}
	if !res.Accepted || res.Text != "## Сборки\nПроза без плана." {
		t.Fatalf("проза должна пройти затвор и вернуться: %+v", res)
	}
}

func TestRewriteRetriesWithCoverageHints(t *testing.T) {
	an := &capturedAnalyzer{outlineOK: true, verdicts: []analyzer.Verdict{
		{Accepted: false, Violations: []analyzer.Violation{{Kind: "CLAIM_COVERAGE_LOST", Message: "пропала карта «Мастер брони»"}}},
		{Accepted: true},
	}}
	anSrv := an.server(t)
	defer anSrv.Close()
	lm, calls := countingLLM(t, outlineJSON, "Плохая проза.", "Хорошая проза с Мастером брони.")
	defer lm.Close()

	res, err := newService(t, anSrv.URL, lm.URL, 3).Edit(context.Background(),
		Request{Text: "Мастер брони.", Game: "hearthstone", Profile: "constructed-guide", Mode: DepthRewrite})
	if err != nil {
		t.Fatal(err)
	}
	if *calls != 3 || len(res.Attempts) != 2 || !res.Accepted {
		t.Fatalf("после отказа затвора должна быть вторая попытка прозы: calls=%d attempts=%d", *calls, len(res.Attempts))
	}
}

func TestRewriteReturnsSourceWhenAllAttemptsFail(t *testing.T) {
	an := &capturedAnalyzer{outlineOK: true, verdicts: []analyzer.Verdict{
		{Accepted: false, Violations: []analyzer.Violation{{Kind: "voice_below_norm", Message: "голос ниже нормы"}}},
	}}
	anSrv := an.server(t)
	defer anSrv.Close()
	lm, _ := countingLLM(t, outlineJSON, "Сухо.")
	defer lm.Close()

	source := "Исходный слоп с числом 5."
	res, err := newService(t, anSrv.URL, lm.URL, 2).Edit(context.Background(),
		Request{Text: source, Game: "hearthstone", Profile: "constructed-guide", Mode: DepthRewrite})
	if err != nil {
		t.Fatal(err)
	}
	if res.Accepted || res.Text != source {
		t.Fatalf("непринятая переплавка возвращает исходник: %+v", res)
	}
	if !strings.Contains(res.SaveStatus, "не сохранена") {
		t.Fatalf("статус должен объяснить отказ: %q", res.SaveStatus)
	}
}

func TestRewritePromptCarriesSkeletonExamplesAndMarkers(t *testing.T) {
	rules := rewriteRules()
	p := buildRewritePrompt(&rules, nil, nil)
	for _, want := range []string{
		"СКЕЛЕТ ЖАНРА", "1. Сборки — две-три сборки", "2. Муллиган", "Необязательные, если есть материал: Заключение",
		"ГОЛОС АВТОРА", "ПОЧЕРК АВТОРА", "Герой гайда", "РИТМ",
		"ФОРМА ПОДАЧИ", "не больше 1 таблицы", "кодов колод не больше 4", "Положение: A−",
		"Источник — не шаблон структуры", "Каждая цифра звучит один раз",
		"ОБРАЗЦЫ ГОЛОСА (СТИЛЬ, НЕ ФАКТ)", "[муллиган] Играйте очень агрессивно",
		"НИКОГДА (вырезается без потерь): «надеюсь, это поможет»",
		"НЕ ПИСАТЬ, ВМЕСТО ЭТОГО — ФАКТ", "стоит отметить", "НЕ БОЛЬШЕ ОДНОГО НА АБЗАЦ",
		"ОТК", "дека → колода", "винрейт", "Исходник — источник фактов, а не формы",
		"Плана нет",
	} {
		if !strings.Contains(p, want) {
			t.Errorf("в промпте переплавки нет %q", want)
		}
	}
	for _, absent := range []string{"Правка — не улучшение по умолчанию", "оставить → починить локально → пересобрать", "Сокращение больше 5%"} {
		if strings.Contains(p, absent) {
			t.Errorf("консервативный блок правки попал в переплавку: %q", absent)
		}
	}
	withOutline := buildRewritePrompt(&rules, nil, &analyzer.Outline{Sections: []analyzer.OutlineSection{{ID: "builds"}}})
	if strings.Contains(withOutline, "Плана нет") {
		t.Error("при наличии плана подсказка «Плана нет» лишняя")
	}
	op := buildOutlinePrompt(&rules)
	for _, want := range []string{"ТОЛЬКО JSON", "missing_sections", "builds, mulligan, matchups, conclusion", "СКЕЛЕТ ЖАНРА"} {
		if !strings.Contains(op, want) {
			t.Errorf("в промпте плана нет %q", want)
		}
	}
}

func TestEditPromptIsUnchangedForOrdinaryDepths(t *testing.T) {
	rules := rewriteRules()
	p := buildSystemPromptContext(&rules, "обычная", nil)
	for _, want := range []string{"Правка — не улучшение по умолчанию", "РЕЖИМ: обычная", "Сокращение больше 5%"} {
		if !strings.Contains(p, want) {
			t.Errorf("в промпте правки нет %q", want)
		}
	}
	if strings.Contains(p, "СКЕЛЕТ ЖАНРА") || strings.Contains(p, "ОБРАЗЦЫ ГОЛОСА") {
		t.Error("блоки переплавки не должны попадать в обычную правку")
	}
}

func TestNormalizeDepth(t *testing.T) {
	cases := map[string]string{
		"переплавка": DepthRewrite, "Переплавка": DepthRewrite, " ПЕРЕПЛАВКА: ": DepthRewrite,
		"легкая": "лёгкая", "лёгкая.": "лёгкая", "глубокая": "глубокая", "обычная": "обычная",
	}
	for in, want := range cases {
		got, ok := NormalizeDepth(in)
		if !ok || got != want {
			t.Errorf("NormalizeDepth(%q) = %q,%v; ожидалось %q", in, got, ok, want)
		}
	}
	for _, bad := range []string{"", "medium", "переплавка текста", "Исходный текст."} {
		if _, ok := NormalizeDepth(bad); ok {
			t.Errorf("NormalizeDepth(%q) не должна распознаваться", bad)
		}
	}
}

func TestParseOutlineHandlesFencesAndProse(t *testing.T) {
	o, err := parseOutline("Вот план:\n```json\n" + outlineJSON + "\n```\nГотово.")
	if err != nil || len(o.Sections) != 2 || o.MissingSections[0] != "matchups" {
		t.Fatalf("план не разобран: %+v %v", o, err)
	}
	if _, err := parseOutline("никакого json"); err == nil {
		t.Fatal("ответ без JSON должен давать ошибку")
	}
	if _, err := parseOutline(`{"sections":[],"missing_sections":[]}`); err == nil {
		t.Fatal("пустой план должен давать ошибку")
	}
}

func TestRetryPromptForRewriteAddsHints(t *testing.T) {
	p := retryPromptFor(DepthRewrite, []analyzer.Violation{
		{Kind: "CLAIM_COVERAGE_LOST", Message: "пропала карта"},
		{Kind: "voice_below_norm", Message: "голос ниже нормы"},
		{Kind: "structure_missing", Message: "нет раздела"},
	})
	for _, want := range []string{"Переплавка не принята", "пропала карта", "Верни пропавшие карты", "на «вы»", "не выдумывай"} {
		if !strings.Contains(p, want) {
			t.Errorf("в повторном запросе нет %q: %s", want, p)
		}
	}
	if !strings.Contains(retryPromptFor("обычная", nil), "Правка не принята") {
		t.Error("обычная глубина должна использовать старый повторный запрос")
	}
}

func TestRewriteWithGoogleDocumentSource(t *testing.T) {
	an := &capturedAnalyzer{outlineOK: true, verdicts: []analyzer.Verdict{{Accepted: true}}}
	anSrv := an.server(t)
	defer anSrv.Close()
	service := New(contextualCompleter{completion: llm.Completion{
		Text:       outlineJSON,
		SourceText: "Текст документа с картой Мастер брони.",
	}}, analyzer.New(anSrv.URL, 5*time.Second), 1)
	res, err := service.Edit(context.Background(), Request{
		Text: "https://docs.google.com/document/d/doc_123/edit", Game: "hearthstone",
		Profile: "constructed-guide", Mode: DepthRewrite,
		LLMContext: llm.RequestContext{Tools: []llm.Tool{{Name: "mcp__google-drive__read_google_document"}}},
	})
	if err != nil {
		t.Fatal(err)
	}
	if before, _ := an.validates[0]["before"].(string); before != "Текст документа с картой Мастер брони." {
		t.Fatalf("затвор должен сравнивать с текстом документа, а не с URL: %q", before)
	}
	if res.GoogleDocumentID != "doc_123" {
		t.Fatalf("id документа должен сохраниться для подтверждения: %q", res.GoogleDocumentID)
	}
}
