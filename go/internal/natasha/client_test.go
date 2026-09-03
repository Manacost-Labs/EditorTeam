package natasha

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"
)

func TestClientAnalyzeAndHealth(t *testing.T) {
	s := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path == "/health" {
			w.WriteHeader(http.StatusOK)
			return
		}
		if r.URL.Path != "/analyze" {
			t.Fatalf("path: %s", r.URL.Path)
		}
		_ = json.NewEncoder(w).Encode(Response{Sentences: []Span{{Text: "Текст", Offset: 0, Length: 5, Lemma: "текст", POS: "NOUN"}}})
	}))
	defer s.Close()
	c := New(s.URL, time.Second)
	if err := c.Health(context.Background()); err != nil {
		t.Fatal(err)
	}
	out, err := c.Analyze(context.Background(), Input{Text: "Текст", Language: "ru"})
	if err != nil || len(out.Sentences) != 1 || out.Sentences[0].Lemma != "текст" {
		t.Fatalf("response: %+v, %v", out, err)
	}
}
