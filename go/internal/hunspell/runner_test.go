package hunspell

import (
	"context"
	"os"
	"path/filepath"
	"runtime"
	"testing"
)

func TestCheckParsesSuggestionsAndAllowlist(t *testing.T) {
	if runtime.GOOS == "windows" {
		t.Skip("тест использует POSIX-скрипт")
	}
	dir := t.TempDir()
	bin := filepath.Join(dir, "hunspell-fake")
	if err := os.WriteFile(bin, []byte("#!/bin/sh\nprintf '%s\\n' '@(#) fake' '& Ошибко 1 0: Ошибка,Ошибку' '*'\n"), 0o700); err != nil {
		t.Fatal(err)
	}
	dict := filepath.Join(dir, "ru_RU.dic")
	if err := os.WriteFile(dict, []byte("fake"), 0o600); err != nil {
		t.Fatal(err)
	}
	r := New(bin, dict, []string{"карта"}, 0)
	got, err := r.Check(context.Background(), "Ошибко карта")
	if err != nil {
		t.Fatal(err)
	}
	if len(got) != 1 || got[0].RuleID != "hunspell.spelling" || len(got[0].Suggestions) != 2 {
		t.Fatalf("unexpected findings: %#v", got)
	}
}

func TestMaskProtectedKeepsLength(t *testing.T) {
	in := "URL https://example.com и 42%"
	out := maskProtected(in)
	if len(in) != len(out) {
		t.Fatalf("mask changed byte length")
	}
	if out == in {
		t.Fatalf("expected protected fragment to be masked")
	}
}
