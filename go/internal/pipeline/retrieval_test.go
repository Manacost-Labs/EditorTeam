package pipeline

import (
	"context"
	"encoding/json"
	"errors"
	"strings"
	"testing"
	"time"

	"github.com/Manacost-Labs/EditorTeam/go/internal/analyzers"
	"github.com/Manacost-Labs/EditorTeam/go/internal/retrieval"
)

type fakeRetriever struct {
	examples []retrieval.StyleExample
	err      error
	block    bool
	queries  []retrieval.RetrievalQuery
	log      *trace
}

func (f *fakeRetriever) Retrieve(ctx context.Context, query retrieval.RetrievalQuery) ([]retrieval.StyleExample, error) {
	f.queries = append(f.queries, query)
	if f.log != nil {
		f.log.add("retrieve")
	}
	if f.block {
		<-ctx.Done()
		return nil, ctx.Err()
	}
	return f.examples, f.err
}

var corpusExamples = []retrieval.StyleExample{
	{ID: "guide-028#a1", Game: "hearthstone", Profile: "constructed-guide", Excerpt: "В медленных поединках куда важнее станут источники добора. Многое сделают Воевода Саурфанг за 7 маны и Алекстраза, но следите за пулом существ.", VoiceFeatures: []string{"обращение к читателю"}, WhyRelevant: "тот же профиль", Score: 4},
	{ID: "guide-032#b2", Game: "hearthstone", Profile: "constructed-guide", Excerpt: "Размены в медленных поединках нужны очень редко. Если Охотник их и совершает, то с помощью Неистового люторога.", WhyRelevant: "тот же жанр", Score: 3},
}

func runWithRetriever(t *testing.T, lm *fakeLLM, retriever *fakeRetriever, text string, checks ...analyzers.Analyzer) *Result {
	t.Helper()
	svc := New(lm, nil, "test", checks...)
	svc.Retriever = retriever
	svc.RetrievalTimeout = 200 * time.Millisecond
	result, err := svc.Run(context.Background(), Request{Text: text, Mode: "edit", Game: "hearthstone", Profile: "constructed-guide", Author: "manacost"})
	if err != nil {
		t.Fatal(err)
	}
	return result
}

func TestRetrievalRunsAfterPreflightAndBeforeAnalysis(t *testing.T) {
	log := &trace{}
	lm := &fakeLLM{log: log, replies: []string{analysisJSON, "Черновик.", criticJSON("accept")}}
	retriever := &fakeRetriever{examples: corpusExamples, log: log}
	check := &scriptedAnalyzer{name: "tool", log: log}
	result := runWithRetriever(t, lm, retriever, "Исходник.", check)
	want := "check:tool:Исходник.\nretrieve\nllm:analysis\nllm:rewrite\ncheck:tool:Черновик.\nllm:critic"
	if strings.Join(log.events, "\n") != want {
		t.Fatalf("order:\n%s\nwant:\n%s", strings.Join(log.events, "\n"), want)
	}
	query := retriever.queries[0]
	if query.Game != "hearthstone" || query.Profile != "constructed-guide" || query.Author != "manacost" || query.Text != "Исходник." || query.Limit != retrieval.MaxExamples {
		t.Fatalf("query: %+v", query)
	}
	if result.Retrieval.Status != RetrievalOK || result.Retrieval.ExamplesUsed != 2 || strings.Join(result.Retrieval.ExampleIDs, ",") != "guide-028#a1,guide-032#b2" {
		t.Fatalf("report: %+v", result.Retrieval)
	}
}

func TestFakeLLMReceivesStyleExamplesAsSeparateField(t *testing.T) {
	lm := &fakeLLM{replies: []string{analysisJSON, "Черновик.", criticJSON("repair", warning("critic.voice", "верните обращение")), "Исправлено.", criticJSON("accept")}}
	runWithRetriever(t, lm, &fakeRetriever{examples: corpusExamples}, "Исходник.")
	for index, stage := range lm.stages {
		call := lm.messages[index]
		payload := lastUserJSON(t, call)
		examples, _ := payload["style_examples"].([]any)
		if len(examples) != 2 {
			t.Fatalf("%s call lacks style_examples: %+v", stage, payload)
		}
		first, _ := examples[0].(map[string]any)
		if first["excerpt"] != corpusExamples[0].Excerpt || first["id"] != "guide-028#a1" {
			t.Fatalf("%s example shape: %+v", stage, first)
		}
		if _, leaked := first["score"]; leaked {
			t.Fatalf("internal score must not reach the model: %+v", first)
		}
		system := call[0].Content
		for _, rule := range []string{"переносить факты из примеров", "копировать предложения дословно", "добавлять карты, числа и советы из корпуса", "выдавать стиль примера за источник"} {
			if !strings.Contains(system, rule) {
				t.Fatalf("%s system prompt lacks prohibition %q", stage, rule)
			}
		}
		if strings.Contains(system, "Саурфанг") {
			t.Fatalf("%s: excerpt leaked into the system prompt: %s", stage, system)
		}
		if stage == "analysis" || stage == "rewrite" {
			if payload["text"] != "Исходник." {
				t.Fatalf("%s payload text: %+v", stage, payload)
			}
		}
	}
}

func TestExampleFactsNeverBecomeSourceClaimsOrProtectedEntities(t *testing.T) {
	lm := &fakeLLM{replies: []string{analysisJSON, "Черновик за 3 маны.", criticJSON("accept")}}
	runWithRetriever(t, lm, &fakeRetriever{examples: corpusExamples}, "Исходник за 3 маны.")
	critic := lastUserJSON(t, lm.messages[2])
	raw, _ := json.Marshal(map[string]any{"source_claims": critic["source_claims"], "protected_entities": critic["protected_entities"], "analysis": critic["analysis"]})
	for _, fact := range []string{"Саурфанг", "Алекстраза", "7", "люторог"} {
		if strings.Contains(string(raw), fact) {
			t.Fatalf("example fact %q leaked into claims/entities: %s", fact, raw)
		}
	}
	system := lm.messages[2][0].Content
	var bundle map[string]any
	start := strings.Index(system, "{")
	if err := json.Unmarshal([]byte(system[start:strings.LastIndex(system, "}")+1]), &bundle); err == nil {
		encoded, _ := json.Marshal(bundle["source_claims"])
		if strings.Contains(string(encoded), "Саурфанг") {
			t.Fatalf("bundle source_claims contain example facts: %s", encoded)
		}
	}
}

func TestCriticSeesExamplesSourceAndCandidate(t *testing.T) {
	lm := &fakeLLM{replies: []string{analysisJSON, "Черновик.", criticJSON("accept")}}
	runWithRetriever(t, lm, &fakeRetriever{examples: corpusExamples}, "Исходник.")
	payload := lastUserJSON(t, lm.messages[2])
	if payload["source"] != "Исходник." || payload["candidate"] != "Черновик." {
		t.Fatalf("critic payload: %+v", payload)
	}
	if examples, _ := payload["style_examples"].([]any); len(examples) != 2 {
		t.Fatalf("critic lacks style_examples: %+v", payload)
	}
	if !strings.Contains(lm.messages[2][0].Content, "только для оценки author_voice") {
		t.Fatalf("critic must judge examples for voice only: %s", lm.messages[2][0].Content)
	}
}

func TestUnavailableCorpusDoesNotStopEditing(t *testing.T) {
	for name, retriever := range map[string]*fakeRetriever{
		"error":   {err: errors.New("connection refused")},
		"timeout": {block: true},
	} {
		t.Run(name, func(t *testing.T) {
			lm := &fakeLLM{replies: []string{analysisJSON, "Черновик.", criticJSON("accept")}}
			started := time.Now()
			result := runWithRetriever(t, lm, retriever, "Исходник.", &scriptedAnalyzer{name: "tool"})
			if time.Since(started) > 2*time.Second {
				t.Fatal("retrieval timeout must be bounded")
			}
			if !result.Accepted || !result.ChecksComplete || result.Text != "Черновик." {
				t.Fatalf("unavailable corpus blocked editing: %+v", result)
			}
			if result.Retrieval.Status != RetrievalUnavailable || result.Retrieval.ExamplesUsed != 0 || len(result.Retrieval.ExampleIDs) != 0 {
				t.Fatalf("report: %+v", result.Retrieval)
			}
			payload := lastUserJSON(t, lm.messages[1])
			if examples, _ := payload["style_examples"].([]any); len(examples) != 0 {
				t.Fatalf("no examples expected: %+v", payload)
			}
			if strings.Contains(lm.messages[1][0].Content, "ПРИМЕРЫ СТИЛЯ") {
				t.Fatal("style instruction must not appear without examples")
			}
		})
	}
}

func TestRetrievalCanBeDisabledPerRequestAndIsSkippedInDryRun(t *testing.T) {
	retriever := &fakeRetriever{examples: corpusExamples}
	lm := &fakeLLM{replies: []string{analysisJSON, "Черновик.", criticJSON("accept")}}
	svc := New(lm, nil, "test")
	svc.Retriever = retriever
	result, err := svc.Run(context.Background(), Request{Text: "Исходник.", Mode: "edit", Retrieval: "off"})
	if err != nil || result.Retrieval.Status != RetrievalDisabled || len(retriever.queries) != 0 {
		t.Fatalf("retrieval=off: %+v %v", result.Retrieval, err)
	}
	dry := New(nil, nil, "none")
	dry.Retriever = retriever
	result, err = dry.Run(context.Background(), Request{Text: "Исходник.", Mode: "edit"})
	if err != nil || result.Retrieval.Status != RetrievalDisabled || len(retriever.queries) != 0 {
		t.Fatalf("dry-run must not retrieve: %+v %v", result.Retrieval, err)
	}
}

func TestPublicResultCarriesNoExampleText(t *testing.T) {
	lm := &fakeLLM{replies: []string{analysisJSON, "Черновик.", criticJSON("accept")}}
	result := runWithRetriever(t, lm, &fakeRetriever{examples: corpusExamples}, "Исходник.")
	raw, err := json.Marshal(result)
	if err != nil {
		t.Fatal(err)
	}
	for _, fragment := range []string{"Саурфанг", "люторога", "excerpt", "why_relevant"} {
		if strings.Contains(string(raw), fragment) {
			t.Fatalf("public API leaked example text %q: %s", fragment, raw)
		}
	}
	var public map[string]any
	_ = json.Unmarshal(raw, &public)
	report, _ := public["retrieval"].(map[string]any)
	if report["status"] != "ok" || report["examples_used"] != float64(2) {
		t.Fatalf("retrieval metrics: %+v", report)
	}
	if ids, _ := report["example_ids"].([]any); len(ids) != 2 {
		t.Fatalf("example ids: %+v", report)
	}
	if _, ok := report["duration_ms"]; !ok {
		t.Fatalf("duration missing: %+v", report)
	}
}

func TestNumberAndCardNameFromExampleNeverReachTheArticle(t *testing.T) {
	source := "Держите ресурсы для медленных поединков и следите за столом."
	leaked := "Держите ресурсы для медленных поединков: Воевода Саурфанг за 7 маны решает, следите за столом."
	lm := &fakeLLM{replies: []string{analysisJSON, leaked, criticJSON("accept"), leaked, criticJSON("accept"), leaked, criticJSON("accept")}}
	result := runWithRetriever(t, lm, &fakeRetriever{examples: corpusExamples}, source)
	if result.Accepted || result.Text != source || result.Status != StatusRejected {
		t.Fatalf("leaked corpus facts accepted: %+v", result)
	}
	if !strings.Contains(strings.Join(result.RejectionReasons, ","), ReasonCorpusLeak) {
		t.Fatalf("reasons: %v", result.RejectionReasons)
	}
	repair := findingsOf(t, lastUserJSON(t, lm.messages[3]), "findings")
	if !containsRule(repair, "corpus_fact_leak") {
		t.Fatalf("leak must be sent to repair: %+v", repair)
	}
	evidence := []string{}
	for _, item := range result.QAFindings {
		if item.RuleID == "corpus_fact_leak" {
			evidence = append(evidence, item.Evidence)
		}
	}
	joined := strings.Join(evidence, "|")
	if !strings.Contains(joined, "7") || !strings.Contains(joined, "Саурфанг") {
		t.Fatalf("leak evidence must name the number and the card: %v", evidence)
	}
	// The same number already present in the source is not a leak.
	fine := &fakeLLM{replies: []string{analysisJSON, "Карта за 7 маны хороша.", criticJSON("accept")}}
	if ok := runWithRetriever(t, fine, &fakeRetriever{examples: corpusExamples}, "Карта за 7 маны  хороша."); !ok.Accepted {
		t.Fatalf("source-owned number treated as leak: %+v", ok)
	}
	if leaks := corpusLeaks("Исходник.", "Исходник.", corpusExamples); len(leaks) != 0 {
		t.Fatalf("unchanged text cannot leak: %+v", leaks)
	}
	if words := midSentenceCapitalized("Воевода Саурфанг решает. Если Алекстраза зайдёт, ждите."); strings.Join(words, ",") != "Саурфанг,Алекстраза" {
		t.Fatalf("mid-sentence names: %v", words)
	}
}
