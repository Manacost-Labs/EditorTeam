package api

import (
	"encoding/json"
	"io"
	"log/slog"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/Manacost-Labs/EditorTeam/go/internal/config"
	"github.com/Manacost-Labs/EditorTeam/go/internal/pipeline"
)

func TestUnknownRetrievalModeIsBadRequest(t *testing.T) {
	server := New(&config.Config{MaxTextBytes: 1 << 20, RequestTimeout: 5e9}, nil, nil, slog.New(slog.NewTextHandler(io.Discard, nil)))
	server.SetPipeline(pipeline.New(nil, nil, "none"))
	handler := server.Routes()
	for _, body := range []string{
		`{"text":"Текст.","mode":"edit","retrieval":"maybe"}`,
		`{"text":"Текст.","mode":"edit","retrieval":"enabled"}`,
		`{"text":"","mode":"edit"}`,
		`{"text":"Текст.","mode":"weird"}`,
	} {
		recorder := httptest.NewRecorder()
		handler.ServeHTTP(recorder, httptest.NewRequest(http.MethodPost, "/v2/edit", strings.NewReader(body)))
		if recorder.Code != http.StatusBadRequest {
			t.Fatalf("%s: code=%d body=%s", body, recorder.Code, recorder.Body.String())
		}
		var payload map[string]any
		_ = json.Unmarshal(recorder.Body.Bytes(), &payload)
		if strings.Contains(body, "retrieval") && payload["error"] != "retrieval должен быть auto, on или off" {
			t.Fatalf("%s: %+v", body, payload)
		}
	}
	for _, mode := range []string{"auto", "on", "off", "Auto", "", " off "} {
		body := `{"text":"Текст.","mode":"edit","retrieval":"` + mode + `"}`
		recorder := httptest.NewRecorder()
		handler.ServeHTTP(recorder, httptest.NewRequest(http.MethodPost, "/v2/edit", strings.NewReader(body)))
		if recorder.Code != http.StatusOK {
			t.Fatalf("mode %q rejected: %d %s", mode, recorder.Code, recorder.Body.String())
		}
	}
}
