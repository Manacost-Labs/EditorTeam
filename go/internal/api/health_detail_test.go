package api

import (
	"encoding/json"
	"io"
	"log/slog"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"

	"github.com/Manacost-Labs/EditorTeam/go/internal/analyzer"
	"github.com/Manacost-Labs/EditorTeam/go/internal/analyzers"
	"github.com/Manacost-Labs/EditorTeam/go/internal/config"
	"github.com/Manacost-Labs/EditorTeam/go/internal/natasha"
	"github.com/Manacost-Labs/EditorTeam/go/internal/pipeline"
)

func TestHealthExposesNatashaDetailAndKeepsChecksIncompleteWhenDegraded(t *testing.T) {
	python := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		_, _ = io.WriteString(w, `{"ok":true}`)
	}))
	degraded := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		_ = json.NewEncoder(w).Encode(natasha.HealthResponse{
			OK: true, Complete: false, Service: "natasha-razdel",
			Natasha: natasha.HealthDetail{
				Status: "degraded", Complete: false, Engine: "razdel-fallback", Version: "natasha-razdel-v2",
			},
		})
	}))
	defer python.Close()
	defer degraded.Close()

	cfg := &config.Config{AnalyzerURL: python.URL, RequestTimeout: time.Second, MaxTextBytes: 4096}
	server := New(cfg, nil, analyzer.New(python.URL, time.Second), slog.New(slog.NewTextHandler(io.Discard, nil)))
	server.SetPipeline(pipeline.New(nil, nil, "none", &analyzers.NatashaAnalyzer{
		Client: natasha.New(degraded.URL, time.Second),
	}))
	recorder := httptest.NewRecorder()
	server.Routes().ServeHTTP(recorder, httptest.NewRequest(http.MethodGet, "/health", nil))

	if recorder.Code != http.StatusOK {
		t.Fatalf("health status=%d body=%s", recorder.Code, recorder.Body.String())
	}
	var body struct {
		ChecksComplete bool                 `json:"checks_complete"`
		Natasha        natasha.HealthDetail `json:"natasha"`
	}
	if err := json.Unmarshal(recorder.Body.Bytes(), &body); err != nil {
		t.Fatal(err)
	}
	if body.ChecksComplete || body.Natasha.Status != "degraded" || body.Natasha.Complete {
		t.Fatalf("unexpected health: %+v", body)
	}
}
