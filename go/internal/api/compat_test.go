package api

import (
	"io"
	"log/slog"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"github.com/Manacost-Labs/EditorTeam/go/internal/analyzer"
	"github.com/Manacost-Labs/EditorTeam/go/internal/config"
)

func TestCompatibleAnalyzeEndpointProxiesJSONAndAddsRequestID(t *testing.T) {
	sidecar := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodPost || r.URL.Path != "/analyze" {
			t.Fatalf("запрос к сайдкару: %s %s", r.Method, r.URL.Path)
		}
		w.Header().Set("Content-Type", "application/json")
		io.WriteString(w, `{"game":"hearthstone","findings":[],"metrics":{}}`)
	}))
	defer sidecar.Close()
	cfg := &config.Config{AnalyzerURL: sidecar.URL, RequestTimeout: time.Second, MaxTextBytes: 4096}
	h := New(cfg, nil, analyzer.New(sidecar.URL, time.Second), slog.New(slog.NewTextHandler(io.Discard, nil))).Routes()
	req := httptest.NewRequest(http.MethodPost, "/analyze", strings.NewReader(`{"text":"Текст","game":"hearthstone"}`))
	rec := httptest.NewRecorder()
	h.ServeHTTP(rec, req)
	if rec.Code != http.StatusOK || rec.Header().Get("X-Request-ID") == "" || !strings.Contains(rec.Body.String(), `"game":"hearthstone"`) {
		t.Fatalf("compat: status=%d id=%q body=%s", rec.Code, rec.Header().Get("X-Request-ID"), rec.Body.String())
	}
}

func TestCompatibleValidationRulesAndOutlineEndpoints(t *testing.T) {
	sidecar := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		if r.URL.Path == "/health" {
			io.WriteString(w, `{"ok":true}`)
			return
		}
		io.WriteString(w, `{"accepted":true,"ok":true,"violations":[],"warnings":[]}`)
	}))
	defer sidecar.Close()
	cfg := &config.Config{AnalyzerURL: sidecar.URL, RequestTimeout: time.Second, MaxTextBytes: 4096}
	h := New(cfg, nil, analyzer.New(sidecar.URL, time.Second), slog.New(slog.NewTextHandler(io.Discard, nil))).Routes()
	for _, path := range []string{"/validate", "/rules", "/outline/validate"} {
		req := httptest.NewRequest(http.MethodPost, path, strings.NewReader(`{"text":"Текст","before":"Текст","after":"Текст"}`))
		rec := httptest.NewRecorder()
		h.ServeHTTP(rec, req)
		if rec.Code != http.StatusOK {
			t.Fatalf("%s: status=%d body=%s", path, rec.Code, rec.Body.String())
		}
	}
	req := httptest.NewRequest(http.MethodGet, "/health", nil)
	rec := httptest.NewRecorder()
	h.ServeHTTP(rec, req)
	if rec.Code != http.StatusOK || !strings.Contains(rec.Body.String(), `"ok":true`) {
		t.Fatalf("health: %d %s", rec.Code, rec.Body.String())
	}
}
