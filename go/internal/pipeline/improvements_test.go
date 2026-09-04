package pipeline

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"log/slog"
	"strings"
	"testing"

	"github.com/Manacost-Labs/EditorTeam/go/internal/retrieval"
)

const (
	improvementSource    = "В этой статье мы рассмотрим колоду.\nОна может  хорошо играть, стоит отметить."
	improvementCandidate = "Разберём колоду.\nОна может хорошо играть."
)

func TestValidateImprovementsRejectsFabricatedAndEmptyFragments(t *testing.T) {
	cases := map[string]Improvement{
		"fabricated":        {Category: "clarity", Before: "Текст был громоздким", After: "Текст стал ясным", Reason: "убрана вводная и повтор"},
		"generic only":      {Category: "clarity", Reason: "Текст стал яснее"},
		"empty before":      {Category: "clarity", Before: "", After: "Разберём колоду.", Reason: "убрана служебная рамка статьи"},
		"empty after":       {Category: "clarity", Before: "В этой статье мы рассмотрим колоду.", After: "", Reason: "убрана служебная рамка статьи"},
		"before not source": {Category: "clarity", Before: "Сегодня мы рассмотрим колоду.", After: "Разберём колоду.", Reason: "убрана служебная рамка статьи"},
		"after not cand":    {Category: "clarity", Before: "В этой статье мы рассмотрим колоду.", After: "Разберем колоду.", Reason: "убрана служебная рамка статьи"},
		"unchanged":         {Category: "clarity", Before: "хорошо играть", After: "хорошо играть", Reason: "ничего не изменилось на деле"},
		"punctuation only":  {Category: "clarity", Before: ".", After: ",", Reason: "запятая вместо точки в конце"},
		"bad category":      {Category: "vibes", Before: "В этой статье мы рассмотрим колоду.", After: "Разберём колоду.", Reason: "убрана служебная рамка статьи"},
		"vague reason":      {Category: "clarity", Before: "В этой статье мы рассмотрим колоду.", After: "Разберём колоду.", Reason: "лучше"},
		"too long":          {Category: "clarity", Before: strings.Repeat("х", 301), After: "Разберём колоду.", Reason: "убрана служебная рамка статьи"},
	}
	for name, item := range cases {
		if got := ValidateImprovements(improvementSource, improvementCandidate, []Improvement{item}); len(got) != 0 {
			t.Fatalf("%s must be rejected: %+v", name, got)
		}
	}
}

func TestValidateImprovementsAcceptsRealQuotesAndMergesDuplicates(t *testing.T) {
	real := Improvement{Category: "Clarity", Before: "В этой статье мы\nрассмотрим колоду.", After: "Разберём колоду.", Reason: "служебная рамка заменена прямым тезисом"}
	duplicate := Improvement{Category: "clarity", Before: "В этой статье  мы рассмотрим колоду.", After: "Разберём  колоду.", Reason: "та же правка описана ещё раз"}
	second := Improvement{Category: "conciseness", Before: "хорошо играть, стоит отметить.", After: "хорошо играть.", Reason: "убрана пустая вводная в конце"}
	got := ValidateImprovements(improvementSource, improvementCandidate, []Improvement{real, duplicate, second})
	if len(got) != 2 {
		t.Fatalf("expected two proven improvements, got %+v", got)
	}
	if got[0].Category != "clarity" || got[0].Before != "В этой статье мы рассмотрим колоду." || got[0].After != "Разберём колоду." {
		t.Fatalf("normalised quote: %+v", got[0])
	}
	if got[1].Category != "conciseness" {
		t.Fatalf("second improvement: %+v", got[1])
	}
}

func criticWithImprovements(improvements string) string {
	return fmt.Sprintf(`{"verdict":"accept","scores":%s,"improvements":%s,"regressions":[],"findings":[],"repair_required":false}`, scoresJSON(8), improvements)
}

func TestFabricatedImprovementReturnsSourceEvenWithAcceptVerdict(t *testing.T) {
	fabricated := criticWithImprovements(`[{"category":"clarity","before":"Текст был тяжёлым","after":"Текст стал ясным","reason":"стало гораздо яснее читать"}]`)
	lm := &fakeLLM{replies: []string{analysisJSON, improvementCandidate, fabricated}}
	result := run(t, lm, improvementSource, "edit")
	if result.Accepted || result.Status != StatusUnchanged || result.Text != improvementSource || len(result.Improvements) != 0 {
		t.Fatalf("fabricated improvement accepted: %+v", result)
	}
	if strings.Join(result.RejectionReasons, ",") != ReasonNoImprovement || result.CriticVerdict != "accept" {
		t.Fatalf("reasons: %v verdict=%s", result.RejectionReasons, result.CriticVerdict)
	}
	real := criticWithImprovements(`[{"category":"clarity","before":"В этой статье мы рассмотрим колоду.","after":"Разберём колоду.","reason":"служебная рамка заменена прямым тезисом"}]`)
	lm = &fakeLLM{replies: []string{analysisJSON, improvementCandidate, real}}
	result = run(t, lm, improvementSource, "edit")
	if !result.Accepted || result.Status != StatusEdited || len(result.Improvements) != 1 {
		t.Fatalf("proven improvement rejected: %+v", result)
	}
	if !strings.Contains(lm.messages[2][0].Content, "before должен быть дословной выдержкой из source") || !strings.Contains(lm.messages[2][0].Content, "Не выдумывай отсутствующие фрагменты") {
		t.Fatalf("critic prompt lacks quoting rules: %s", lm.messages[2][0].Content)
	}
}

// --- runtime copy guard --------------------------------------------------------

const exampleText = "Оставляйте монету для ключевого хода, если соперник не давит на стол и не выставляет угрозы каждый ход подряд, потому что ранний темп часто важнее красивого финального стола в этой мете."

func exampleWith(text string) []retrieval.StyleExample {
	return []retrieval.StyleExample{{ID: "guide-1#x", Game: "hearthstone", Profile: "constructed-guide", Author: "manacost", Excerpt: text}}
}

func copyFindings(source, candidate string) []string {
	var out []string
	for _, item := range DetectCorpusCopy(source, candidate, exampleWith(exampleText)) {
		out = append(out, fmt.Sprintf("%s:%d", item.Severity, item.Length))
	}
	return out
}

func TestDetectCorpusCopyThresholds(t *testing.T) {
	source := "Исходный совет про стол."
	if got := copyFindings(source, "Держите ресурсы. Оставляйте монету для ключевого хода — и всё."); len(got) != 0 {
		t.Fatalf("five shared words are a common phrase, not a copy: %v", got)
	}
	ten := "Держите ресурсы. **Оставляйте** монету для ключевого хода, если соперник не давит на стол — и хватит."
	if got := copyFindings(source, ten); len(got) != 1 || !strings.HasPrefix(got[0], "warning:1") {
		t.Fatalf("ten shared words must be a warning: %v", got)
	}
	fourteen := "Держите ресурсы. Оставляйте монету для ключевого хода, если соперник не давит на стол и не выставляет угрозы каждый ход подряд, и хватит."
	if got := copyFindings(source, fourteen); len(got) != 1 || !strings.HasPrefix(got[0], "error:") {
		t.Fatalf("fourteen shared words must be an error: %v", got)
	}
	two := "Оставляйте монету для ключевого хода, если соперник не давит на стол. Своё предложение. Потому что ранний темп часто важнее красивого финального стола в этой мете, знаете."
	got := copyFindings(source, two)
	if len(got) != 2 || got[0] != "error:11" || got[1] != "error:12" {
		t.Fatalf("two matches of ten or more words from one example must be an error: %v", got)
	}
	findings := DetectCorpusCopy(source, fourteen, exampleWith(exampleText))
	if findings[0].Analyzer != "guards" || findings[0].RuleID != "corpus_copy" || findings[0].Message != "Кандидат дословно копирует фрагмент стилевого примера" {
		t.Fatalf("finding shape: %+v", findings[0])
	}
	if len([]rune(findings[0].Evidence)) > 160 || strings.Contains(findings[0].Evidence, "в этой мете") {
		t.Fatalf("evidence must be short and not the whole example: %q", findings[0].Evidence)
	}
}

func TestDetectCorpusCopyIgnoresFragmentsAlreadyInSource(t *testing.T) {
	source := "Оставляйте монету для ключевого хода, если соперник не давит на стол и не выставляет угрозы каждый ход подряд."
	candidate := "Оставляйте монету для ключевого хода, если соперник не давит на стол и не выставляет угрозы каждый ход подряд. Добавлено."
	if got := DetectCorpusCopy(source, candidate, exampleWith(exampleText)); len(got) != 0 {
		t.Fatalf("author's own sentence is not a leak: %+v", got)
	}
	if got := DetectCorpusCopy(source, candidate, nil); len(got) != 0 {
		t.Fatalf("no examples, no copies: %+v", got)
	}
}

func TestCorpusCopyGoesToRepairAndDisappearsAfterFix(t *testing.T) {
	source := "Держите ресурсы для медленных поединков и следите за столом."
	copied := source + " Оставляйте монету для ключевого хода, если соперник не давит на стол и не выставляет угрозы каждый ход подряд."
	fixed := "Держите ресурсы для медленных поединков и следите за столом. Монету берегите."
	improvement := `[{"category":"conciseness","before":"Держите ресурсы для медленных поединков и следите за столом.","after":"Монету берегите.","reason":"добавлен короткий конкретный совет"}]`
	lm := &fakeLLM{replies: []string{analysisJSON, copied, criticWithImprovements(improvement), fixed, criticWithImprovements(improvement)}}
	result := runWithRetriever(t, lm, &fakeRetriever{examples: exampleWith(exampleText)}, source)
	if lm.stages[3] != "repair" {
		t.Fatalf("copy must trigger repair: %v", lm.stages)
	}
	if !containsRule(findingsOf(t, lastUserJSON(t, lm.messages[3]), "findings"), "corpus_copy") {
		t.Fatal("corpus_copy finding must reach repair")
	}
	if !result.Accepted || result.Text != fixed || !result.Retrieval.CopyGuardTriggered {
		t.Fatalf("repaired copy must be accepted with the guard recorded: %+v", result)
	}
	for _, item := range result.QAFindings {
		if item.RuleID == "corpus_copy" {
			t.Fatalf("copy finding must vanish after repair: %+v", item)
		}
	}
}

func TestPersistentCorpusCopyReturnsSourceAfterTwoRepairs(t *testing.T) {
	source := "Держите ресурсы для медленных поединков и следите за столом."
	copied := source + " Оставляйте монету для ключевого хода, если соперник не давит на стол и не выставляет угрозы каждый ход подряд."
	improvement := `[{"category":"usefulness","before":"следите за столом.","after":"следите за столом. Оставляйте монету","reason":"добавлен совет про монету"}]`
	reply := criticWithImprovements(improvement)
	lm := &fakeLLM{replies: []string{analysisJSON, copied, reply, copied, reply, copied, reply}}
	result := runWithRetriever(t, lm, &fakeRetriever{examples: exampleWith(exampleText)}, source)
	if result.Accepted || result.Text != source || result.Attempts != 3 {
		t.Fatalf("persistent copy accepted: %+v", result)
	}
	reasons := strings.Join(result.RejectionReasons, ",")
	if !strings.Contains(reasons, ReasonCorpusCopy) || !strings.Contains(reasons, ReasonRepairExhausted) {
		t.Fatalf("reasons: %v", result.RejectionReasons)
	}
	if !result.Retrieval.CopyGuardTriggered {
		t.Fatalf("report: %+v", result.Retrieval)
	}
}

// --- retrieval modes -------------------------------------------------------------

func TestRetrievalModesFollowAutoOnOff(t *testing.T) {
	cases := []struct {
		name, mode, request, config, want string
		called                            bool
	}{
		{"proofread auto", "proofread", "", "auto", RetrievalDisabledByMode, false},
		{"edit auto", "edit", "auto", "auto", RetrievalOK, true},
		{"rewrite auto", "rewrite", "", "auto", RetrievalOK, true},
		{"proofread on", "proofread", "on", "auto", RetrievalOK, true},
		{"edit off", "edit", "off", "auto", RetrievalDisabledByRequest, false},
		{"config off", "edit", "on", "off", RetrievalDisabledByConfig, false},
		{"config on proofread", "proofread", "", "on", RetrievalOK, true},
	}
	for _, test := range cases {
		t.Run(test.name, func(t *testing.T) {
			retriever := &fakeRetriever{examples: corpusExamples}
			lm := &fakeLLM{replies: []string{analysisJSON, "Черновик.", criticJSON("accept")}}
			svc := New(lm, nil, "test")
			svc.Retriever = retriever
			svc.RetrievalMode = test.config
			result, err := svc.Run(context.Background(), Request{Text: "Исходник.", Mode: test.mode, Retrieval: test.request})
			if err != nil {
				t.Fatal(err)
			}
			if result.Retrieval.Status != test.want || (len(retriever.queries) > 0) != test.called {
				t.Fatalf("status=%s called=%d want %s/%v", result.Retrieval.Status, len(retriever.queries), test.want, test.called)
			}
		})
	}
	svc := New(&fakeLLM{replies: []string{analysisJSON}}, nil, "test")
	if _, err := svc.Run(context.Background(), Request{Text: "Исходник.", Mode: "edit", Retrieval: "maybe"}); err == nil || err.Error() != "retrieval должен быть auto, on или off" {
		t.Fatalf("unknown mode: %v", err)
	}
}

func TestRetrievalTimeoutIsReportedSeparatelyAndKeepsChecksComplete(t *testing.T) {
	lm := &fakeLLM{replies: []string{analysisJSON, "Черновик.", criticJSON("accept")}}
	result := runWithRetriever(t, lm, &fakeRetriever{block: true}, "Исходник.", &scriptedAnalyzer{name: "tool"})
	if result.Retrieval.Status != RetrievalTimeout || !result.ChecksComplete || !result.Accepted {
		t.Fatalf("timeout: %+v", result)
	}
}

func TestAuthorAndExcerptNeverReachPublicResultOrLogs(t *testing.T) {
	var buffer bytes.Buffer
	lm := &fakeLLM{replies: []string{analysisJSON, "Черновик.", criticJSON("accept")}}
	svc := New(lm, nil, "openai")
	svc.Log = slog.New(slog.NewJSONHandler(&buffer, nil))
	svc.Retriever = &fakeRetriever{examples: []retrieval.StyleExample{{ID: "g#1", Game: "hearthstone", Profile: "constructed-guide", Author: "secret-author", Excerpt: "Уникальная фраза корпуса про Саурфанга.", WhyRelevant: "тот же профиль"}}}
	result, err := svc.Run(WithRequestID(context.Background(), "r1"), Request{Text: "Исходник.", Mode: "edit", Author: "secret-author"})
	if err != nil {
		t.Fatal(err)
	}
	raw, _ := json.Marshal(result)
	for _, fragment := range []string{"secret-author", "Уникальная фраза", "author\"", "excerpt"} {
		if strings.Contains(string(raw), fragment) {
			t.Fatalf("public result leaked %q: %s", fragment, raw)
		}
	}
	for _, fragment := range []string{"Уникальная фраза", "Саурфанг", "secret-author", "Исходник"} {
		if strings.Contains(buffer.String(), fragment) {
			t.Fatalf("logs leaked %q: %s", fragment, buffer.String())
		}
	}
	if !strings.Contains(buffer.String(), `"stage":"retrieval"`) {
		t.Fatalf("retrieval stage must be logged: %s", buffer.String())
	}
}
