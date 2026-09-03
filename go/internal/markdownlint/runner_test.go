package markdownlint

import (
	"context"
	"os"
	"path/filepath"
	"runtime"
	"testing"
	"time"
)

func TestCheckParsesJSON(t *testing.T) {
	if runtime.GOOS == "windows" {
		t.Skip("тест использует POSIX-скрипт")
	}
	dir := t.TempDir()
	bin := filepath.Join(dir, "markdownlint-fake")
	content := "#!/bin/sh\nprintf '%s' '[{\"fileName\":\"input.md\",\"lineNumber\":2,\"ruleNames\":[\"MD022\"],\"errorDetail\":\"нужна пустая строка\",\"errorContext\":\"## Заголовок\",\"errorRange\":[1,3]}]'\n"
	if err := os.WriteFile(bin, []byte(content), 0o700); err != nil {
		t.Fatal(err)
	}
	got, err := New(bin, "", 0).Check(context.Background(), "# Заголовок\n## Заголовок")
	if err != nil {
		t.Fatal(err)
	}
	if len(got) != 1 || got[0].RuleID != "MD022" || got[0].Line != 2 {
		t.Fatalf("unexpected findings: %#v", got)
	}
}

func TestHealthRunsMarkdownlintVersionProbe(t *testing.T) {
	if runtime.GOOS == "windows" {
		t.Skip("shell fixture for Unix")
	}
	dir := t.TempDir()
	bin := filepath.Join(dir, "broken-markdownlint")
	if err := os.WriteFile(bin, []byte("#!/bin/sh\nexit 7\n"), 0o700); err != nil {
		t.Fatal(err)
	}
	if err := New(bin, "", 3*time.Second).Health(context.Background()); err == nil {
		t.Fatal("health must execute markdownlint instead of checking only its path")
	}
}
