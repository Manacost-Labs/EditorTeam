package analyzers

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"

	"github.com/Manacost-Labs/EditorTeam/go/internal/analyzer"
)

func TestPythonAdapterMapsSidecarReport(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/analyze" {
			t.Fatalf("endpoint: %s", r.URL.Path)
		}
		_ = json.NewEncoder(w).Encode(map[string]any{"profile": "analytics-article", "findings": []map[string]any{{"id": "clarity.thesis.missing", "severity": "review", "message": "нужен тезис", "line": 2, "evidence": "текст"}}, "metrics": map[string]any{"words": 4}})
	}))
	defer server.Close()
	p := &PythonAnalyzerAdapter{Client: analyzer.New(server.URL, time.Second)}
	result, err := p.Analyze(context.Background(), Input{Text: "Текст", Game: "hearthstone", Profile: "analytics-article", Mode: "GUIDE"})
	if err != nil || len(result.Findings) != 1 || result.Findings[0].RuleID != "clarity.thesis.missing" || result.Findings[0].Severity != "review" {
		t.Fatalf("adapter: %+v, %v", result, err)
	}
}

func TestNativeAnalyzerFlagsProjectTerminology(t *testing.T) {
	result, err := (NativeGoAnalyzer{}).Analyze(context.Background(), Input{Text: "Подарки и племя на Полях сражений"})
	if err != nil || len(result.Findings) != 2 {
		t.Fatalf("native: %+v, %v", result, err)
	}
}

func TestOptionalAnalyzersReportUnavailable(t *testing.T) {
	for _, check := range []Analyzer{
		&NatashaAnalyzer{},
		&HunspellAnalyzer{},
		&MarkdownlintAnalyzer{},
	} {
		result, err := check.Analyze(context.Background(), Input{Text: "Текст"})
		if err != nil || !result.Skipped || result.Error == "" {
			t.Fatalf("%s: %+v, %v", check.Name(), result, err)
		}
	}
}
