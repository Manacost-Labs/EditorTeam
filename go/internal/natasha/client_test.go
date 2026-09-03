package natasha

import (
	"context"
	"encoding/json"
	"errors"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"
)

func TestClientAcceptsCompleteNatashaResponses(t *testing.T) {
	s := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch r.URL.Path {
		case "/health":
			_ = json.NewEncoder(w).Encode(HealthResponse{OK: true, Complete: true, Service: "natasha-razdel"})
		case "/analyze":
			_ = json.NewEncoder(w).Encode(Response{
				Sentences: []Span{{Text: "Текст", Offset: 0, Length: 5, Lemma: "текст", POS: "NOUN"}},
				Meta:      map[string]any{"engine": "natasha+razdel", "complete": true},
			})
		default:
			http.NotFound(w, r)
		}
	}))
	defer s.Close()

	c := New(s.URL, time.Second)
	health, err := c.HealthStatus(context.Background())
	if err != nil || !health.OK || !health.Complete || health.Service != "natasha-razdel" {
		t.Fatalf("health: %+v, %v", health, err)
	}
	if err := c.Health(context.Background()); err != nil {
		t.Fatal(err)
	}
	out, err := c.Analyze(context.Background(), Input{Text: "Текст", Language: "ru"})
	if err != nil || len(out.Sentences) != 1 || out.Sentences[0].Lemma != "текст" {
		t.Fatalf("response: %+v, %v", out, err)
	}
}

func TestClientRejectsDegradedHealth(t *testing.T) {
	for _, tc := range []struct {
		name string
		body string
	}{
		{name: "ok_false", body: `{"ok":false,"complete":true}`},
		{name: "complete_false", body: `{"ok":true,"complete":false}`},
	} {
		t.Run(tc.name, func(t *testing.T) {
			s := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
				_, _ = w.Write([]byte(tc.body))
			}))
			defer s.Close()

			status, err := New(s.URL, time.Second).HealthStatus(context.Background())
			if status == nil || !errors.Is(err, ErrDegraded) {
				t.Fatalf("status=%+v err=%v", status, err)
			}
		})
	}
}

func TestClientRejectsIncompleteAnalyzeAndPreservesFallbackResponse(t *testing.T) {
	s := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		_ = json.NewEncoder(w).Encode(Response{
			Sentences: []Span{{Text: "Текст", Offset: 0, Length: 5}},
			Meta:      map[string]any{"engine": "razdel-fallback", "complete": false},
		})
	}))
	defer s.Close()

	out, err := New(s.URL, time.Second).Analyze(context.Background(), Input{Text: "Текст"})
	if out == nil || len(out.Sentences) != 1 || !errors.Is(err, ErrDegraded) {
		t.Fatalf("response=%+v err=%v", out, err)
	}
}

func TestClientRejectsInvalidHealthResponses(t *testing.T) {
	for _, tc := range []struct {
		name    string
		status  int
		body    string
		delay   time.Duration
		wantErr string
	}{
		{name: "malformed_json", status: http.StatusOK, body: `{`, wantErr: "не JSON"},
		{name: "http_500", status: http.StatusInternalServerError, body: `{"error":"boom"}`, wantErr: "500"},
		{name: "timeout", status: http.StatusOK, body: `{"ok":true,"complete":true}`, delay: 100 * time.Millisecond, wantErr: "недоступен"},
	} {
		t.Run(tc.name, func(t *testing.T) {
			s := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
				time.Sleep(tc.delay)
				w.WriteHeader(tc.status)
				_, _ = w.Write([]byte(tc.body))
			}))
			defer s.Close()

			timeout := time.Second
			if tc.delay > 0 {
				timeout = 10 * time.Millisecond
			}
			_, err := New(s.URL, timeout).HealthStatus(context.Background())
			if err == nil || !strings.Contains(err.Error(), tc.wantErr) {
				t.Fatalf("err=%v, want substring %q", err, tc.wantErr)
			}
		})
	}
}

func TestClientRejectsInvalidAnalyzeResponses(t *testing.T) {
	for _, tc := range []struct {
		name    string
		status  int
		body    string
		delay   time.Duration
		wantErr string
	}{
		{name: "malformed_json", status: http.StatusOK, body: `{`, wantErr: "не JSON"},
		{name: "http_500", status: http.StatusInternalServerError, body: `{"error":"boom"}`, wantErr: "500"},
		{name: "timeout", status: http.StatusOK, body: `{"meta":{"complete":true}}`, delay: 100 * time.Millisecond, wantErr: "недоступен"},
	} {
		t.Run(tc.name, func(t *testing.T) {
			s := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
				time.Sleep(tc.delay)
				w.WriteHeader(tc.status)
				_, _ = w.Write([]byte(tc.body))
			}))
			defer s.Close()

			timeout := time.Second
			if tc.delay > 0 {
				timeout = 10 * time.Millisecond
			}
			_, err := New(s.URL, timeout).Analyze(context.Background(), Input{Text: "Текст"})
			if err == nil || !strings.Contains(err.Error(), tc.wantErr) {
				t.Fatalf("err=%v, want substring %q", err, tc.wantErr)
			}
		})
	}
}
