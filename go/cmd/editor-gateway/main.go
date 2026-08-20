// Команда editor-gateway — сервис редактуры.
//
// Принимает текст, правит его моделью и проверяет результат анализаторами.
// Правка возвращается, только если прошла затвор; иначе клиент получает
// исходный текст и перечень причин отказа.
package main

import (
	"context"
	"errors"
	"log/slog"
	"net/http"
	"os"
	"os/signal"
	"syscall"
	"time"

	"github.com/Manacost-Labs/EditorTeam/go/internal/analyzer"
	"github.com/Manacost-Labs/EditorTeam/go/internal/api"
	"github.com/Manacost-Labs/EditorTeam/go/internal/config"
	"github.com/Manacost-Labs/EditorTeam/go/internal/editor"
	"github.com/Manacost-Labs/EditorTeam/go/internal/llm"
)

func main() {
	log := slog.New(slog.NewTextHandler(os.Stderr, &slog.HandlerOptions{Level: slog.LevelInfo}))

	cfg, err := config.Load()
	if err != nil {
		log.Error("настройки", "ошибка", err)
		os.Exit(1)
	}

	an := analyzer.New(cfg.AnalyzerURL, cfg.RequestTimeout)
	lm := llm.New(cfg.Provider, cfg.Model, cfg.APIKey, cfg.AccountID, cfg.BaseURL, cfg.RequestTimeout)
	srv := &http.Server{
		Addr:              cfg.Addr,
		Handler:           api.New(cfg, editor.New(lm, an, cfg.MaxAttempts), an, log).Routes(),
		ReadHeaderTimeout: 10 * time.Second,
	}

	go func() {
		log.Info("сервис запущен", "адрес", cfg.Addr, "провайдер", cfg.Provider,
			"модель", cfg.Model, "анализатор", cfg.AnalyzerURL)
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
