package openbot

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"
)

func TestPrepareGoogleDocumentEditUsesOnlyInternalAuthenticatedEndpoint(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/internal/editor/google-doc-edits" || r.Method != http.MethodPost {
			t.Fatalf("неверный endpoint: %s %s", r.Method, r.URL.Path)
		}
		if got := r.Header.Get("X-OpenBot-Agent-Token"); got != "service-token" {
			t.Fatalf("неверный сервисный токен: %q", got)
		}
		var body map[string]string
		if err := json.NewDecoder(r.Body).Decode(&body); err != nil {
			t.Fatal(err)
		}
		if body["run"] != "signed-run" || body["documentId"] != "doc_123" || body["sourceText"] != "До" || body["candidateText"] != "После" {
			t.Fatalf("неверный контракт: %#v", body)
		}
		_ = json.NewEncoder(w).Encode(PreparedGoogleDocumentEdit{
			ID: "00000000-0000-4000-8000-000000000000", State: "pending",
			ReviewPath: "/editor/google-doc-edits/00000000-0000-4000-8000-000000000000", EditCount: 1,
		})
	}))
	defer server.Close()

	prepared, err := New(server.URL, "service-token", time.Second).PrepareGoogleDocumentEdit(
		context.Background(), "signed-run", "doc_123", "До", "После",
	)
	if err != nil {
		t.Fatal(err)
	}
	if prepared.EditCount != 1 || prepared.State != "pending" {
		t.Fatalf("неверное предложение: %#v", prepared)
	}
}

func TestPrepareGoogleDocumentEditRejectsAnUntrustedReviewPath(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		_ = json.NewEncoder(w).Encode(PreparedGoogleDocumentEdit{
			ID: "00000000-0000-4000-8000-000000000000", State: "pending",
			ReviewPath: "https://attacker.example/confirm", EditCount: 1,
		})
	}))
	defer server.Close()

	_, err := New(server.URL, "service-token", time.Second).PrepareGoogleDocumentEdit(
		context.Background(), "signed-run", "doc_123", "До", "После",
	)
	if err == nil {
		t.Fatal("внешний review URL не должен приниматься")
	}
}
