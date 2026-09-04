package pipeline

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"log/slog"
	"strings"
	"testing"

	"github.com/Manacost-Labs/EditorTeam/go/internal/analyzers"
	"github.com/Manacost-Labs/EditorTeam/go/internal/llm"
)

// trace is shared by the fake completer and the fake analyzers so tests can
// assert the order of pipeline stages, not only the final JSON.
type trace struct{ events []string }

func (t *trace) add(event string) { t.events = append(t.events, event) }

type fakeLLM struct {
	replies  []string
	fail     map[int]error
	messages [][]llm.Message
	stages   []string
	log      *trace
	i        int
}

func stageOf(system string) string {
	switch {
	case strings.Contains(system, "QA_FINDINGS"):
		return "repair"
	case strings.Contains(system, "Ты critic"):
		return "critic"
	case strings.Contains(system, "Проанализируй текст"):
		return "analysis"
	}
	return "rewrite"
}

func (f *fakeLLM) Model() string { return "fake-model" }
func (f *fakeLLM) Complete(_ context.Context, messages []llm.Message, _ int) (string, error) {
	f.messages = append(f.messages, append([]llm.Message(nil), messages...))
	stage := stageOf(messages[0].Content)
	f.stages = append(f.stages, stage)
	if f.log != nil {
		f.log.add("llm:" + stage)
	}
	index := f.i
	f.i++
	if err, ok := f.fail[index]; ok {
		return "", err
	}
	reply := f.replies[len(f.replies)-1]
	if index < len(f.replies) {
		reply = f.replies[index]
	}
	if stage == "critic" {
		// Critic fixtures quote the real source and candidate so that
		// ValidateImprovements can verify them like a real reply.
		var payload struct{ Source, Candidate string }
		if len(messages) > 1 && json.Unmarshal([]byte(messages[1].Content), &payload) == nil {
			reply = strings.ReplaceAll(reply, "__SOURCE__", jsonEscape(payload.Source))
			reply = strings.ReplaceAll(reply, "__CANDIDATE__", jsonEscape(payload.Candidate))
		}
	}
	return reply, nil
}

func jsonEscape(text string) string {
	raw, _ := json.Marshal(text)
	return strings.Trim(string(raw), `"`)
}

// scriptedAnalyzer returns the findings scripted for each successive call and
// records every input it received.
type scriptedAnalyzer struct {
	name   string
	perRun [][]analyzers.Finding
	inputs []analyzers.Input
	log    *trace
}

func (a *scriptedAnalyzer) Name() string                 { return a.name }
func (a *scriptedAnalyzer) Health(context.Context) error { return nil }
func (a *scriptedAnalyzer) Analyze(_ context.Context, in analyzers.Input) (analyzers.Result, error) {
	call := len(a.inputs)
	a.inputs = append(a.inputs, in)
	if a.log != nil {
		a.log.add("check:" + a.name + ":" + in.Text)
	}
	var findings []analyzers.Finding
	if call < len(a.perRun) {
		findings = a.perRun[call]
	} else if len(a.perRun) > 0 {
		findings = a.perRun[len(a.perRun)-1]
	}
	return analyzers.Result{Analyzer: a.name, Findings: findings}, nil
}

type unavailableAnalyzer struct{}

func (unavailableAnalyzer) Name() string                 { return "missing" }
func (unavailableAnalyzer) Health(context.Context) error { return context.DeadlineExceeded }
func (unavailableAnalyzer) Analyze(context.Context, analyzers.Input) (analyzers.Result, error) {
	return analyzers.Result{Analyzer: "missing", Skipped: true, Error: "инструмент недоступен"}, nil
}

const analysisJSON = `{"thesis":"тезис","audience":"игрок","genre":"гайд","paragraphs":[],"weak_spots":[],"repetitions":[],"unclear":[],"template_phrases":[],"missing_links":[],"factual_risks":[]}`

func scoresJSON(value int) string {
	return fmt.Sprintf(`{"factual_preservation":%d,"meaning_preservation":%d,"clarity":%d,"structure":%d,"usefulness":%d,"natural_russian":%d,"author_voice":%d,"terminology":%d}`, value, value, value, value, value, value, value, value)
}

const improvementJSON = `[{"category":"clarity","before":"__SOURCE__","after":"__CANDIDATE__","reason":"формулировка стала короче и яснее"}]`

// criticJSON builds a consistent critic reply: accept without findings,
// repair with findings and repair_required=true, reject as given. Every
// reply names one improvement so an edited candidate can be accepted.
func criticJSON(verdict string, findings ...analyzers.Finding) string {
	raw, _ := json.Marshal(findings)
	if findings == nil {
		raw = []byte("[]")
	}
	repairRequired := verdict == "repair"
	return fmt.Sprintf(`{"verdict":%q,"scores":%s,"improvements":%s,"regressions":[],"findings":%s,"repair_required":%v}`, verdict, scoresJSON(8), improvementJSON, raw, repairRequired)
}

// criticJSONNoImprovement is a valid accept that names nothing better.
func criticJSONNoImprovement() string {
	return fmt.Sprintf(`{"verdict":"accept","scores":%s,"improvements":[],"regressions":[],"findings":[],"repair_required":false}`, scoresJSON(8))
}

func warning(rule, message string) analyzers.Finding {
	return analyzers.Finding{RuleID: rule, Severity: "warning", Message: message, Line: 1}
}

func lastUserJSON(t *testing.T, messages []llm.Message) map[string]any {
	t.Helper()
	var out map[string]any
	if err := json.Unmarshal([]byte(messages[len(messages)-1].Content), &out); err != nil {
		t.Fatalf("user message is not JSON: %v\n%s", err, messages[len(messages)-1].Content)
	}
	return out
}

func findingsOf(t *testing.T, payload map[string]any, key string) []map[string]any {
	t.Helper()
	raw, _ := payload[key].([]any)
	out := make([]map[string]any, 0, len(raw))
	for _, item := range raw {
		entry, _ := item.(map[string]any)
		out = append(out, entry)
	}
	return out
}

func containsRule(items []map[string]any, rule string) bool {
	for _, item := range items {
		if item["rule_id"] == rule {
			return true
		}
	}
	return false
}

func run(t *testing.T, lm *fakeLLM, text, mode string, checks ...analyzers.Analyzer) *Result {
	t.Helper()
	result, err := New(lm, nil, "test", checks...).Run(context.Background(), Request{Text: text, Mode: mode, Game: "hearthstone", Profile: "constructed-guide"})
	if err != nil {
		t.Fatal(err)
	}
	return result
}

// --- order of stages ---------------------------------------------------------

func TestPipelineOrderPreflightDraftPostflightCriticRepairPostflightCritic(t *testing.T) {
	log := &trace{}
	lm := &fakeLLM{log: log, replies: []string{
		analysisJSON,
		"Черновик.",
		criticJSON("repair", warning("critic.clarity", "уточнить")),
		"Исправлено.",
		criticJSON("accept"),
	}}
	check := &scriptedAnalyzer{name: "tool", log: log}
	result := run(t, lm, "Исходник.", "edit", check)
	want := []string{
		"check:tool:Исходник.",
		"llm:analysis",
		"llm:rewrite",
		"check:tool:Черновик.",
		"llm:critic",
		"llm:repair",
		"check:tool:Исправлено.",
		"llm:critic",
	}
	if strings.Join(log.events, "\n") != strings.Join(want, "\n") {
		t.Fatalf("stage order:\n%s\nwant:\n%s", strings.Join(log.events, "\n"), strings.Join(want, "\n"))
	}
	if !result.Accepted || result.Text != "Исправлено." || result.Attempts != 2 {
		t.Fatalf("result: %+v", result)
	}
}

// --- critic input and JSON contract -----------------------------------------

func TestCriticReceivesSourceCandidateDiffAndToolFindings(t *testing.T) {
	lm := &fakeLLM{replies: []string{analysisJSON, "Кандидат за 3 маны.", criticJSON("accept")}}
	check := &scriptedAnalyzer{name: "vale", perRun: [][]analyzers.Finding{nil, {warning("EditorTeam.AIFrames", "уберите рамку")}}}
	run(t, lm, "Исходник за 3 маны.", "edit", check)
	critic := lm.messages[2]
	if len(critic) != 2 || stageOf(critic[0].Content) != "critic" {
		t.Fatalf("critic call shape: %+v", critic)
	}
	for _, forbidden := range []string{"Исходник за 3 маны.", "Кандидат за 3 маны."} {
		if strings.Contains(critic[0].Content, forbidden) {
			t.Fatalf("system prompt must not carry the texts as instructions: %s", critic[0].Content)
		}
	}
	payload := lastUserJSON(t, critic)
	if payload["source"] != "Исходник за 3 маны." || payload["candidate"] != "Кандидат за 3 маны." || payload["mode"] != "edit" {
		t.Fatalf("critic payload: %+v", payload)
	}
	if diffs, _ := payload["diff"].([]any); len(diffs) != 1 {
		t.Fatalf("critic diff: %+v", payload["diff"])
	}
	if !containsRule(findingsOf(t, payload, "tool_findings"), "EditorTeam.AIFrames") {
		t.Fatalf("critic did not receive tool findings: %+v", payload["tool_findings"])
	}
	for _, key := range []string{"analysis", "source_claims", "protected_entities"} {
		if _, ok := payload[key]; !ok {
			t.Fatalf("critic payload lacks %s: %+v", key, payload)
		}
	}
	if entities, _ := payload["protected_entities"].([]any); len(entities) == 0 {
		t.Fatalf("protected entities missing: %+v", payload)
	}
}

func TestValidCriticJSONIsAccepted(t *testing.T) {
	lm := &fakeLLM{replies: []string{analysisJSON, "Готово.", `{"verdict":"accept","scores":{"factual_preservation":10,"meaning_preservation":9,"clarity":8,"structure":7,"usefulness":6,"natural_russian":5,"author_voice":4,"terminology":3},"improvements":` + improvementJSON + `,"regressions":["ритм ровнее"],"findings":[],"repair_required":false}`}}
	result := run(t, lm, "Текст.", "edit")
	if !result.Accepted || result.Status != StatusEdited || !result.ScoresValid || result.CriticVerdict != "accept" || len(result.RejectionReasons) != 0 {
		t.Fatalf("valid critic: %+v", result)
	}
	if len(result.Improvements) != 1 || result.Improvements[0].Category != "clarity" {
		t.Fatalf("improvements: %+v", result.Improvements)
	}
	want := Scores{FactualPreservation: 10, MeaningPreservation: 9, Clarity: 8, Structure: 7, Usefulness: 6, NaturalRussian: 5, AuthorVoice: 4, Terminology: 3}
	if result.Scores != want || len(result.Regressions) != 1 {
		t.Fatalf("scores: %+v", result)
	}
}

func TestInvalidCriticJSONIsRetriedOnceWithParseError(t *testing.T) {
	lm := &fakeLLM{replies: []string{analysisJSON, "Готово.", "не JSON вовсе", criticJSON("accept")}}
	result := run(t, lm, "Текст.", "edit")
	if lm.stages[2] != "critic" || lm.stages[3] != "critic" || len(lm.stages) != 4 {
		t.Fatalf("critic must be retried exactly once: %v", lm.stages)
	}
	retry := lm.messages[3]
	if len(retry) != 4 || retry[2].Role != "assistant" || retry[2].Content != "не JSON вовсе" {
		t.Fatalf("retry must carry the bad reply: %+v", retry)
	}
	if !strings.Contains(retry[3].Content, "Ответ не разобран") || !strings.Contains(retry[3].Content, "только исправленный JSON") {
		t.Fatalf("retry must state the parse error and demand JSON only: %s", retry[3].Content)
	}
	if !result.Accepted || !result.ScoresValid || result.Text != "Готово." {
		t.Fatalf("retry result: %+v", result)
	}
}

func TestTwoInvalidCriticRepliesReturnSourceWithoutHTTPError(t *testing.T) {
	lm := &fakeLLM{replies: []string{analysisJSON, "Готово.", "мусор", "{\"verdict\":\"accept\"}"}}
	result := run(t, lm, "Текст.", "edit")
	if result.Accepted || result.ChecksComplete || result.ScoresValid || result.Text != "Текст." || len(result.Changes) != 0 {
		t.Fatalf("invalid critic must return the source: %+v", result)
	}
	if strings.Join(result.RejectionReasons, ",") != ReasonCriticInvalid+","+ReasonChecksIncomplete {
		t.Fatalf("rejection reasons: %v", result.RejectionReasons)
	}
	if len(lm.stages) != 4 {
		t.Fatalf("no repair after an invalid critic: %v", lm.stages)
	}
}

func TestCriticScoreElevenIsRejected(t *testing.T) {
	eleven := strings.Replace(criticJSON("accept"), `"clarity":8`, `"clarity":11`, 1)
	lm := &fakeLLM{replies: []string{analysisJSON, "Готово.", eleven, eleven}}
	result := run(t, lm, "Текст.", "edit")
	if result.Accepted || result.ScoresValid || result.Text != "Текст." {
		t.Fatalf("score 11 accepted: %+v", result)
	}
	if !strings.Contains(lm.messages[3][3].Content, "вне диапазона") {
		t.Fatalf("retry must name the range error: %s", lm.messages[3][3].Content)
	}
	for _, bad := range []string{`"clarity":-1`, `"clarity":"5"`, `"clarity":5.5`} {
		if _, err := parseCritic(strings.Replace(criticJSON("accept"), `"clarity":8`, bad, 1)); err == nil {
			t.Fatalf("%s must be invalid", bad)
		}
	}
	if _, err := parseCritic(strings.Replace(criticJSON("accept"), `"clarity":8,`, ``, 1)); err == nil {
		t.Fatal("missing score must be invalid")
	}
	if _, err := parseCritic(strings.Replace(criticJSON("accept"), `"verdict":"accept"`, `"verdict":"maybe"`, 1)); err == nil {
		t.Fatal("unknown verdict must be invalid")
	}
}

func TestNoFakeScoresAreFabricated(t *testing.T) {
	dry, err := New(nil, nil, "none").Run(context.Background(), Request{Text: "Текст.", Mode: "proofread"})
	if err != nil || dry.ScoresValid || dry.Scores != (Scores{}) {
		t.Fatalf("dry-run fabricated scores: %+v %v", dry, err)
	}
	lm := &fakeLLM{replies: []string{analysisJSON, "Готово.", "мусор", "мусор"}}
	invalid := run(t, lm, "Текст.", "edit")
	if invalid.ScoresValid || invalid.Scores != (Scores{}) {
		t.Fatalf("invalid critic fabricated scores: %+v", invalid)
	}
	if strings.Contains(fmt.Sprint(invalid.Scores), "5") {
		t.Fatalf("legacy placeholder 5 is back: %+v", invalid.Scores)
	}
}

// --- postflight findings drive repair ----------------------------------------

func TestPostflightFindingIsPassedToRepair(t *testing.T) {
	lm := &fakeLLM{replies: []string{analysisJSON, "Черновик.", criticJSON("accept"), "Исправлено.", criticJSON("accept")}}
	check := &scriptedAnalyzer{name: "languagetool", perRun: [][]analyzers.Finding{nil, {warning("MORFOLOGIK_RULE_RU_RU", "возможная опечатка: черновик")}, nil}}
	result := run(t, lm, "Исходник.", "edit", check)
	if lm.stages[3] != "repair" {
		t.Fatalf("repair not triggered by postflight finding: %v", lm.stages)
	}
	repair := lm.messages[3]
	payload := lastUserJSON(t, repair)
	if payload["source"] != "Исходник." || payload["candidate"] != "Черновик." {
		t.Fatalf("repair payload: %+v", payload)
	}
	if !containsRule(findingsOf(t, payload, "findings"), "MORFOLOGIK_RULE_RU_RU") {
		t.Fatalf("postflight finding missing from repair: %+v", payload["findings"])
	}
	if !strings.Contains(repair[0].Content, "возможная опечатка: черновик") {
		t.Fatalf("repair system prompt lacks QA message: %s", repair[0].Content)
	}
	if !result.Accepted || result.Text != "Исправлено." {
		t.Fatalf("result: %+v", result)
	}
}

func TestPostflightRunsAgainAfterRepair(t *testing.T) {
	lm := &fakeLLM{replies: []string{analysisJSON, "Черновик.", criticJSON("accept"), "Исправлено.", criticJSON("accept")}}
	check := &scriptedAnalyzer{name: "vale", perRun: [][]analyzers.Finding{nil, {warning("EditorTeam.Intro", "служебное вступление")}, nil}}
	result := run(t, lm, "Исходник.", "edit", check)
	texts := make([]string, 0, len(check.inputs))
	for _, in := range check.inputs {
		texts = append(texts, in.Text)
	}
	if strings.Join(texts, "|") != "Исходник.|Черновик.|Исправлено." {
		t.Fatalf("postflight sequence: %v", texts)
	}
	if check.inputs[2].Before != "Исходник." || check.inputs[2].After != "Исправлено." {
		t.Fatalf("second postflight must compare source with repaired text: %+v", check.inputs[2])
	}
	if lm.stages[4] != "critic" || result.Attempts != 2 || !result.Accepted {
		t.Fatalf("critic must rerun after repair: %v %+v", lm.stages, result)
	}
}

func TestVanishedFindingIsNotSentToSecondRepair(t *testing.T) {
	lm := &fakeLLM{replies: []string{
		analysisJSON, "Черновик.",
		criticJSON("repair", warning("critic.voice", "верните обращение")), "Правка 1.",
		criticJSON("repair", warning("critic.voice", "верните обращение")), "Правка 2.",
		criticJSON("accept"),
	}}
	check := &scriptedAnalyzer{name: "vale", perRun: [][]analyzers.Finding{nil, {warning("EditorTeam.Intro", "служебное вступление")}, nil, nil}}
	result := run(t, lm, "Исходник.", "edit", check)
	if strings.Join(lm.stages, ",") != "analysis,rewrite,critic,repair,critic,repair,critic" {
		t.Fatalf("stages: %v", lm.stages)
	}
	first := findingsOf(t, lastUserJSON(t, lm.messages[3]), "findings")
	second := findingsOf(t, lastUserJSON(t, lm.messages[5]), "findings")
	if !containsRule(first, "EditorTeam.Intro") || !containsRule(first, "critic.voice") {
		t.Fatalf("first repair must carry both findings: %+v", first)
	}
	if containsRule(second, "EditorTeam.Intro") || !containsRule(second, "critic.voice") {
		t.Fatalf("vanished Vale finding leaked into the second repair: %+v", second)
	}
	if !result.Accepted || result.Text != "Правка 2." || result.Attempts != 3 {
		t.Fatalf("result: %+v", result)
	}
}

// --- acceptance rules ---------------------------------------------------------

func TestWarningDoesNotBlockResult(t *testing.T) {
	lm := &fakeLLM{replies: []string{analysisJSON, "Черновик.", criticJSON("accept"), "Черновик.", criticJSON("accept"), "Черновик.", criticJSON("accept")}}
	check := &scriptedAnalyzer{name: "vale", perRun: [][]analyzers.Finding{nil, {warning("EditorTeam.WeakVerb", "слабый глагол")}}}
	result := run(t, lm, "Исходник.", "edit", check)
	if !result.Accepted || result.Text != "Черновик." || len(result.RejectionReasons) != 0 {
		t.Fatalf("persistent warning must not reject: %+v", result)
	}
	if result.Attempts != 1+MaxRepairs {
		t.Fatalf("repairs: %+v", result)
	}
	if !result.ChecksComplete || !result.ScoresValid {
		t.Fatalf("checks: %+v", result)
	}
}

func TestBlockerBlocksResult(t *testing.T) {
	blocker := analyzers.Finding{RuleID: "critic.meaning", Severity: "blocker", Message: "смысл абзаца изменился", Line: 1}
	lm := &fakeLLM{replies: []string{analysisJSON, "Черновик.", criticJSON("reject", blocker)}}
	result := run(t, lm, "Исходник.", "proofread")
	if result.Accepted || result.Status != StatusRejected || result.Text != "Исходник." || len(result.Changes) != 0 {
		t.Fatalf("blocker accepted: %+v", result)
	}
	if strings.Join(result.RejectionReasons, ",") != ReasonCriticRejected {
		t.Fatalf("reasons: %v", result.RejectionReasons)
	}
	if !result.ScoresValid || !result.ChecksComplete {
		t.Fatalf("a valid critic with a blocker still yields real scores: %+v", result)
	}
	if len(lm.stages) != 3 {
		t.Fatalf("proofread must not repair: %v", lm.stages)
	}
}

func TestTwoFailedRepairsReturnSource(t *testing.T) {
	blocker := analyzers.Finding{RuleID: "critic.fact", Severity: "error", Message: "потерян факт", Line: 1}
	lm := &fakeLLM{replies: []string{analysisJSON, "Черновик.", criticJSON("repair", blocker), "Правка 1.", criticJSON("repair", blocker), "Правка 2.", criticJSON("repair", blocker)}}
	result := run(t, lm, "Исходник.", "edit")
	if strings.Join(lm.stages, ",") != "analysis,rewrite,critic,repair,critic,repair,critic" {
		t.Fatalf("stages: %v", lm.stages)
	}
	if result.Accepted || result.Text != "Исходник." || result.Changes != nil || result.Attempts != 3 {
		t.Fatalf("exhausted repair accepted: %+v", result)
	}
	reasons := strings.Join(result.RejectionReasons, ",")
	if !strings.Contains(reasons, ReasonRepairExhausted) || !strings.Contains(reasons, ReasonCriticRejected) {
		t.Fatalf("reasons: %v", result.RejectionReasons)
	}
}

func TestChangedNumberURLOrNegationReturnsSource(t *testing.T) {
	for _, test := range []struct{ name, source, damaged string }{
		{"number", "Карта стоит 3 маны.", "Карта стоит 4 маны."},
		{"url", "Читайте https://example.com.", "Читайте https://evil.example."},
		{"negation", "Не спешите с разменом.", "Спешите с разменом."},
		{"markdown", "# Совет\n\n**Не спешите.**", "Совет\n\nНе спешите."},
	} {
		t.Run(test.name, func(t *testing.T) {
			lm := &fakeLLM{replies: []string{analysisJSON, test.damaged, criticJSON("accept")}}
			result := run(t, lm, test.source, "edit")
			if result.Accepted || result.Text != test.source || len(result.Changes) != 0 {
				t.Fatalf("damage accepted: %+v", result)
			}
			if !strings.Contains(strings.Join(result.RejectionReasons, ","), ReasonProtectedEntityChanged) {
				t.Fatalf("reasons: %v", result.RejectionReasons)
			}
			repair := lastUserJSON(t, lm.messages[3])
			if !containsRule(findingsOf(t, repair, "findings"), "protected_entity_changed") {
				t.Fatalf("guard finding must reach repair so the model can restore the entity: %+v", repair)
			}
		})
	}
}

func TestDuplicateFindingsAreMerged(t *testing.T) {
	dup := analyzers.Finding{RuleID: "EditorTeam.Repeat", Severity: "warning", Message: "повтор", Line: 2, Evidence: "колода  колода"}
	same := dup
	same.Evidence = "Колода колода"
	lm := &fakeLLM{replies: []string{analysisJSON, "Черновик.", criticJSON("accept"), "Исправлено.", criticJSON("accept"), "Исправлено.", criticJSON("accept")}}
	// The same finding is reported three times by one analyzer and once more
	// by a second instance, on every postflight.
	first := &scriptedAnalyzer{name: "vale", perRun: [][]analyzers.Finding{nil, {dup, same, dup}}}
	second := &scriptedAnalyzer{name: "vale", perRun: [][]analyzers.Finding{nil, {dup}}}
	result := run(t, lm, "Исходник.", "edit", first, second)
	for _, index := range []int{3, 5} {
		findings := findingsOf(t, lastUserJSON(t, lm.messages[index]), "findings")
		if len(findings) != 1 || findings[0]["rule_id"] != "EditorTeam.Repeat" {
			t.Fatalf("duplicates were not merged for repair %d: %+v", index, findings)
		}
	}
	if !result.Accepted || result.Attempts != 3 {
		t.Fatalf("persistent warning duplicates must not reject: %+v", result)
	}
	repeats := 0
	for _, item := range result.QAFindings {
		if item.RuleID == "EditorTeam.Repeat" {
			repeats++
		}
	}
	if repeats != 1 {
		t.Fatalf("response still lists duplicates: %+v", result.QAFindings)
	}
	if merged := mergeFindings([]analyzers.Finding{dup, {RuleID: "EditorTeam.Repeat", Severity: "warning", Message: "повтор", Line: 3, Evidence: "колода колода"}}); len(merged) != 2 {
		t.Fatalf("different lines must stay separate: %+v", merged)
	}
}

func TestAnalyzerUnavailableIsNeverSentToTheModel(t *testing.T) {
	lm := &fakeLLM{replies: []string{analysisJSON, "Черновик.", criticJSON("accept"), "Исправлено.", criticJSON("accept")}}
	svc := New(lm, nil, "test", unavailableAnalyzer{}, &scriptedAnalyzer{name: "vale", perRun: [][]analyzers.Finding{nil, {warning("EditorTeam.Intro", "вступление")}, nil}})
	svc.SetAllowUnavailable(true)
	result, err := svc.Run(context.Background(), Request{Text: "Исходник.", Mode: "edit"})
	if err != nil {
		t.Fatal(err)
	}
	if lm.stages[3] != "repair" {
		t.Fatalf("stages: %v", lm.stages)
	}
	for index, call := range lm.messages {
		for _, message := range call {
			if strings.Contains(message.Content, "analyzer_unavailable") || strings.Contains(message.Content, "инструмент недоступен") {
				t.Fatalf("call %d leaked analyzer_unavailable to the model: %s", index, message.Content)
			}
		}
	}
	unavailable := 0
	for _, item := range result.QAFindings {
		if item.RuleID == "analyzer_unavailable" {
			unavailable++
		}
	}
	if unavailable == 0 || result.ChecksComplete {
		t.Fatalf("unavailable analyzer must stay visible to the client: %+v", result)
	}
	// Info findings and empty messages are filtered the same way.
	filtered := repairable([]analyzers.Finding{
		{Analyzer: "natasha-razdel", RuleID: "analyzer_degraded", Severity: "info", Message: "fallback"},
		{Analyzer: "hunspell", RuleID: "hunspell.unknown", Severity: "info", Message: "неизвестное слово"},
		{Analyzer: "vale", RuleID: "EditorTeam.Intro", Severity: "warning", Message: "   "},
		{Analyzer: "vale", RuleID: "EditorTeam.Intro", Severity: "warning", Message: "вступление"},
	})
	if len(filtered) != 1 || filtered[0].Message != "вступление" {
		t.Fatalf("repairable filter: %+v", filtered)
	}
}

func TestPreflightMessagesExcludeUnavailableAnalyzers(t *testing.T) {
	lm := &fakeLLM{replies: []string{analysisJSON, "Черновик.", criticJSON("accept")}}
	svc := New(lm, nil, "test", unavailableAnalyzer{})
	svc.SetAllowUnavailable(true)
	if _, err := svc.Run(context.Background(), Request{Text: "Исходник.", Mode: "edit"}); err != nil {
		t.Fatal(err)
	}
	if strings.Contains(lm.messages[0][0].Content, "инструмент недоступен") {
		t.Fatalf("preflight leaked analyzer_unavailable into the analysis prompt: %s", lm.messages[0][0].Content)
	}
}

// --- legacy behaviour kept --------------------------------------------------

func TestRunWithoutModelIsSafeDryRunAndNeverAccepted(t *testing.T) {
	res, err := New(nil, nil, "none").Run(context.Background(), Request{Text: "Текст.", Mode: "proofread"})
	if err != nil || res.Accepted || res.Status != StatusDryRun || res.Text != "Текст." || res.ScoresValid || len(res.Changes) != 0 {
		t.Fatalf("dry-run: %+v, %v", res, err)
	}
	if !res.ChecksComplete || len(res.RejectionReasons) != 0 {
		t.Fatalf("dry-run with complete checks carries no rejection reason: %+v", res)
	}
}

func TestRunDoesNotAcceptWhenCheckerIsUnavailable(t *testing.T) {
	res, err := New(nil, nil, "none", unavailableAnalyzer{}).Run(context.Background(), Request{Text: "Текст без правок.", Mode: "proofread"})
	if err != nil || res.Accepted || res.Status != StatusDryRun || res.ChecksComplete || len(res.SkippedAnalyzers) != 1 {
		t.Fatalf("unavailable checker: %+v, %v", res, err)
	}
	if strings.Join(res.RejectionReasons, ",") != ReasonChecksIncomplete {
		t.Fatalf("reasons: %v", res.RejectionReasons)
	}
}

func TestPipelineUsesServerSelectedPromptVariant(t *testing.T) {
	lm := &fakeLLM{replies: []string{analysisJSON, "Текст.", criticJSON("accept")}}
	service := New(lm, nil, "test")
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

func TestDraftModelFailureIsStillAnError(t *testing.T) {
	lm := &fakeLLM{replies: []string{analysisJSON}, fail: map[int]error{1: errors.New("модель недоступна")}}
	if _, err := New(lm, nil, "test").Run(context.Background(), Request{Text: "Текст.", Mode: "edit"}); err == nil {
		t.Fatal("draft failure without any candidate must surface as an error")
	}
}

type timeoutError struct{}

func (timeoutError) Error() string   { return "i/o timeout" }
func (timeoutError) Timeout() bool   { return true }
func (timeoutError) Temporary() bool { return true }

func TestCriticTimeoutDiffersFromInvalidJSONAndUnavailable(t *testing.T) {
	cases := []struct {
		name    string
		replies []string
		fail    map[int]error
		want    string
		calls   int
	}{
		{"invalid json retried once", []string{analysisJSON, "Готово.", "мусор", "ещё мусор"}, nil, ReasonCriticInvalid, 4},
		{"context deadline", []string{analysisJSON, "Готово."}, map[int]error{2: fmt.Errorf("запрос: %w", context.DeadlineExceeded)}, ReasonCriticTimeout, 3},
		{"client timeout", []string{analysisJSON, "Готово."}, map[int]error{2: fmt.Errorf("запрос к openai: %w", timeoutError{})}, ReasonCriticTimeout, 3},
		{"http 500", []string{analysisJSON, "Готово."}, map[int]error{2: errors.New("openai вернул 500: overloaded")}, ReasonCriticUnavailable, 3},
		{"network", []string{analysisJSON, "Готово."}, map[int]error{2: errors.New("dial tcp: connection refused")}, ReasonCriticUnavailable, 3},
	}
	for _, test := range cases {
		t.Run(test.name, func(t *testing.T) {
			lm := &fakeLLM{replies: test.replies, fail: test.fail}
			result := run(t, lm, "Текст.", "edit")
			if result.Accepted || result.Status != StatusRejected || result.Text != "Текст." || result.ScoresValid || result.ChecksComplete {
				t.Fatalf("critic failure accepted: %+v", result)
			}
			if got := strings.Join(result.RejectionReasons, ","); got != test.want+","+ReasonChecksIncomplete {
				t.Fatalf("reasons=%s, want %s first", got, test.want)
			}
			if len(lm.stages) != test.calls {
				t.Fatalf("transport failures must not be retried, invalid JSON once: %v", lm.stages)
			}
		})
	}
}

func TestInconsistentCriticReplyIsInvalid(t *testing.T) {
	for name, reply := range map[string]string{
		"repair without findings": fmt.Sprintf(`{"verdict":"repair","scores":%s,"improvements":[],"regressions":[],"findings":[],"repair_required":true}`, scoresJSON(8)),
		"repair not required":     fmt.Sprintf(`{"verdict":"repair","scores":%s,"improvements":[],"regressions":[],"findings":[{"rule_id":"x","severity":"warning","message":"y"}],"repair_required":false}`, scoresJSON(8)),
		"repair info only":        fmt.Sprintf(`{"verdict":"repair","scores":%s,"improvements":[],"regressions":[],"findings":[{"rule_id":"x","severity":"info","message":"y"}],"repair_required":true}`, scoresJSON(8)),
		"accept requiring repair": fmt.Sprintf(`{"verdict":"accept","scores":%s,"improvements":[],"regressions":[],"findings":[],"repair_required":true}`, scoresJSON(8)),
		"accept with blocker":     fmt.Sprintf(`{"verdict":"accept","scores":%s,"improvements":[],"regressions":[],"findings":[{"rule_id":"x","severity":"blocker","message":"y"}],"repair_required":false}`, scoresJSON(8)),
	} {
		t.Run(name, func(t *testing.T) {
			if _, err := parseCritic(reply); err == nil {
				t.Fatalf("%s must be invalid", name)
			}
			lm := &fakeLLM{replies: []string{analysisJSON, "Готово.", reply, reply}}
			result := run(t, lm, "Текст.", "edit")
			if result.Accepted || result.Text != "Текст." || result.RejectionReasons[0] != ReasonCriticInvalid || len(lm.stages) != 4 {
				t.Fatalf("inconsistent reply accepted or not retried once: %+v %v", result, lm.stages)
			}
		})
	}
}

func TestLastCriticVerdictMustBeAccept(t *testing.T) {
	repairReply := criticJSON("repair", warning("critic.voice", "верните обращение"))
	lm := &fakeLLM{replies: []string{analysisJSON, "Черновик.", repairReply, "Правка 1.", repairReply, "Правка 2.", repairReply}}
	result := run(t, lm, "Исходник.", "edit")
	if result.Accepted || result.Text != "Исходник." || result.Attempts != 3 {
		t.Fatalf("persistent repair verdict accepted: %+v", result)
	}
	if strings.Join(result.RejectionReasons, ",") != ReasonRepairExhausted {
		t.Fatalf("reasons: %v", result.RejectionReasons)
	}
	// Proofread never repairs, so a repair verdict cannot end in accept either.
	lm = &fakeLLM{replies: []string{analysisJSON, "Черновик.", repairReply}}
	result = run(t, lm, "Исходник.", "proofread")
	if result.Accepted || strings.Join(result.RejectionReasons, ",") != ReasonRepairExhausted || len(lm.stages) != 3 {
		t.Fatalf("proofread with repair verdict: %+v %v", result, lm.stages)
	}
	// The same sequence ending in accept is taken.
	lm = &fakeLLM{replies: []string{analysisJSON, "Черновик.", repairReply, "Правка 1.", repairReply, "Правка 2.", criticJSON("accept")}}
	result = run(t, lm, "Исходник.", "edit")
	if !result.Accepted || result.Status != StatusEdited || result.Text != "Правка 2." {
		t.Fatalf("final accept not taken: %+v", result)
	}
}

func TestChangeWithoutMeasurableImprovementReturnsSource(t *testing.T) {
	lm := &fakeLLM{replies: []string{analysisJSON, "Готово.", criticJSONNoImprovement()}}
	result := run(t, lm, "Текст.", "edit")
	if result.Accepted || result.Status != StatusUnchanged || result.Text != "Текст." || len(result.Changes) != 0 {
		t.Fatalf("unproven improvement accepted: %+v", result)
	}
	if strings.Join(result.RejectionReasons, ",") != ReasonNoImprovement || !result.ScoresValid {
		t.Fatalf("reasons: %v scores_valid=%v", result.RejectionReasons, result.ScoresValid)
	}
	// A model that returns the text untouched is "unchanged", not an edit.
	lm = &fakeLLM{replies: []string{analysisJSON, "Текст.", criticJSONNoImprovement()}}
	result = run(t, lm, "Текст.", "edit")
	if result.Accepted || result.Status != StatusUnchanged || strings.Join(result.RejectionReasons, ",") != ReasonNoImprovement {
		t.Fatalf("untouched text: %+v", result)
	}
	// Improvements without category or reason do not count.
	empty := fmt.Sprintf(`{"verdict":"accept","scores":%s,"improvements":[{"category":"","reason":""},{"category":"clarity","reason":" "}],"regressions":[],"findings":[],"repair_required":false}`, scoresJSON(8))
	lm = &fakeLLM{replies: []string{analysisJSON, "Готово.", empty}}
	if result = run(t, lm, "Текст.", "edit"); result.Accepted {
		t.Fatalf("empty improvements accepted: %+v", result)
	}
}

func TestRemovedRussianNameBlocksResult(t *testing.T) {
	source := "Рыцарь смерти держит Огненный шар и Темные дары до шестого хода."
	for name, damaged := range map[string]string{
		"class removed":  "Держит Огненный шар и Темные дары до шестого хода.",
		"gift renamed":   "Рыцарь смерти держит Огненный шар и подарки до шестого хода.",
		"name inflected": "Рыцари смерти держат Огненный шар и Темные дары до шестого хода.",
	} {
		t.Run(name, func(t *testing.T) {
			lm := &fakeLLM{replies: []string{analysisJSON, damaged, criticJSON("accept")}}
			result := run(t, lm, source, "edit")
			if result.Accepted || result.Text != source || result.Status != StatusRejected {
				t.Fatalf("removed Russian name accepted: %+v", result)
			}
			if !strings.Contains(strings.Join(result.RejectionReasons, ","), ReasonProtectedEntityChanged) {
				t.Fatalf("reasons: %v", result.RejectionReasons)
			}
		})
	}
}

func TestStructuredStageLogsCarryNoArticleText(t *testing.T) {
	var buffer bytes.Buffer
	lm := &fakeLLM{replies: []string{analysisJSON, "Черновик секретного текста.", criticJSON("repair", warning("critic.voice", "верните обращение")), "Исправлено.", "мусор", "мусор"}}
	svc := New(lm, nil, "openai")
	svc.Log = slog.New(slog.NewJSONHandler(&buffer, nil))
	ctx := WithRequestID(context.Background(), "req-42")
	if _, err := svc.Run(ctx, Request{Text: "Исходный секретный текст.", Mode: "edit"}); err != nil {
		t.Fatal(err)
	}
	logs := buffer.String()
	if strings.Contains(logs, "секретн") {
		t.Fatalf("logs leaked article text: %s", logs)
	}
	stages := map[string]bool{}
	for _, line := range strings.Split(strings.TrimSpace(logs), "\n") {
		var entry map[string]any
		if err := json.Unmarshal([]byte(line), &entry); err != nil {
			t.Fatalf("log line is not JSON: %s", line)
		}
		for _, key := range []string{"request_id", "stage", "provider", "model", "duration_ms", "error_kind", "attempt"} {
			if _, ok := entry[key]; !ok {
				t.Fatalf("log line lacks %s: %s", key, line)
			}
		}
		if entry["request_id"] != "req-42" || entry["provider"] != "openai" || entry["model"] != "fake-model" {
			t.Fatalf("log identity: %s", line)
		}
		stages[entry["stage"].(string)] = true
		if entry["stage"] == "critic_retry" && entry["error_kind"] != ReasonCriticInvalid {
			t.Fatalf("retry must carry error_kind: %s", line)
		}
	}
	for _, want := range []string{"draft", "postflight", "critic", "repair", "critic_retry"} {
		if !stages[want] {
			t.Fatalf("stage %s not logged: %v", want, stages)
		}
	}
}
