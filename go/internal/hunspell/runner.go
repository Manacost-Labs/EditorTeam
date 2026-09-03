// Package hunspell запускает Hunspell только для выдачи подсказок.
// Автоматическая замена по словарю намеренно запрещена.
package hunspell

import (
	"bytes"
	"context"
	"errors"
	"fmt"
	"io"
	"os"
	"os/exec"
	"path/filepath"
	"regexp"
	"strings"
	"time"
	"unicode/utf8"

	"github.com/Manacost-Labs/EditorTeam/go/internal/finding"
	"github.com/Manacost-Labs/EditorTeam/go/internal/guards"
)

var ErrNotInstalled = errors.New("Hunspell не установлен или не найден в PATH")
var ErrDictionaryUnavailable = errors.New("русский словарь Hunspell не задан")

type Runner struct {
	Binary     string
	Dictionary string
	Allowlist  []string
	Timeout    time.Duration
	MaxBytes   int64
}

func New(binary, dictionary string, allowlist []string, timeout time.Duration) *Runner {
	if binary == "" {
		binary = "hunspell"
	}
	if timeout <= 0 {
		timeout = 8 * time.Second
	}
	return &Runner{Binary: binary, Dictionary: dictionary, Allowlist: allowlist, Timeout: timeout, MaxBytes: 2 << 20}
}

// Health checks the same executable and dictionary that will be used by
// Check. This prevents a present binary with a missing ru_RU dictionary from
// being reported as healthy.
func (r *Runner) Health(parent context.Context) error {
	if r == nil {
		return ErrNotInstalled
	}
	if strings.TrimSpace(r.Dictionary) == "" {
		return ErrDictionaryUnavailable
	}
	if _, err := os.Stat(r.Dictionary); err != nil {
		return ErrDictionaryUnavailable
	}
	if _, err := exec.LookPath(r.Binary); err != nil {
		return ErrNotInstalled
	}
	_, err := r.Check(parent, "")
	return err
}

func (r *Runner) Check(parent context.Context, text string) ([]finding.Finding, error) {
	if r == nil {
		return nil, ErrNotInstalled
	}
	if strings.TrimSpace(r.Dictionary) == "" {
		return nil, ErrDictionaryUnavailable
	}
	if _, err := os.Stat(r.Dictionary); err != nil {
		return nil, ErrDictionaryUnavailable
	}
	ctx, cancel := context.WithTimeout(parent, r.Timeout)
	defer cancel()
	masked := maskProtected(text)
	args := []string{"-a"}
	if r.Dictionary != "" {
		args = append(args, "-d", dictionaryName(r.Dictionary))
	}
	cmd := exec.CommandContext(ctx, r.Binary, args...)
	cmd.Stdin = strings.NewReader(masked)
	var output limitedBuffer
	output.max = r.MaxBytes
	cmd.Stdout, cmd.Stderr = &output, &output
	err := cmd.Run()
	if errors.Is(ctx.Err(), context.DeadlineExceeded) {
		return nil, ctx.Err()
	}
	if errors.Is(err, exec.ErrNotFound) {
		return nil, ErrNotInstalled
	}
	if err != nil {
		return nil, fmt.Errorf("Hunspell: %w: %s", err, strings.TrimSpace(output.String()))
	}
	return parse(text, output.String(), r.Allowlist), nil
}

func dictionaryName(path string) string {
	if strings.HasSuffix(path, ".dic") {
		return strings.TrimSuffix(path, ".dic")
	}
	return filepath.Join(path, "ru_RU")
}

var tokenRE = regexp.MustCompile(`[\p{L}\p{N}][\p{L}\p{N}'’-]*`)

func parse(text, raw string, allowlist []string) []finding.Finding {
	allowed := make(map[string]struct{}, len(allowlist))
	for _, item := range allowlist {
		allowed[strings.ToLower(strings.TrimSpace(item))] = struct{}{}
	}
	findings := []finding.Finding{}
	lines := strings.Split(text, "\n")
	outLines := strings.Split(raw, "\n")
	lineNo := 1
	wordIndex := 0
	for _, line := range outLines {
		if line == "" {
			lineNo++
			wordIndex = 0
			continue
		}
		if strings.HasPrefix(line, "@") {
			continue
		}
		if line == "*" || line == "+" {
			wordIndex++
			continue
		}
		if strings.HasPrefix(line, "#") || strings.HasPrefix(line, "&") {
			parts := strings.Fields(line)
			if len(parts) < 2 {
				continue
			}
			word := parts[1]
			if _, ok := allowed[strings.ToLower(word)]; ok {
				wordIndex++
				continue
			}
			if lineNo > len(lines) {
				lineNo = len(lines)
			}
			lineText := lines[lineNo-1]
			loc := tokenAt(lineText, wordIndex)
			severity := "warning"
			rule := "hunspell.unknown"
			message := "неизвестное слово: " + word
			if strings.HasPrefix(line, "&") {
				rule = "hunspell.spelling"
				message = "возможная опечатка: " + word
			}
			var suggestions []string
			if colon := strings.Index(line, ":"); colon >= 0 && colon+1 < len(line) {
				for _, suggestion := range strings.Split(strings.TrimSpace(line[colon+1:]), ",") {
					if suggestion = strings.TrimSpace(suggestion); suggestion != "" {
						suggestions = append(suggestions, suggestion)
					}
				}
			}
			findings = append(findings, finding.Finding{Analyzer: "hunspell", RuleID: rule, Severity: severity, Message: message, Suggestions: suggestions, Line: lineNo, Column: loc.column, Offset: loc.offset, Length: utf8.RuneCountInString(word), Evidence: word, Confidence: 0.7, Tags: []string{"spelling"}})
			wordIndex++
		}
	}
	return findings
}

type tokenLocation struct{ offset, column int }

func tokenAt(line string, index int) tokenLocation {
	matches := tokenRE.FindAllStringIndex(line, -1)
	if index < 0 || index >= len(matches) {
		return tokenLocation{offset: 0, column: 1}
	}
	start := matches[index][0]
	return tokenLocation{offset: start, column: start + 1}
}

func maskProtected(text string) string {
	masked := []byte(text)
	for _, entity := range guards.Extract(text) {
		start, end := entity.Start, entity.End
		if start < 0 || end > len(masked) || start >= end {
			continue
		}
		for i := start; i < end; {
			_, size := utf8.DecodeRune(masked[i:end])
			if size <= 0 {
				size = 1
			}
			masked[i] = ' '
			for j := i + 1; j < i+size && j < end; j++ {
				masked[j] = ' '
			}
			i += size
		}
	}
	return string(masked)
}

type limitedBuffer struct {
	bytes.Buffer
	max int64
}

func (b *limitedBuffer) Write(p []byte) (int, error) {
	if b.max > 0 && int64(b.Len()+len(p)) > b.max {
		return 0, io.ErrShortBuffer
	}
	return b.Buffer.Write(p)
}
