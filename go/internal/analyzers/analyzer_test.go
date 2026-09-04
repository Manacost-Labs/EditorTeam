package analyzers

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"strings"
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

func TestValeAdapterPassesMaterialProfileToAllowlistedFile(t *testing.T) {
	if runtime.GOOS == "windows" {
		t.Skip("shell fixture for Unix")
	}
	record := filepath.Join(t.TempDir(), "paths.txt")
	script := filepath.Join(t.TempDir(), "vale-profile")
	content := "#!/bin/sh\nfor arg in \"$@\"; do case \"$arg\" in /*) printf '%s\\n' \"$arg\" >> " + record + " ;; esac; done; printf '{}'\n"
	if err := os.WriteFile(script, []byte(content), 0700); err != nil {
		t.Fatal(err)
	}
	adapter := &ValeAnalyzer{Runner: vale.New(script, "", 10*time.Second)}
	cases := []struct{ profile, want string }{
		{"constructed-guide", "input.guide.md"},
		{"battlegrounds-guide", "input.guide.md"},
		{"news", "input.news.md"},
		{"analytics-article", "input.analysis.md"},
		{"meta-report", "input.meta-report.md"},
		{"unknown-material", "input.md"},
		{"../../../etc/passwd", "input.md"},
	}
	for _, item := range cases {
		result, err := adapter.Analyze(context.Background(), Input{Text: "Текст", Profile: item.profile})
		if err != nil || result.Error != "" || result.Skipped {
			t.Fatalf("%s: profile did not reach Vale runner: %+v, %v", item.profile, result, err)
		}
	}
	data, err := os.ReadFile(record)
	if err != nil {
		t.Fatal(err)
	}
	paths := strings.Split(strings.TrimSpace(string(data)), "\n")
	if len(paths) != len(cases) {
		t.Fatalf("paths: %q", paths)
	}
	for index, item := range cases {
		if filepath.Base(paths[index]) != item.want || strings.Contains(paths[index], "..") {
			t.Fatalf("%s wrote %q, want %s", item.profile, paths[index], item.want)
		}
	}
}

// realValeRunner returns a runner bound to a real Vale binary and the
// repository .vale.ini, or skips the test when Vale is not installed.
func realValeRunner(t *testing.T) *vale.Runner {
	t.Helper()
	binary := os.Getenv("VALE_BIN")
	if binary == "" {
		found, err := exec.LookPath("vale")
		if err != nil {
			t.Skip("real Vale binary is not available; set VALE_BIN")
		}
		binary = found
	}
	config := os.Getenv("VALE_CONFIG")
	if config == "" {
		config = filepath.Join("..", "..", "..", ".vale.ini")
	}
	absolute, err := filepath.Abs(config)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := os.Stat(absolute); err != nil {
		t.Skipf("Vale config not found at %s", absolute)
	}
	return vale.New(binary, absolute, 20*time.Second)
}

func TestValeAdapterAppliesProfileSectionsWithRealBinary(t *testing.T) {
	adapter := &ValeAnalyzer{Runner: realValeRunner(t)}
	const phrase = "Этот вариант гарантированно побеждает любую колоду.\n"
	rules := func(profile string) map[string]bool {
		result, err := adapter.Analyze(context.Background(), Input{Text: phrase, Profile: profile})
		if err != nil || result.Error != "" || result.Skipped {
			t.Fatalf("%s: %+v %v", profile, result, err)
		}
		seen := map[string]bool{}
		for _, item := range result.Findings {
			seen[item.RuleID] = true
			if item.Severity != "suggestion" {
				t.Fatalf("%s: Vale style must stay a suggestion: %+v", profile, item)
			}
		}
		return seen
	}
	for _, guide := range []string{"guide", "constructed-guide", "battlegrounds-guide", "wow-guide"} {
		if rules(guide)["EditorTeam.Overcertainty"] {
			t.Fatalf("%s must disable EditorTeam.Overcertainty", guide)
		}
	}
	for _, other := range []string{"news", "analysis", "analytics-article", "meta-report"} {
		if !rules(other)["EditorTeam.Overcertainty"] {
			t.Fatalf("%s must keep EditorTeam.Overcertainty as a suggestion", other)
		}
	}
	// Unknown and hostile profiles fall back to input.md: the base [*.md]
	// section keeps every rule enabled, and nothing escapes the temp dir.
	for _, fallback := range []string{"unknown", "../../etc/passwd", ""} {
		if !rules(fallback)["EditorTeam.Overcertainty"] {
			t.Fatalf("%q must use the default input.md section", fallback)
		}
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
