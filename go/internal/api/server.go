// Package api — HTTP-слой сервиса.
package api

import (
	"context"
	"crypto/rand"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"log/slog"
	"net/http"
	"time"

	"github.com/Manacost-Labs/EditorTeam/go/internal/analyzer"
	"github.com/Manacost-Labs/EditorTeam/go/internal/config"
	"github.com/Manacost-Labs/EditorTeam/go/internal/editor"
	"github.com/Manacost-Labs/EditorTeam/go/internal/openbot"
	"github.com/Manacost-Labs/EditorTeam/go/internal/pipeline"
)

type Server struct {
	cfg   *config.Config
	ed    *editor.Service
	an    *analyzer.Client
	log   *slog.Logger
	edits openbot.GoogleDocumentEditPreparer
	pipe  *pipeline.Service
}

func New(cfg *config.Config, ed *editor.Service, an *analyzer.Client, log *slog.Logger) *Server {
	server := &Server{cfg: cfg, ed: ed, an: an, log: log}
	if cfg.OpenBotURL != "" && cfg.OpenBotToken != "" {
		server.edits = openbot.New(cfg.OpenBotURL, cfg.OpenBotToken, cfg.RequestTimeout)
	}
	return server
}

// SetPipeline подключает новую staged-оркестрацию, не ломая существующий
// `/edit` и AG-UI контракт старого шлюза.
func (s *Server) SetPipeline(p *pipeline.Service) { s.pipe = p }

func (s *Server) Routes() http.Handler {
	mux := http.NewServeMux()
	mux.HandleFunc("GET /health", s.health)
	mux.HandleFunc("POST /ag-ui", s.agui)
	mux.HandleFunc("POST /edit", s.edit)
	mux.HandleFunc("POST /audit", s.audit)
	mux.HandleFunc("POST /v2/edit", s.editV2)
	mux.HandleFunc("POST /analyze", s.compat("/analyze"))
	mux.HandleFunc("POST /validate", s.compat("/validate"))
	mux.HandleFunc("POST /rules", s.compat("/rules"))
	mux.HandleFunc("POST /outline/validate", s.compat("/outline/validate"))
	return s.withLogging(mux)
}

func (s *Server) withLogging(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		id := r.Header.Get("X-Request-ID")
		if id == "" {
			id = requestID()
			r.Header.Set("X-Request-ID", id)
		}
		w.Header().Set("X-Request-ID", id)
		start := time.Now()
		next.ServeHTTP(w, r)
		// Тело не логируем: сервис принимает чужие тексты
		s.log.Info("запрос", "request_id", id, "метод", r.Method, "путь", r.URL.Path,
			"длительность", time.Since(start).Round(time.Millisecond))
	})
}

func writeJSON(w http.ResponseWriter, code int, v any) {
	w.Header().Set("Content-Type", "application/json; charset=utf-8")
	w.WriteHeader(code)
	_ = json.NewEncoder(w).Encode(v)
}

func writeErr(w http.ResponseWriter, code int, msg string) {
	writeJSON(w, code, map[string]any{"error": msg, "code": http.StatusText(code), "request_id": w.Header().Get("X-Request-ID")})
}

func requestID() string {
	b := make([]byte, 12)
	if _, err := rand.Read(b); err != nil {
		return fmt.Sprintf("%d", time.Now().UnixNano())
	}
	return fmt.Sprintf("%x", b)
}

func (s *Server) compat(path string) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		var payload map[string]any
		if !s.decode(w, r, &payload) {
			return
		}
		ctx, cancel := context.WithTimeout(r.Context(), s.cfg.RequestTimeout)
		defer cancel()
		raw, err := s.an.Forward(ctx, path, payload)
		if err != nil {
			status := http.StatusBadGateway
			var responseErr *analyzer.ResponseError
			if errors.As(err, &responseErr) {
				status = responseErr.Status
			}
			writeErr(w, status, err.Error())
			return
		}
		w.Header().Set("Content-Type", "application/json; charset=utf-8")
		_, _ = w.Write(raw)
	}
}

func (s *Server) health(w http.ResponseWriter, r *http.Request) {
	ctx, cancel := context.WithTimeout(r.Context(), 5*time.Second)
	defer cancel()
	status := map[string]any{"ok": true, "config": s.cfg.Redacted()}
	if s.pipe != nil {
		analyzers := s.pipe.Health(ctx)
		status["analyzers"] = analyzers
		details := s.pipe.HealthDetails(ctx)
		if len(details) > 0 {
			status["analyzer_details"] = details
		}
		if natashaDetail, ok := details["natasha-razdel"]; ok {
			status["natasha"] = natashaDetail
		}
		complete := true
		for _, state := range analyzers {
			if state != "ok" {
				complete = false
				break
			}
		}
		for _, detail := range details {
			if detail.Status != "ok" || !detail.Complete {
				complete = false
				break
			}
		}
		status["checks_complete"] = complete
	}
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
	if req.Mode != "" {
		canon, ok := editor.NormalizeDepth(req.Mode)
		if !ok {
			writeErr(w, http.StatusBadRequest,
				"неизвестный режим правки: "+req.Mode+" (лёгкая, обычная, глубокая, переплавка)")
			return
		}
		req.Mode = canon
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

func (s *Server) editV2(w http.ResponseWriter, r *http.Request) {
	if s.pipe == nil {
		writeErr(w, http.StatusNotImplemented, "новый pipeline не настроен")
		return
	}
	var req pipeline.Request
	if !s.decode(w, r, &req) {
		return
	}
	ctx, cancel := context.WithTimeout(pipeline.WithRequestID(r.Context(), r.Header.Get("X-Request-ID")), s.cfg.RequestTimeout)
	defer cancel()
	result, err := s.pipe.Run(ctx, req)
	if err != nil {
		writeErr(w, http.StatusBadGateway, err.Error())
		return
	}
	writeJSON(w, http.StatusOK, result)
}
