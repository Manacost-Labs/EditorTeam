// Package api — HTTP-слой сервиса.
package api

import (
	"context"
	"encoding/json"
	"errors"
	"io"
	"log/slog"
	"net/http"
	"time"

	"github.com/Manacost-Labs/EditorTeam/go/internal/analyzer"
	"github.com/Manacost-Labs/EditorTeam/go/internal/config"
	"github.com/Manacost-Labs/EditorTeam/go/internal/editor"
)

type Server struct {
	cfg *config.Config
	ed  *editor.Service
	an  *analyzer.Client
	log *slog.Logger
}

func New(cfg *config.Config, ed *editor.Service, an *analyzer.Client, log *slog.Logger) *Server {
	return &Server{cfg: cfg, ed: ed, an: an, log: log}
}

func (s *Server) Routes() http.Handler {
	mux := http.NewServeMux()
	mux.HandleFunc("GET /health", s.health)
	mux.HandleFunc("POST /ag-ui", s.agui)
	mux.HandleFunc("POST /edit", s.edit)
	mux.HandleFunc("POST /audit", s.audit)
	return s.withLogging(mux)
}

func (s *Server) withLogging(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		start := time.Now()
		next.ServeHTTP(w, r)
		// Тело не логируем: сервис принимает чужие тексты
		s.log.Info("запрос", "метод", r.Method, "путь", r.URL.Path,
			"длительность", time.Since(start).Round(time.Millisecond))
	})
}

func writeJSON(w http.ResponseWriter, code int, v any) {
	w.Header().Set("Content-Type", "application/json; charset=utf-8")
	w.WriteHeader(code)
	_ = json.NewEncoder(w).Encode(v)
}

func writeErr(w http.ResponseWriter, code int, msg string) {
	writeJSON(w, code, map[string]string{"error": msg})
}

func (s *Server) health(w http.ResponseWriter, r *http.Request) {
	ctx, cancel := context.WithTimeout(r.Context(), 5*time.Second)
	defer cancel()
	status := map[string]any{"ok": true, "config": s.cfg.Redacted()}
	if err := s.an.Health(ctx); err != nil {
		status["ok"] = false
		status["analyzer"] = err.Error()
		writeJSON(w, http.StatusServiceUnavailable, status)
		return
	}
	status["analyzer"] = "ok"
	writeJSON(w, http.StatusOK, status)
}

func (s *Server) decode(w http.ResponseWriter, r *http.Request, v any) bool {
	body, err := io.ReadAll(io.LimitReader(r.Body, int64(s.cfg.MaxTextBytes)+4096))
	if err != nil {
		writeErr(w, http.StatusBadRequest, "не удалось прочитать тело запроса")
		return false
	}
	if len(body) > s.cfg.MaxTextBytes {
		writeErr(w, http.StatusRequestEntityTooLarge, "текст слишком большой")
		return false
	}
	if err := json.Unmarshal(body, v); err != nil {
		writeErr(w, http.StatusBadRequest, "нужен корректный JSON")
		return false
	}
	return true
}

func (s *Server) edit(w http.ResponseWriter, r *http.Request) {
	var req editor.Request
	if !s.decode(w, r, &req) {
		return
	}
	if req.Text == "" {
		writeErr(w, http.StatusBadRequest, "поле text пустое")
		return
	}
	if req.Game == "" {
		req.Game = "hearthstone"
	}

	ctx, cancel := context.WithTimeout(r.Context(), s.cfg.RequestTimeout)
	defer cancel()

	res, err := s.ed.Edit(ctx, req)
	if err != nil {
		if errors.Is(err, context.DeadlineExceeded) {
			writeErr(w, http.StatusGatewayTimeout, "правка не уложилась в таймаут")
			return
		}
		s.log.Error("правка", "ошибка", err)
		writeErr(w, http.StatusBadGateway, err.Error())
		return
	}
	// 200 и когда правка не принята: это осмысленный результат,
	// а не сбой — клиент получает исходник и причины отказа
	writeJSON(w, http.StatusOK, res)
}

func (s *Server) audit(w http.ResponseWriter, r *http.Request) {
	var req editor.Request
	if !s.decode(w, r, &req) {
		return
	}
	if req.Game == "" {
		req.Game = "hearthstone"
	}
	ctx, cancel := context.WithTimeout(r.Context(), s.cfg.RequestTimeout)
	defer cancel()

	rep, err := s.an.Analyze(ctx, req.Text, req.Game, req.Profile)
	if err != nil {
		writeErr(w, http.StatusBadGateway, err.Error())
		return
	}
	writeJSON(w, http.StatusOK, rep)
}
