package vale

import (
	"context"
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"testing"
	"time"
)

func fakeVale(t *testing.T, body string) string {
	t.Helper()
	if runtime.GOOS == "windows" {
		t.Skip("shell fixture для Unix")
	}
	script := filepath.Join(t.TempDir(), "vale-fake")
	if err := os.WriteFile(script, []byte("#!/bin/sh\n"+body+"\n"), 0700); err != nil {
		t.Fatal(err)
	}
	return script
}

func TestCheckParsesJSONAndUsesTempFile(t *testing.T) {
	script := fakeVale(t, `printf '%s' '{"input.md":[{"Check":"EditorTeam.Test","Message":"проверка","Severity":"suggestion","Line":2,"Column":3,"Match":"слово","Suggestions":["вариант"]}]}'`)
	findings, err := New(script, "", 3*time.Second).Check(context.Background(), "Текст\nслово", "")
	if err != nil {
		t.Fatal(err)
	}
	if len(findings) != 1 || findings[0].Analyzer != "vale" || findings[0].RuleID != "EditorTeam.Test" || findings[0].Suggestions[0] != "вариант" {
		t.Fatalf("находки: %+v", findings)
	}
}

func TestMissingBinaryIsSkippable(t *testing.T) {
	_, err := New("/definitely/not/vale", "", time.Second).Check(context.Background(), "текст", "guide")
	if err == nil {
		t.Fatal("ожидалась ошибка отсутствующего Vale")
	}
}

func TestCheckAcceptsWrappedValeJSON(t *testing.T) {
	script := fakeVale(t, `printf '%s' '{"results":[{"path":"input.md","findings":[{"ruleId":"EditorTeam.Modern","message":"проверка","severity":"warning","line":1,"col":2,"match":"тест"}]}]}'`)
	findings, err := New(script, "", 3*time.Second).Check(context.Background(), "тест", "")
	if err != nil || len(findings) != 1 || findings[0].RuleID != "EditorTeam.Modern" || findings[0].Column != 2 {
		t.Fatalf("modern JSON: %+v %v", findings, err)
	}
}

func TestFileNameUsesOnlyTheAllowlist(t *testing.T) {
	for profile, want := range map[string]string{
		"guide":               "input.guide.md",
		"constructed-guide":   "input.guide.md",
		"battlegrounds-guide": "input.guide.md",
		"wow-guide":           "input.guide.md",
		"news":                "input.news.md",
		"analysis":            "input.analysis.md",
		"analytics-article":   "input.analysis.md",
		"meta-report":         "input.meta-report.md",
		" News ":              "input.news.md",
		"":                    "input.md",
		"unknown":             "input.md",
		"../../etc/passwd":    "input.md",
		"guide/../../x":       "input.md",
		"news.md":             "input.md",
	} {
		if got := FileName(profile); got != want {
			t.Fatalf("FileName(%q)=%q, want %q", profile, got, want)
		}
	}
}

func TestCheckWritesAllowlistedFileNameIntoTempDir(t *testing.T) {
	// The fake prints the path it received so the test can inspect the
	// real file name that reached the Vale process.
	script := fakeVale(t, `for arg in "$@"; do case "$arg" in /*) printf '{"%s":[]}' "$arg" ;; esac; done`)
	runner := New(script, "", 3*time.Second)
	for _, profile := range []string{"constructed-guide", "news", "unknown-profile", "../../escape"} {
		findings, err := runner.Check(context.Background(), "текст", profile)
		if err != nil || len(findings) != 0 {
			t.Fatalf("%s: %+v %v", profile, findings, err)
		}
	}
	// Same idea, but assert on the exact path shape via a recording script.
	record := filepath.Join(t.TempDir(), "paths.txt")
	recorder := fakeVale(t, `for arg in "$@"; do case "$arg" in /*) printf '%s\n' "$arg" >> `+record+` ;; esac; done; printf '{}'`)
	runner = New(recorder, "", 3*time.Second)
	for _, profile := range []string{"constructed-guide", "../../escape", "unknown"} {
		if _, err := runner.Check(context.Background(), "текст", profile); err != nil {
			t.Fatal(err)
		}
	}
	data, err := os.ReadFile(record)
	if err != nil {
		t.Fatal(err)
	}
	paths := strings.Split(strings.TrimSpace(string(data)), "\n")
	if len(paths) != 3 {
		t.Fatalf("paths: %q", paths)
	}
	for index, want := range []string{"input.guide.md", "input.md", "input.md"} {
		if filepath.Base(paths[index]) != want {
			t.Fatalf("call %d wrote %q, want %q", index, paths[index], want)
		}
		if !strings.Contains(paths[index], "editorteam-vale-") || strings.Contains(paths[index], "..") {
			t.Fatalf("path escaped the temp dir: %q", paths[index])
		}
	}
}
