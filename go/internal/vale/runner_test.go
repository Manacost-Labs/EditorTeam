package vale

import (
	"context"
	"os"
	"path/filepath"
	"runtime"
	"testing"
	"time"
)

func TestCheckParsesJSONAndUsesTempFile(t *testing.T) {
	if runtime.GOOS == "windows" {
		t.Skip("shell fixture для Unix")
	}
	dir := t.TempDir()
	script := filepath.Join(dir, "vale-fake")
	if err := os.WriteFile(script, []byte("#!/bin/sh\nprintf '%s' '{\"input.md\":[{\"Check\":\"EditorTeam.Test\",\"Message\":\"проверка\",\"Severity\":\"suggestion\",\"Line\":2,\"Column\":3,\"Match\":\"слово\",\"Suggestions\":[\"вариант\"]}]}'\n"), 0700); err != nil {
		t.Fatal(err)
	}
	findings, err := New(script, "", 3*time.Second).Check(context.Background(), "Текст\nслово")
	if err != nil {
		t.Fatal(err)
	}
	if len(findings) != 1 || findings[0].Analyzer != "vale" || findings[0].RuleID != "EditorTeam.Test" || findings[0].Suggestions[0] != "вариант" {
		t.Fatalf("находки: %+v", findings)
	}
}

func TestMissingBinaryIsSkippable(t *testing.T) {
	_, err := New("/definitely/not/vale", "", time.Second).Check(context.Background(), "текст")
	if err == nil {
		t.Fatal("ожидалась ошибка отсутствующего Vale")
	}
}

func TestCheckAcceptsWrappedValeJSON(t *testing.T) {
	if runtime.GOOS == "windows" {
		t.Skip("shell fixture для Unix")
	}
	dir := t.TempDir()
	script := filepath.Join(dir, "vale-modern")
	content := "#!/bin/sh\nprintf '%s' '{\"results\":[{\"path\":\"input.md\",\"findings\":[{\"ruleId\":\"EditorTeam.Modern\",\"message\":\"проверка\",\"severity\":\"warning\",\"line\":1,\"col\":2,\"match\":\"тест\"}]}]}'\n"
	if err := os.WriteFile(script, []byte(content), 0700); err != nil {
		t.Fatal(err)
	}
	findings, err := New(script, "", 3*time.Second).Check(context.Background(), "тест")
	if err != nil || len(findings) != 1 || findings[0].RuleID != "EditorTeam.Modern" || findings[0].Column != 2 {
		t.Fatalf("modern JSON: %+v %v", findings, err)
	}
}

func TestCheckProfileUsesAllowlistedProfileInFilename(t *testing.T) {
	if runtime.GOOS == "windows" {
		t.Skip("shell fixture for Unix")
	}
	dir := t.TempDir()
	script := filepath.Join(dir, "vale-profile")
	content := "#!/bin/sh\ncase \"$*\" in *input.guide.md*) printf '%s' '{}' ;; *) exit 9 ;; esac\n"
	if err := os.WriteFile(script, []byte(content), 0700); err != nil {
		t.Fatal(err)
	}
	if _, err := New(script, "", 3*time.Second).CheckProfile(context.Background(), "текст", "guide"); err != nil {
		t.Fatalf("profile was not passed through an allowlisted filename: %v", err)
	}
}

func TestCheckProfileRejectsUnknownProfile(t *testing.T) {
	_, err := New("vale", "", time.Second).CheckProfile(context.Background(), "текст", "../../escape")
	if err == nil {
		t.Fatal("expected an unsupported profile error")
	}
}
