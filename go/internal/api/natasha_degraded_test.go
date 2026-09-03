package api

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"

	"github.com/Manacost-Labs/EditorTeam/go/internal/analyzers"
	"github.com/Manacost-Labs/EditorTeam/go/internal/natasha"
	"github.com/Manacost-Labs/EditorTeam/go/internal/pipeline"
)

func TestNatashaFallbackKeepsPipelineChecksIncomplete(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/analyze" {
			t.Fatalf("endpoint: %s", r.URL.Path)
		}
		_ = json.NewEncoder(w).Encode(natasha.Response{
			Sentences: []natasha.Span{{Text: "Текст", Offset: 0, Length: 5}},
			Meta:      map[string]any{"engine": "razdel-fallback", "complete": false},
		})
	}))
	defer server.Close()

	checker := &analyzers.NatashaAnalyzer{Client: natasha.New(server.URL, time.Second)}
	result, err := pipeline.New(nil, nil, "none", checker).Run(context.Background(), pipeline.Request{Text: "Текст", Mode: "proofread"})
	if err != nil {
		t.Fatal(err)
	}
	if result.ChecksComplete || result.Accepted {
		t.Fatalf("fallback must be incomplete: %+v", result)
	}
	found := false
	for _, item := range result.QAFindings {
		if item.RuleID == "analyzer_unavailable" {
			t.Fatalf("degraded analyzer must not also be mislabeled unavailable: %+v", result.QAFindings)
		}
		if item.Analyzer == "natasha-razdel" && item.RuleID == "analyzer_degraded" && item.Severity == "info" {
			found = true
		}
	}
	if !found {
		t.Fatalf("missing degraded finding: %+v", result.QAFindings)
	}
}
