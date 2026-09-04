// Команда editorteam — основной Go-оркестратор EditorTeam.
package main

import (
	"context"
	"errors"
	"log/slog"
	"net/http"
	"os"
	"os/signal"
	"path/filepath"
	"strings"
	"syscall"
	"time"

	"github.com/Manacost-Labs/EditorTeam/go/internal/analyzer"
	"github.com/Manacost-Labs/EditorTeam/go/internal/analyzers"
	"github.com/Manacost-Labs/EditorTeam/go/internal/api"
	"github.com/Manacost-Labs/EditorTeam/go/internal/config"
	"github.com/Manacost-Labs/EditorTeam/go/internal/editor"
	"github.com/Manacost-Labs/EditorTeam/go/internal/hunspell"
	languageTool "github.com/Manacost-Labs/EditorTeam/go/internal/language_tool"
	"github.com/Manacost-Labs/EditorTeam/go/internal/llm"
	"github.com/Manacost-Labs/EditorTeam/go/internal/markdownlint"
	"github.com/Manacost-Labs/EditorTeam/go/internal/natasha"
	"github.com/Manacost-Labs/EditorTeam/go/internal/pipeline"
	"github.com/Manacost-Labs/EditorTeam/go/internal/retrieval"
	"github.com/Manacost-Labs/EditorTeam/go/internal/vale"
)

type noopCompleter struct{ model string }

func (n noopCompleter) Model() string { return n.model }
func (noopCompleter) Complete(_ context.Context, msgs []llm.Message, _ int) (string, error) {
	for i := len(msgs) - 1; i >= 0; i-- {
		if msgs[i].Role == "user" {
			return msgs[i].Content, nil
		}
	}
	return "", nil
}

func main() {
	log := slog.New(slog.NewJSONHandler(os.Stderr, &slog.HandlerOptions{Level: slog.LevelInfo}))
	cfg, err := config.Load()
	if err != nil {
		log.Error("настройки", "ошибка", err)
		os.Exit(1)
	}
	an := analyzer.New(cfg.AnalyzerURL, cfg.RequestTimeout)
	var completer llm.Completer
	switch cfg.Provider {
	case "none":
		completer = noopCompleter{model: cfg.Model}
	case "agui":
		completer = llm.NewAGUI(cfg.AGUIURL, cfg.AGUIToken, cfg.Model, cfg.ReasoningEffort, cfg.RequestTimeout)
	default:
		completer = llm.New(cfg.Provider, cfg.Model, cfg.APIKey, cfg.AccountID, cfg.BaseURL, cfg.RequestTimeout)
	}
	ed := editor.New(completer, an, cfg.MaxAttempts)
	checks := []analyzers.Analyzer{
		analyzers.NativeGoAnalyzer{},
		&analyzers.PythonAnalyzerAdapter{Client: an, LegacyScripts: analyzers.LegacyScripts, Python: cfg.PythonBinary, Timeout: cfg.PythonTimeout, MaxBytes: cfg.MaxProcessOutput},
		&analyzers.NatashaAnalyzer{Client: natasha.New(cfg.NatashaURL, cfg.NatashaTimeout)},
		&analyzers.HunspellAnalyzer{Runner: hunspell.New(cfg.HunspellBinary, cfg.RussianDictionary, loadAllowlists(), cfg.HunspellTimeout)},
		&analyzers.LanguageToolAnalyzer{Client: languageTool.New(cfg.LanguageToolURL, cfg.LanguageToolTimeout)},
		&analyzers.ValeAnalyzer{Runner: vale.New(cfg.ValeBinary, cfg.ValeConfig, cfg.ValeTimeout)},
		&analyzers.MarkdownlintAnalyzer{Runner: markdownlint.New(cfg.MarkdownlintBinary, cfg.MarkdownlintConfig, cfg.MarkdownlintTimeout)},
	}
	// Provider "none" is a dry run: the pipeline gets no model at all, so it
	// never fabricates a draft, a critic verdict or scores and simply reports
	// the analyzers on the source text.
	var pipelineLLM llm.Completer = completer
	if cfg.Provider == "none" {
		pipelineLLM = nil
	}
	pipe := pipeline.New(pipelineLLM, an, cfg.Provider, checks...)
	pipe.Log = log
	if cfg.RetrievalEnabled {
		// Примеры стиля идут из существующего Python-корпуса; их отсутствие
		// не мешает правке и не входит в checks_complete.
		pipe.Retriever = retrieval.NewHTTP(an, cfg.RetrievalTimeout)
		pipe.RetrievalTimeout = cfg.RetrievalTimeout
	}
	pipe.SetAllowUnavailable(cfg.AllowUnavailable)
	pipe.SetPromptVariant(cfg.PromptVariant)
	server := api.New(cfg, ed, an, log)
	server.SetPipeline(pipe)
	srv := &http.Server{Addr: cfg.Addr, Handler: server.Routes(), ReadHeaderTimeout: 10 * time.Second, ReadTimeout: cfg.RequestTimeout, WriteTimeout: cfg.RequestTimeout}
	go func() {
		log.Info("сервис запущен", "адрес", cfg.Addr, "провайдер", cfg.Provider, "модель", cfg.Model)
		if err := srv.ListenAndServe(); err != nil && !errors.Is(err, http.ErrServerClosed) {
			log.Error("сервер", "ошибка", err)
			os.Exit(1)
		}
	}()
	stop := make(chan os.Signal, 1)
	signal.Notify(stop, syscall.SIGINT, syscall.SIGTERM)
	<-stop
	ctx, cancel := context.WithTimeout(context.Background(), 15*time.Second)
	defer cancel()
	if err := srv.Shutdown(ctx); err != nil {
		log.Error("остановка", "ошибка", err)
	}
	log.Info("остановлен")
}

func loadAllowlists() []string {
	paths := []string{
		"config/dictionaries/common-gaming.txt",
		"config/dictionaries/hearthstone.txt",
		"config/dictionaries/world-of-warcraft.txt",
		"config/dictionaries/league-of-legends.txt",
	}
	var out []string
	for _, path := range paths {
		data, err := os.ReadFile(filepath.Clean(path))
		if err != nil {
			continue
		}
		for _, line := range strings.Split(string(data), "\n") {
			line = strings.TrimSpace(line)
			if line != "" && !strings.HasPrefix(line, "#") {
				out = append(out, line)
			}
		}
	}
	return out
}
