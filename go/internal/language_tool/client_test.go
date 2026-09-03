package languagetool

import (
	"context"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"
)

func TestCheckMapsMatchesToUnifiedFinding(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/v2/check" {
			t.Fatalf("path: %s", r.URL.Path)
		}
		body, _ := io.ReadAll(r.Body)
		if !strings.Contains(string(body), "language=ru-RU") {
			t.Errorf("язык не передан явно: %s", body)
		}
		w.Header().Set("Content-Type", "application/json")
		io.WriteString(w, `{"matches":[{"message":"Исправьте слово","rule":{"id":"MORFOLOGIK_RULE_RU_RU"},"replacements":[{"value":"слово"}],"offset":3,"length":4,"context":{"text":"Пример слово","offset":3,"length":4},"sentence":"Пример слово","type":{"typeName":"misspelling"}}]}`)
	}))
	defer server.Close()
	findings, err := New(server.URL, time.Second).Check(context.Background(), "Пример словоо", "ru-RU", Options{DisabledRules: []string{"X"}})
	if err != nil {
		t.Fatal(err)
	}
	if len(findings) != 1 || findings[0].Analyzer != "languagetool" || findings[0].RuleID != "MORFOLOGIK_RULE_RU_RU" || findings[0].Line != 1 || findings[0].Column != 4 {
		t.Fatalf("неверная находка: %+v", findings)
	}
}

func TestHealthProbesLanguageToolEndpoint(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/v2/check" {
			t.Fatalf("path: %s", r.URL.Path)
		}
		http.Error(w, "unavailable", http.StatusServiceUnavailable)
	}))
	defer server.Close()
	if err := New(server.URL, time.Second).Health(context.Background()); err == nil {
		t.Fatal("health must fail when LanguageTool endpoint is unavailable")
	}
}
