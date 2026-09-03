// Package vale запускает Vale как ограниченный внешний процесс.
package vale

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"time"

	"github.com/Manacost-Labs/EditorTeam/go/internal/finding"
)

var ErrNotInstalled = errors.New("Vale не установлен или не найден в PATH")

type Runner struct {
	Binary   string
	Config   string
	Timeout  time.Duration
	MaxBytes int64
}

func New(binary, config string, timeout time.Duration) *Runner {
	if binary == "" {
		binary = "vale"
	}
	if timeout <= 0 {
		timeout = 8 * time.Second
	}
	return &Runner{Binary: binary, Config: config, Timeout: timeout, MaxBytes: 4 << 20}
}

// Check создаёт временный Markdown-файл с правами 0600 и удаляет его после
// запуска. Путь к файлу не строится через shell.
func (r *Runner) Check(parent context.Context, text string) ([]finding.Finding, error) {
	if r == nil {
		return nil, ErrNotInstalled
	}
	ctx, cancel := context.WithTimeout(parent, r.Timeout)
	defer cancel()
	dir, err := os.MkdirTemp("", "editorteam-vale-")
	if err != nil {
		return nil, err
	}
	defer os.RemoveAll(dir)
	path := filepath.Join(dir, "input.md")
	if err := os.WriteFile(path, []byte(text), 0600); err != nil {
		return nil, err
	}
	args := []string{"--output=JSON"}
	if r.Config != "" {
		args = append(args, "--config", r.Config)
	}
	args = append(args, path)
	cmd := exec.CommandContext(ctx, r.Binary, args...)
	var output limitedBuffer
	output.max = r.MaxBytes
	cmd.Stdout, cmd.Stderr = &output, &output
	err = cmd.Run()
	if errors.Is(ctx.Err(), context.DeadlineExceeded) {
		return nil, ctx.Err()
	}
	if errors.Is(err, exec.ErrNotFound) {
		return nil, ErrNotInstalled
	}
	// Vale exits 1 when it found style issues; JSON is still useful.
	if err != nil && output.Len() == 0 {
		return nil, fmt.Errorf("Vale: %w", err)
	}
	var raw map[string][]valeFinding
	mapErr := json.Unmarshal(output.Bytes(), &raw)
	findings := []finding.Finding{}
	if mapErr == nil {
		for _, items := range raw {
			for _, item := range items {
				if item.Check == "" && item.RuleID == "" && item.Message == "" {
					continue
				}
				if item.Check == "" {
					item.Check = item.RuleID
				}
				line := item.Line
				if line == 0 {
					line = item.LineLower
				}
				column := item.Column
				if column == 0 {
					column = item.Col
				}
				if column == 0 && len(item.Span) > 0 {
					column = item.Span[0] + 1
				}
				sev := strings.ToLower(item.Severity)
				if sev == "" {
					sev = "suggestion"
				}
				findings = append(findings, finding.Finding{Analyzer: "vale", RuleID: item.Check, Severity: sev,
					Message: item.Message, Line: line, Column: column, Context: item.Match, Suggestions: item.Replacements})
			}
		}
	}
	// Новые версии Vale могут оборачивать данные в {results:[{findings:[]}]}.
	if len(findings) == 0 {
		var wrapped struct {
			Results []struct {
				Findings []valeFinding `json:"findings"`
			} `json:"results"`
		}
		if err := json.Unmarshal(output.Bytes(), &wrapped); err == nil {
			for _, group := range wrapped.Results {
				for _, item := range group.Findings {
					if item.Check == "" {
						item.Check = item.RuleID
					}
					line := item.Line
					if line == 0 {
						line = item.LineLower
					}
					column := item.Column
					if column == 0 {
						column = item.Col
					}
					if column == 0 && len(item.Span) > 0 {
						column = item.Span[0] + 1
					}
					sev := strings.ToLower(item.Severity)
					if sev == "" {
						sev = "suggestion"
					}
					findings = append(findings, finding.Finding{Analyzer: "vale", RuleID: item.Check, Severity: sev, Message: item.Message, Line: line, Column: column, Context: item.Match, Suggestions: item.Replacements})
				}
			}
		}
	}
	if mapErr != nil && len(findings) == 0 {
		var wrapped struct {
			Results []struct {
				Findings []valeFinding `json:"findings"`
			} `json:"results"`
		}
		if err := json.Unmarshal(output.Bytes(), &wrapped); err != nil || len(wrapped.Results) == 0 {
			return nil, fmt.Errorf("ответ Vale не JSON: %w", mapErr)
		}
	}
	return findings, nil
}

type valeFinding struct {
	Check        string   `json:"Check"`
	RuleID       string   `json:"ruleId"`
	Message      string   `json:"message"`
	Severity     string   `json:"severity"`
	Line         int      `json:"line"`
	Column       int      `json:"column"`
	Col          int      `json:"col"`
	Span         []int    `json:"Span"`
	LineLower    int      `json:"Line"`
	Match        string   `json:"match"`
	Replacements []string `json:"Suggestions"`
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
