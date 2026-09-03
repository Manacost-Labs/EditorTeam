// Package markdownlint запускает markdownlint CLI без shell.
package markdownlint

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

var ErrNotInstalled = errors.New("markdownlint не установлен или не найден в PATH")

type Runner struct {
	Binary   string
	Config   string
	Timeout  time.Duration
	MaxBytes int64
}

func New(binary, config string, timeout time.Duration) *Runner {
	if binary == "" {
		binary = "markdownlint"
	}
	if timeout <= 0 {
		timeout = 8 * time.Second
	}
	return &Runner{Binary: binary, Config: config, Timeout: timeout, MaxBytes: 2 << 20}
}

func (r *Runner) Check(parent context.Context, text string) ([]finding.Finding, error) {
	if r == nil {
		return nil, ErrNotInstalled
	}
	ctx, cancel := context.WithTimeout(parent, r.Timeout)
	defer cancel()
	dir, err := os.MkdirTemp("", "editorteam-markdownlint-")
	if err != nil {
		return nil, err
	}
	defer os.RemoveAll(dir)
	path := filepath.Join(dir, "input.md")
	if err := os.WriteFile(path, []byte(text), 0600); err != nil {
		return nil, err
	}
	args := []string{"--json"}
	if r.Config != "" {
		args = append(args, "--config", r.Config)
	}
	args = append(args, path)
	cmd := exec.CommandContext(ctx, r.Binary, args...)
	var out limitedBuffer
	out.max = r.MaxBytes
	cmd.Stdout, cmd.Stderr = &out, &out
	err = cmd.Run()
	if errors.Is(ctx.Err(), context.DeadlineExceeded) {
		return nil, ctx.Err()
	}
	if errors.Is(err, exec.ErrNotFound) {
		return nil, ErrNotInstalled
	}
	if err != nil && out.Len() == 0 {
		return nil, fmt.Errorf("markdownlint: %w", err)
	}
	findings, parseErr := parse(out.Bytes())
	if parseErr != nil {
		return nil, parseErr
	}
	return findings, nil
}

type violation struct {
	FileName        string   `json:"fileName"`
	LineNumber      int      `json:"lineNumber"`
	RuleNames       []string `json:"ruleNames"`
	RuleDescription string   `json:"ruleDescription"`
	RuleInformation string   `json:"ruleInformation"`
	ErrorDetail     string   `json:"errorDetail"`
	ErrorContext    string   `json:"errorContext"`
	ErrorRange      []int    `json:"errorRange"`
}

func parse(raw []byte) ([]finding.Finding, error) {
	var items []violation
	if err := json.Unmarshal(raw, &items); err != nil {
		var grouped map[string][]violation
		if err2 := json.Unmarshal(raw, &grouped); err2 != nil {
			return nil, fmt.Errorf("ответ markdownlint не JSON: %w", err)
		}
		for _, list := range grouped {
			items = append(items, list...)
		}
	}
	out := make([]finding.Finding, 0, len(items))
	for _, item := range items {
		rule := strings.Join(item.RuleNames, ",")
		if rule == "" {
			rule = "markdownlint"
		}
		message := item.ErrorDetail
		if message == "" {
			message = item.RuleDescription
		}
		column := 0
		length := 0
		if len(item.ErrorRange) > 0 {
			column = item.ErrorRange[0] + 1
		}
		if len(item.ErrorRange) > 1 {
			length = item.ErrorRange[1]
		}
		out = append(out, finding.Finding{Analyzer: "markdownlint", RuleID: rule, Severity: "warning", Message: message, Line: item.LineNumber, Column: column, Length: length, Evidence: item.ErrorContext, Confidence: 0.98, Tags: []string{"markdown"}})
	}
	return out, nil
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
