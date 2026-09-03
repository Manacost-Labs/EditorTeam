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
	Addr                string
	AnalyzerURL         string
	AgentToken          string // необязательный Bearer-токен для AG-UI endpoint
	Provider            string // none | agui | openai | openrouter | cloudflare | ollama
	AGUIURL             string // внутренний AG-UI endpoint, если Provider=agui
	AGUIToken           string // Bearer-токен внутреннего AG-UI endpoint
	ReasoningEffort     string // передаётся внутреннему Codex; например xhigh
	Model               string
	APIKey              string
	AccountID           string // нужен только Cloudflare
	BaseURL             string // свой адрес провайдера: прокси или self-hosted
	MaxAttempts         int
	RequestTimeout      time.Duration
	MaxTextBytes        int
	OpenBotURL          string // внутренний TeamBot endpoint для подготовки подтверждаемой записи
	OpenBotToken        string // сервисный токен; никогда не передаётся модели
	LanguageToolURL     string
	LanguageToolTimeout time.Duration
	NatashaURL          string
	NatashaTimeout      time.Duration
	HunspellBinary      string
	RussianDictionary   string
	HunspellTimeout     time.Duration
	MarkdownlintBinary  string
	MarkdownlintConfig  string
	MarkdownlintTimeout time.Duration
	PromptfooConfig     string
	EvalMode            string
	PromptVariant       string // baseline | candidate; выбирается только окружением сервера
	AllowUnavailable    bool
	ValeBinary          string
	ValeConfig          string
	ValeTimeout         time.Duration
	PythonBinary        string
	PythonTimeout       time.Duration
	MaxProcessOutput    int64
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
		BaseURL:         env("EDITOR_BASE_URL", os.Getenv("OLLAMA_BASE_URL")),
		// Больше трёх попыток редко помогают: если модель дважды вычистила
		// голос, она это делает системно, и повторы только жгут токены.
		MaxAttempts:         envInt("EDITOR_MAX_ATTEMPTS", 3),
		RequestTimeout:      time.Duration(envInt("EDITOR_TIMEOUT_SEC", 120)) * time.Second,
		MaxTextBytes:        envInt("EDITOR_MAX_TEXT_BYTES", 512*1024),
		OpenBotURL:          os.Getenv("EDITOR_OPENBOT_URL"),
		OpenBotToken:        os.Getenv("EDITOR_OPENBOT_TOKEN"),
		LanguageToolURL:     os.Getenv("LANGUAGETOOL_URL"),
		LanguageToolTimeout: time.Duration(envInt("LANGUAGETOOL_TIMEOUT_SEC", 8)) * time.Second,
		NatashaURL:          os.Getenv("NATASHA_URL"),
		NatashaTimeout:      time.Duration(envInt("NATASHA_TIMEOUT_SEC", 8)) * time.Second,
		HunspellBinary:      env("HUNSPELL_BIN", "hunspell"),
		RussianDictionary:   os.Getenv("RU_DICT_PATH"),
		HunspellTimeout:     time.Duration(envInt("HUNSPELL_TIMEOUT_SEC", 8)) * time.Second,
		MarkdownlintBinary:  env("MARKDOWNLINT_BIN", "markdownlint-cli2"),
		MarkdownlintConfig:  os.Getenv("MARKDOWNLINT_CONFIG"),
		MarkdownlintTimeout: time.Duration(envInt("MARKDOWNLINT_TIMEOUT_SEC", 8)) * time.Second,
		PromptfooConfig:     env("PROMPTFOO_CONFIG", "evals/promptfooconfig.yaml"),
		EvalMode:            env("EDITOR_EVAL_MODE", "candidate"),
		PromptVariant:       env("EDITOR_PROMPT_VARIANT", "candidate"),
		AllowUnavailable:    env("EDITOR_ALLOW_UNAVAILABLE", "false") == "true",
		ValeBinary:          env("VALE_BIN", env("VALE_BINARY", "vale")),
		ValeConfig:          os.Getenv("VALE_CONFIG"),
		ValeTimeout:         time.Duration(envInt("VALE_TIMEOUT_SEC", 8)) * time.Second,
		PythonBinary:        env("EDITOR_PYTHON", "python3"),
		PythonTimeout:       time.Duration(envInt("EDITOR_PYTHON_TIMEOUT_SEC", 20)) * time.Second,
		MaxProcessOutput:    int64(envInt("EDITOR_PROCESS_MAX_OUTPUT", 4*1024*1024)),
	}

	switch c.Provider {
	case "none":
		if c.Model == "" {
			c.Model = "disabled"
		}
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
	case "ollama":
		if c.Model == "" {
			c.Model = "llama3.1"
		}
		if c.BaseURL == "" {
			c.BaseURL = "http://127.0.0.1:11434/v1"
		}
	default:
		return nil, fmt.Errorf("неизвестный провайдер %q: agui, openai, openrouter, cloudflare или ollama", c.Provider)
	}
	if c.PromptVariant != "baseline" && c.PromptVariant != "candidate" {
		return nil, fmt.Errorf("неизвестный EDITOR_PROMPT_VARIANT %q: baseline или candidate", c.PromptVariant)
	}

	if c.Provider != "agui" && c.Provider != "none" && c.Provider != "ollama" && c.APIKey == "" {
		return nil, fmt.Errorf("нужен EDITOR_API_KEY")
	}
	return c, nil
}

// Redacted — безопасное представление для /health и логов.
func (c *Config) Redacted() map[string]any {
	return map[string]any{
		"addr":                 c.Addr,
		"analyzer_url":         c.AnalyzerURL,
		"agent_token_set":      c.AgentToken != "",
		"agui_url":             c.AGUIURL,
		"agui_token_set":       c.AGUIToken != "",
		"provider":             c.Provider,
		"model":                c.Model,
		"reasoning_effort":     c.ReasoningEffort,
		"base_url":             c.BaseURL,
		"max_attempts":         c.MaxAttempts,
		"api_key_set":          c.APIKey != "",
		"openbot_url":          c.OpenBotURL,
		"openbot_token_set":    c.OpenBotToken != "",
		"languagetool_url_set": c.LanguageToolURL != "",
		"natasha_url_set":      c.NatashaURL != "",
		"hunspell_binary":      c.HunspellBinary,
		"dictionary_set":       c.RussianDictionary != "",
		"markdownlint_binary":  c.MarkdownlintBinary,
		"markdownlint_config":  c.MarkdownlintConfig,
		"promptfoo_config":     c.PromptfooConfig,
		"eval_mode":            c.EvalMode,
		"prompt_variant":       c.PromptVariant,
		"allow_unavailable":    c.AllowUnavailable,
		"vale_binary":          c.ValeBinary,
	}
}
