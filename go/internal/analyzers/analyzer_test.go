package analyzers

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"runtime"
	"testing"
	"time"

	"github.com/Manacost-Labs/EditorTeam/go/internal/analyzer"
	"github.com/Manacost-Labs/EditorTeam/go/internal/finding"
	"github.com/Manacost-Labs/EditorTeam/go/internal/natasha"
	"github.com/Manacost-Labs/EditorTeam/go/internal/vale"
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

func TestNatashaAdapterMarksRazdelFallbackDegraded(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/analyze" {
			t.Fatalf("endpoint: %s", r.URL.Path)
		}
		_ = json.NewEncoder(w).Encode(natasha.Response{
			Sentences: []natasha.Span{{Text: "Текст", Offset: 0, Length: 5}},
			Findings:  []finding.Finding{{Analyzer: "natasha-razdel", RuleID: "repeat.word", Severity: "warning", Message: "повтор"}},
			Meta:      map[string]any{"engine": "razdel-fallback", "complete": false},
		})
	}))
	defer server.Close()

	adapter := &NatashaAnalyzer{Client: natasha.New(server.URL, time.Second)}
	result, err := adapter.Analyze(context.Background(), Input{Text: "Текст"})
	if err != nil || result.Error == "" || result.Skipped {
		t.Fatalf("adapter: %+v, %v", result, err)
	}
	foundDegraded := false
	foundFallbackFinding := false
	for _, item := range result.Findings {
		if item.Analyzer == "natasha-razdel" && item.RuleID == "analyzer_degraded" && item.Severity == "info" {
			foundDegraded = true
		}
		if item.RuleID == "repeat.word" {
			foundFallbackFinding = true
		}
	}
	if !foundDegraded || !foundFallbackFinding {
		t.Fatalf("findings: %+v", result.Findings)
	}
}

func TestValeAdapterPassesMaterialProfile(t *testing.T) {
	if runtime.GOOS == "windows" {
		t.Skip("shell fixture for Unix")
	}
	dir := t.TempDir()
	script := filepath.Join(dir, "vale-profile")
	content := "#!/bin/sh\ncase \"$*\" in *input.news.md*) printf '%s' '{}' ;; *) exit 9 ;; esac\n"
	if err := os.WriteFile(script, []byte(content), 0700); err != nil {
		t.Fatal(err)
	}
	adapter := &ValeAnalyzer{Runner: vale.New(script, "", 10*time.Second)}
	result, err := adapter.Analyze(context.Background(), Input{Text: "Текст", Profile: "news"})
	if err != nil || result.Error != "" {
		t.Fatalf("profile did not reach Vale runner: %+v, %v", result, err)
	}
}

func TestValeHealthRunsVersionProbe(t *testing.T) {
	if runtime.GOOS == "windows" {
		t.Skip("shell fixture for Unix")
	}
	dir := t.TempDir()
	script := filepath.Join(dir, "broken-vale")
	if err := os.WriteFile(script, []byte("#!/bin/sh\nexit 7\n"), 0700); err != nil {
		t.Fatal(err)
	}
	adapter := &ValeAnalyzer{Runner: vale.New(script, "", time.Second)}
	if err := adapter.Health(context.Background()); err == nil {
		t.Fatal("health must execute Vale instead of checking only its path")
	}
}
