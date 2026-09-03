package pipeline

import (
	"context"
	"strings"
	"testing"

	"github.com/Manacost-Labs/EditorTeam/go/internal/analyzers"
	"github.com/Manacost-Labs/EditorTeam/go/internal/llm"
)

type sequenceLLM struct {
	replies  []string
	messages [][]llm.Message
	i        int
}

func (f *sequenceLLM) Model() string { return "fake-model" }
func (f *sequenceLLM) Complete(_ context.Context, messages []llm.Message, _ int) (string, error) {
	f.messages = append(f.messages, append([]llm.Message(nil), messages...))
	if f.i >= len(f.replies) {
		return f.replies[len(f.replies)-1], nil
	}
	out := f.replies[f.i]
	f.i++
	return out, nil
}

type recordingAnalyzer struct{ inputs []analyzers.Input }

func (r *recordingAnalyzer) Name() string                 { return "recording" }
func (r *recordingAnalyzer) Health(context.Context) error { return nil }
func (r *recordingAnalyzer) Analyze(_ context.Context, in analyzers.Input) (analyzers.Result, error) {
	r.inputs = append(r.inputs, in)
	return analyzers.Result{Analyzer: "recording"}, nil
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

func TestPipelineUsesServerSelectedPromptVariant(t *testing.T) {
	lm := &sequenceLLM{replies: []string{
		`{"thesis":"","audience":"","genre":"","paragraphs":[],"weak_spots":[],"repetitions":[],"unclear":[],"template_phrases":[],"missing_links":[],"factual_risks":[]}`,
		"Текст.",
		`{"scores":{"clarity":5,"structure":5,"usefulness":5,"specificity":5,"voice":5,"accuracy":5,"terminology":5},"findings":[]}`,
	}}
	service := New(lm, nil, "test", staticAnalyzer{})
	service.SetPromptVariant("baseline")
	result, err := service.Run(context.Background(), Request{Text: "Текст.", Mode: "edit"})
	if err != nil {
		t.Fatal(err)
	}
	if result.PromptVariant != "baseline" {
		t.Fatalf("prompt variant=%q", result.PromptVariant)
	}
	for index, messages := range lm.messages {
		if len(messages) == 0 || !strings.Contains(messages[0].Content, "PROMPT_VARIANT: baseline") {
			t.Fatalf("call %d did not receive baseline prompt: %+v", index, messages)
		}
	}
}

func TestPipelineRunsRepairThenPostflight(t *testing.T) {
	lm := &sequenceLLM{replies: []string{
		`{"thesis":"тезис","audience":"игрок","genre":"гайд","paragraphs":[],"weak_spots":[],"repetitions":[],"unclear":[],"template_phrases":[],"missing_links":[],"factual_risks":[]}`,
		"Отредактированный текст.",
		`{"scores":{"clarity":3,"structure":5,"usefulness":5,"specificity":5,"voice":5,"accuracy":5,"terminology":5},"findings":[{"rule_id":"clarity","severity":"warning","message":"уточнить"}]}`,
		"Исправленный текст.",
		`{"scores":{"clarity":5,"structure":5,"usefulness":5,"specificity":5,"voice":5,"accuracy":5,"terminology":5},"findings":[]}`,
	}}
	check := &recordingAnalyzer{}
	result, err := New(lm, nil, "test", check).Run(
		context.Background(), Request{Text: "Исходный текст.", Mode: "edit"},
	)
	if err != nil {
		t.Fatal(err)
	}
	if len(lm.messages) != 5 || lm.messages[2][1].Content != "Отредактированный текст." {
		t.Fatalf("critic did not receive edited text: %+v", lm.messages)
	}
	if !strings.Contains(lm.messages[3][0].Content, "QA_FINDINGS") || lm.messages[3][1].Content != "Отредактированный текст." {
		t.Fatalf("repair call missing findings/candidate: %+v", lm.messages[3])
	}
	if len(check.inputs) != 2 || check.inputs[1].Text != "Исправленный текст." || check.inputs[1].After != "Исправленный текст." {
		t.Fatalf("postflight did not receive repaired text: %+v", check.inputs)
	}
	if result.Attempts != 2 || !result.ChecksComplete || !result.Accepted {
		t.Fatalf("pipeline result: %+v", result)
	}
}

func TestPipelineRejectsProtectedURLAndMarkdownDamage(t *testing.T) {
	for _, test := range []struct {
		name, source, damaged string
	}{
		{"url", "Читайте https://example.com.", "Читайте https://evil.example."},
		{"markdown", "# Совет\n\n**Не спешите.**", "Совет\n\nНе спешите."},
	} {
		t.Run(test.name, func(t *testing.T) {
			lm := &sequenceLLM{replies: []string{
				`{"thesis":"","audience":"","genre":"","paragraphs":[],"weak_spots":[],"repetitions":[],"unclear":[],"template_phrases":[],"missing_links":[],"factual_risks":[]}`,
				test.damaged,
				`{"scores":{"clarity":5,"structure":5,"usefulness":5,"specificity":5,"voice":5,"accuracy":5,"terminology":5},"findings":[]}`,
			}}
			result, err := New(lm, nil, "test").Run(
				context.Background(), Request{Text: test.source, Mode: "edit"},
			)
			if err != nil || result.Accepted || result.Text != test.source {
				t.Fatalf("damage accepted: result=%+v err=%v", result, err)
			}
		})
	}
}
