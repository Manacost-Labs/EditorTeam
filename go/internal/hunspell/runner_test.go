package hunspell

import (
	"context"
	"errors"
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

func TestHealthRequiresLoadableDictionary(t *testing.T) {
	runner := New("/definitely/not/hunspell", "/definitely/not/ru_RU.dic", nil, 0)
	if err := runner.Health(context.Background()); !errors.Is(err, ErrDictionaryUnavailable) {
		t.Fatalf("expected dictionary health failure, got %v", err)
	}
}

func TestRealRussianDictionaryDetectsTypoAndKeepsGameAllowlist(t *testing.T) {
	if os.Getenv("HUNSPELL_INTEGRATION") != "1" {
		t.Skip("set HUNSPELL_INTEGRATION=1 to run with the real dictionary")
	}
	dictionary := os.Getenv("RU_DICT_PATH")
	if dictionary == "" {
		t.Fatal("RU_DICT_PATH is required for the integration test")
	}
	runner := New(os.Getenv("HUNSPELL_BIN"), dictionary, []string{"Хартстоун"}, 0)
	findings, err := runner.Check(context.Background(), "сабака Хартстоун")
	if err != nil {
		t.Fatal(err)
	}
	if len(findings) != 1 {
		t.Fatalf("expected one real typo and no game-term finding, got: %+v", findings)
	}
	if findings[0].Evidence != "сабака" || findings[0].RuleID != "hunspell.spelling" {
		t.Fatalf("unexpected real-dictionary finding: %+v", findings[0])
	}
}
