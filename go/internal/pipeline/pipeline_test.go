package pipeline

import (
	"context"
	"testing"

	"github.com/Manacost-Labs/EditorTeam/go/internal/analyzers"
	"github.com/Manacost-Labs/EditorTeam/go/internal/llm"
)

type sequenceLLM struct {
	replies []string
	i       int
}

func (f *sequenceLLM) Model() string { return "fake-model" }
func (f *sequenceLLM) Complete(_ context.Context, _ []llm.Message, _ int) (string, error) {
	if f.i >= len(f.replies) {
		return f.replies[len(f.replies)-1], nil
	}
	out := f.replies[f.i]
	f.i++
	return out, nil
}

type staticAnalyzer struct{}

func (staticAnalyzer) Name() string                 { return "test" }
func (staticAnalyzer) Health(context.Context) error { return nil }
func (staticAnalyzer) Analyze(context.Context, analyzers.Input) (analyzers.Result, error) {
	return analyzers.Result{Analyzer: "test"}, nil
}

type unavailableAnalyzer struct{}

func (unavailableAnalyzer) Name() string                 { return "missing" }
func (unavailableAnalyzer) Health(context.Context) error { return context.DeadlineExceeded }
func (unavailableAnalyzer) Analyze(context.Context, analyzers.Input) (analyzers.Result, error) {
	return analyzers.Result{Analyzer: "missing", Skipped: true, Error: "инструмент недоступен"}, nil
}

func TestRunSeparatesAnalysisRewriteAndCritic(t *testing.T) {
	lm := &sequenceLLM{replies: []string{
		`{"thesis":"тезис","audience":"игрок","genre":"гайд","paragraphs":["вступление"],"weak_spots":[],"repetitions":[],"unclear":[],"template_phrases":[],"missing_links":[],"factual_risks":[]}`,
		"Готовый текст за 3 маны.",
		`{"scores":{"clarity":5,"structure":4,"usefulness":5,"specificity":4,"voice":5,"accuracy":5,"terminology":5},"findings":[]}`,
	}}
	svc := New(lm, nil, "test", staticAnalyzer{})
	res, err := svc.Run(context.Background(), Request{Text: "Исходный текст за 3 маны.", Mode: "edit", Game: "hearthstone"})
	if err != nil {
		t.Fatal(err)
	}
	if !res.Accepted || res.Text != "Готовый текст за 3 маны." || res.Analysis.Thesis != "тезис" {
		t.Fatalf("pipeline: %+v", res)
	}
	if res.PromptVersion == "" || res.Model != "fake-model" || len(res.Changes) != 1 {
		t.Fatalf("контракт результата: %+v", res)
	}
}

func TestRunRejectsChangedProtectedNumberAndReturnsSource(t *testing.T) {
	lm := &sequenceLLM{replies: []string{
		`{"thesis":"","audience":"","genre":"","paragraphs":[],"weak_spots":[],"repetitions":[],"unclear":[],"template_phrases":[],"missing_links":[],"factual_risks":[]}`,
		"Готовый текст за 4 маны.",
		`{"scores":{"clarity":5,"structure":5,"usefulness":5,"specificity":5,"voice":5,"accuracy":5,"terminology":5},"findings":[]}`,
	}}
	res, err := New(lm, nil, "test").Run(context.Background(), Request{Text: "Исходный текст за 3 маны.", Mode: "edit"})
	if err != nil {
		t.Fatal(err)
	}
	if res.Accepted || res.Text != "Исходный текст за 3 маны." || len(res.FactualRisks) == 0 {
		t.Fatalf("защита числа: %+v", res)
	}
}

func TestRunWithoutModelIsSafeDryRun(t *testing.T) {
	res, err := New(nil, nil, "none").Run(context.Background(), Request{Text: "Текст.", Mode: "proofread"})
	if err != nil || !res.Accepted || res.Text != "Текст." {
		t.Fatalf("dry-run: %+v, %v", res, err)
	}
}

func TestRunDoesNotAcceptWhenCheckerIsUnavailable(t *testing.T) {
	res, err := New(nil, nil, "none", unavailableAnalyzer{}).Run(context.Background(), Request{Text: "Текст без правок.", Mode: "proofread"})
	if err != nil || res.Accepted || res.ChecksComplete || len(res.SkippedAnalyzers) != 1 {
		t.Fatalf("unavailable checker: %+v, %v", res, err)
	}
}
