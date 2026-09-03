package markdownlint

import (
	"context"
	"os"
	"path/filepath"
	"runtime"
	"testing"
)

func TestCheckParsesJSON(t *testing.T) {
	if runtime.GOOS == "windows" { t.Skip("тест использует POSIX-скрипт") }
	dir := t.TempDir()
	bin := filepath.Join(dir, "markdownlint-fake")
	content := "#!/bin/sh\nprintf '%s' '[{\"fileName\":\"input.md\",\"lineNumber\":2,\"ruleNames\":[\"MD022\"],\"errorDetail\":\"нужна пустая строка\",\"errorContext\":\"## Заголовок\",\"errorRange\":[1,3]}]'\n"
	if err := os.WriteFile(bin, []byte(content), 0o700); err != nil { t.Fatal(err) }
	got, err := New(bin, "", 0).Check(context.Background(), "# Заголовок\n## Заголовок")
	if err != nil { t.Fatal(err) }
	if len(got) != 1 || got[0].RuleID != "MD022" || got[0].Line != 2 { t.Fatalf("unexpected findings: %#v", got) }
}
