// Package config собирает настройки сервиса из окружения.
//
// Ключи провайдеров берутся только из окружения и никогда не пишутся в логи
// и ответы: сервис принимает чужие тексты и не должен становиться местом
// утечки чужих ключей.
package config

import (
	"fmt"
	"os"
	"strconv"
	"time"
)

type Config struct {
	Addr            string
	AnalyzerURL     string
	AgentToken      string // необязательный Bearer-токен для AG-UI endpoint
	Provider        string // agui | openai | openrouter | cloudflare
	AGUIURL         string // внутренний AG-UI endpoint, если Provider=agui
	AGUIToken       string // Bearer-токен внутреннего AG-UI endpoint
	ReasoningEffort string // передаётся внутреннему Codex; например xhigh
	Model           string
	APIKey          string
	AccountID       string // нужен только Cloudflare
	BaseURL         string // свой адрес провайдера: прокси или self-hosted
	MaxAttempts     int
	RequestTimeout  time.Duration
	MaxTextBytes    int
	OpenBotURL      string // внутренний TeamBot endpoint для подготовки подтверждаемой записи
	OpenBotToken    string // сервисный токен; никогда не передаётся модели
}

func env(key, def string) string {
	if v := os.Getenv(key); v != "" {
		return v
	}
	return def
}

func envInt(key string, def int) int {
	if v, err := strconv.Atoi(os.Getenv(key)); err == nil && v > 0 {
		return v
	}
	return def
}

func Load() (*Config, error) {
	c := &Config{
		Addr:            env("EDITOR_ADDR", ":8080"),
		AnalyzerURL:     env("EDITOR_ANALYZER_URL", "http://127.0.0.1:8731"),
		AgentToken:      os.Getenv("EDITOR_AGENT_TOKEN"),
		Provider:        env("EDITOR_PROVIDER", "openrouter"),
		AGUIURL:         os.Getenv("EDITOR_AGUI_URL"),
		AGUIToken:       os.Getenv("EDITOR_AGUI_TOKEN"),
		ReasoningEffort: env("EDITOR_REASONING_EFFORT", "xhigh"),
		Model:           env("EDITOR_MODEL", ""),
		APIKey:          os.Getenv("EDITOR_API_KEY"),
		AccountID:       os.Getenv("EDITOR_CF_ACCOUNT_ID"),
		BaseURL:         os.Getenv("EDITOR_BASE_URL"),
		// Больше трёх попыток редко помогают: если модель дважды вычистила
		// голос, она это делает системно, и повторы только жгут токены.
		MaxAttempts:    envInt("EDITOR_MAX_ATTEMPTS", 3),
		RequestTimeout: time.Duration(envInt("EDITOR_TIMEOUT_SEC", 120)) * time.Second,
		MaxTextBytes:   envInt("EDITOR_MAX_TEXT_BYTES", 512*1024),
		OpenBotURL:     os.Getenv("EDITOR_OPENBOT_URL"),
		OpenBotToken:   os.Getenv("EDITOR_OPENBOT_TOKEN"),
	}

	switch c.Provider {
	case "agui":
		if c.Model == "" {
			c.Model = "gpt-5.6-luna"
		}
		if c.AGUIURL == "" {
			return nil, fmt.Errorf("для agui нужен EDITOR_AGUI_URL")
		}
		if c.AGUIToken == "" {
			return nil, fmt.Errorf("для agui нужен EDITOR_AGUI_TOKEN")
		}
	case "openai":
		if c.Model == "" {
			c.Model = "gpt-4o-mini"
		}
	case "openrouter":
		if c.Model == "" {
			c.Model = "anthropic/claude-sonnet-4.5"
		}
	case "cloudflare":
		if c.Model == "" {
			c.Model = "@cf/meta/llama-3.3-70b-instruct-fp8-fast"
		}
		if c.AccountID == "" {
			return nil, fmt.Errorf("для cloudflare нужен EDITOR_CF_ACCOUNT_ID")
		}
	default:
		return nil, fmt.Errorf("неизвестный провайдер %q: agui, openai, openrouter или cloudflare", c.Provider)
	}

	if c.Provider != "agui" && c.APIKey == "" {
		return nil, fmt.Errorf("нужен EDITOR_API_KEY")
	}
	return c, nil
}

// Redacted — безопасное представление для /health и логов.
func (c *Config) Redacted() map[string]any {
	return map[string]any{
		"addr":              c.Addr,
		"analyzer_url":      c.AnalyzerURL,
		"agent_token_set":   c.AgentToken != "",
		"agui_url":          c.AGUIURL,
		"agui_token_set":    c.AGUIToken != "",
		"provider":          c.Provider,
		"model":             c.Model,
		"reasoning_effort":  c.ReasoningEffort,
		"base_url":          c.BaseURL,
		"max_attempts":      c.MaxAttempts,
		"api_key_set":       c.APIKey != "",
		"openbot_url":       c.OpenBotURL,
		"openbot_token_set": c.OpenBotToken != "",
	}
}
