// Package llm — клиент к провайдерам моделей.
//
// OpenRouter и Cloudflare Workers AI оба дают OpenAI-совместимый REST,
// поэтому реализация одна, различаются только адрес и заголовки.
package llm

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"strings"
	"time"
)

type Message struct {
	Role    string `json:"role"`
	Content string `json:"content"`
}

type Client struct {
	provider  string
	model     string
	apiKey    string
	accountID string
	http      *http.Client

	baseURL      string // свой адрес: прокси, self-hosted, локальная модель
	testEndpoint string // подменяется только в тестах
}

func New(provider, model, apiKey, accountID, baseURL string, timeout time.Duration) *Client {
	return &Client{
		baseURL:   baseURL,
		provider:  provider,
		model:     model,
		apiKey:    apiKey,
		accountID: accountID,
		http:      &http.Client{Timeout: timeout},
	}
}

func (c *Client) Model() string { return c.model }

func (c *Client) endpoint() string {
	if c.testEndpoint != "" {
		return c.testEndpoint
	}
	if c.baseURL != "" {
		return c.baseURL
	}
	if c.provider == "cloudflare" {
		return fmt.Sprintf(
			"https://api.cloudflare.com/client/v4/accounts/%s/ai/v1/chat/completions",
			c.accountID)
	}
	return "https://openrouter.ai/api/v1/chat/completions"
}

type chatRequest struct {
	Model       string    `json:"model"`
	Messages    []Message `json:"messages"`
	Temperature float64   `json:"temperature"`
	MaxTokens   int       `json:"max_tokens,omitempty"`
}

type chatResponse struct {
	Choices []struct {
		Message Message `json:"message"`
	} `json:"choices"`
	Error *struct {
		Message string `json:"message"`
	} `json:"error,omitempty"`
}

// Complete отправляет диалог и возвращает текст ответа.
func (c *Client) Complete(ctx context.Context, msgs []Message, maxTokens int) (string, error) {
	// Температура низкая намеренно: задача — правка по правилам, а не
	// сочинение. Разброс здесь означает разные правки на одном тексте.
	body, err := json.Marshal(chatRequest{
		Model: c.model, Messages: msgs, Temperature: 0.2, MaxTokens: maxTokens,
	})
	if err != nil {
		return "", fmt.Errorf("сборка запроса: %w", err)
	}

	req, err := http.NewRequestWithContext(ctx, http.MethodPost, c.endpoint(),
		bytes.NewReader(body))
	if err != nil {
		return "", fmt.Errorf("создание запроса: %w", err)
	}
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("Authorization", "Bearer "+c.apiKey)
	if c.provider == "openrouter" {
		req.Header.Set("X-Title", "EditorTeam")
	}

	resp, err := c.http.Do(req)
	if err != nil {
		return "", fmt.Errorf("запрос к %s: %w", c.provider, err)
	}
	defer resp.Body.Close()

	raw, err := io.ReadAll(io.LimitReader(resp.Body, 8<<20))
	if err != nil {
		return "", fmt.Errorf("чтение ответа: %w", err)
	}

	var parsed chatResponse
	if err := json.Unmarshal(raw, &parsed); err != nil {
		return "", fmt.Errorf("разбор ответа (%d): %s", resp.StatusCode, truncate(string(raw), 200))
	}
	if parsed.Error != nil {
		return "", fmt.Errorf("%s: %s", c.provider, parsed.Error.Message)
	}
	if resp.StatusCode >= 400 {
		return "", fmt.Errorf("%s вернул %d: %s", c.provider, resp.StatusCode,
			truncate(string(raw), 200))
	}
	if len(parsed.Choices) == 0 {
		return "", fmt.Errorf("%s вернул пустой ответ", c.provider)
	}
	return strings.TrimSpace(parsed.Choices[0].Message.Content), nil
}

func truncate(s string, n int) string {
	if len(s) <= n {
		return s
	}
	return s[:n] + "…"
}

// SetEndpointForTest подменяет адрес провайдера. Только для тестов:
// иначе клиент ходил бы в настоящий OpenRouter.
func (c *Client) SetEndpointForTest(url string) { c.testEndpoint = url }
