// Package retrieval подбирает примеры авторского стиля из существующего
// корпуса. Хранилище и поиск остаются в Python-сайдкаре (/corpus/examples);
// Go отвечает за жёсткие фильтры, лимиты и исключение самого текста.
package retrieval

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"sort"
	"strings"
	"time"
	"unicode"

	"github.com/Manacost-Labs/EditorTeam/go/internal/analyzer"
)

// Лимиты: не больше трёх примеров, не длиннее 1200 знаков каждый и 3000 в сумме.
const (
	MaxExamples    = 3
	MaxExcerptLen  = 1200
	MaxTotalLen    = 3000
	DefaultTimeout = 5 * time.Second
)

var ErrUnavailable = errors.New("корпус недоступен")

type RetrievalQuery struct {
	Text    string
	Game    string
	Profile string
	Genre   string
	Author  string
	Limit   int
}

// StyleExample — абзац автора: только форма. Excerpt никогда не попадает в
// публичный API, только в prompt.
type StyleExample struct {
	ID            string   `json:"id"`
	Game          string   `json:"game"`
	Profile       string   `json:"profile"`
	Excerpt       string   `json:"excerpt"`
	VoiceFeatures []string `json:"voice_features"`
	WhyRelevant   string   `json:"why_relevant"`
	Score         float64  `json:"score"`
}

type CorpusRetriever interface {
	Retrieve(ctx context.Context, query RetrievalQuery) ([]StyleExample, error)
}

// HTTPRetriever вызывает существующий Python-сайдкар. Он не хранит статьи.
type HTTPRetriever struct {
	Client  *analyzer.Client
	Timeout time.Duration
}

func NewHTTP(client *analyzer.Client, timeout time.Duration) *HTTPRetriever {
	if timeout <= 0 {
		timeout = DefaultTimeout
	}
	return &HTTPRetriever{Client: client, Timeout: timeout}
}

type sidecarResponse struct {
	Status   string         `json:"status"`
	Examples []StyleExample `json:"examples"`
}

func (r *HTTPRetriever) Retrieve(ctx context.Context, query RetrievalQuery) ([]StyleExample, error) {
	if r == nil || r.Client == nil {
		return nil, ErrUnavailable
	}
	ctx, cancel := context.WithTimeout(ctx, r.Timeout)
	defer cancel()
	limit := query.Limit
	if limit <= 0 || limit > MaxExamples {
		limit = MaxExamples
	}
	raw, err := r.Client.Forward(ctx, "/corpus/examples", map[string]any{
		"text": query.Text, "game": query.Game, "profile": query.Profile, "genre": query.Genre,
		"author": query.Author, "limit": limit * 3, "exclude_hash": ContentHash(query.Text),
	})
	if err != nil {
		return nil, fmt.Errorf("%w: %v", ErrUnavailable, err)
	}
	var parsed sidecarResponse
	if err := json.Unmarshal(raw, &parsed); err != nil {
		return nil, fmt.Errorf("%w: ответ не JSON", ErrUnavailable)
	}
	if parsed.Status != "" && parsed.Status != "ok" {
		return nil, fmt.Errorf("%w: status=%s", ErrUnavailable, parsed.Status)
	}
	return Select(query, parsed.Examples), nil
}

// ContentHash — SHA-256 нормализованного текста (регистр и пробелы сняты).
func ContentHash(text string) string {
	sum := sha256.Sum256([]byte(normalize(text)))
	return hex.EncodeToString(sum[:])
}

func normalize(text string) string {
	var b strings.Builder
	space := false
	for _, r := range strings.ToLower(text) {
		if unicode.IsSpace(r) {
			space = true
			continue
		}
		if space && b.Len() > 0 {
			b.WriteByte(' ')
		}
		space = false
		b.WriteRune(unicode.ToLower(r))
	}
	return b.String()
}

var genreFamilies = map[string]string{
	"guide": "guide", "constructed-guide": "guide", "battlegrounds-guide": "guide", "wow-guide": "guide",
	"analysis": "analysis", "analytics-article": "analysis", "battlegrounds-article": "analysis", "meta-report": "analysis",
	"news": "news",
}

func family(profile string) string {
	profile = strings.ToLower(strings.TrimSpace(profile))
	if f, ok := genreFamilies[profile]; ok {
		return f
	}
	return profile
}

// Select применяет жёсткие фильтры (та же игра, тот же профиль или жанр,
// тот же автор, если задан), исключает сам редактируемый текст по хешу и
// вложенности, ранжирует и режет по лимитам. Функция чистая: её проверяют
// тесты без сайдкара.
func Select(query RetrievalQuery, candidates []StyleExample) []StyleExample {
	game := strings.ToLower(strings.TrimSpace(query.Game))
	if game == "" {
		game = "hearthstone"
	}
	wantProfile := strings.ToLower(strings.TrimSpace(query.Profile))
	wantFamily := family(wantProfile)
	if wantFamily == "" {
		wantFamily = family(query.Genre)
	}
	queryHash := ContentHash(query.Text)
	queryNorm := normalize(query.Text)
	limit := query.Limit
	if limit <= 0 || limit > MaxExamples {
		limit = MaxExamples
	}

	type ranked struct {
		example StyleExample
		score   float64
	}
	var pool []ranked
	seen := map[string]struct{}{}
	for _, item := range candidates {
		excerpt := strings.TrimSpace(item.Excerpt)
		if excerpt == "" {
			continue
		}
		itemGame := strings.ToLower(strings.TrimSpace(item.Game))
		if itemGame == "" {
			itemGame = "hearthstone"
		}
		if itemGame != game {
			continue
		}
		itemProfile := strings.ToLower(strings.TrimSpace(item.Profile))
		sameProfile := wantProfile != "" && itemProfile == wantProfile
		if wantFamily != "" && !sameProfile && family(itemProfile) != wantFamily {
			continue
		}
		norm := normalize(excerpt)
		hash := ContentHash(excerpt)
		if hash == queryHash || strings.Contains(queryNorm, norm) || strings.Contains(norm, queryNorm) {
			continue
		}
		if _, dup := seen[hash]; dup {
			continue
		}
		seen[hash] = struct{}{}
		score := item.Score
		if sameProfile {
			score += 1.0
		}
		score += lengthAffinity(query.Text, excerpt)
		item.Excerpt = truncateRunes(excerpt, MaxExcerptLen)
		if item.WhyRelevant == "" {
			item.WhyRelevant = "тот же профиль материала"
		}
		pool = append(pool, ranked{example: item, score: score})
	}
	sort.SliceStable(pool, func(i, j int) bool { return pool[i].score > pool[j].score })

	out := []StyleExample{}
	total := 0
	for _, item := range pool {
		if len(out) >= limit {
			break
		}
		length := len([]rune(item.example.Excerpt))
		if total+length > MaxTotalLen {
			continue
		}
		item.example.Score = item.score
		out = append(out, item.example)
		total += length
	}
	return out
}

// lengthAffinity favours excerpts whose paragraph length is close to the
// median paragraph of the edited text: the model sees a comparable shape.
func lengthAffinity(text, excerpt string) float64 {
	target := medianParagraphLen(text)
	if target == 0 {
		return 0
	}
	got := float64(len([]rune(excerpt)))
	ratio := got / float64(target)
	if ratio > 1 {
		ratio = 1 / ratio
	}
	return ratio * 0.5
}

func medianParagraphLen(text string) int {
	var lengths []int
	for _, paragraph := range strings.Split(text, "\n\n") {
		if n := len([]rune(strings.TrimSpace(paragraph))); n > 0 {
			lengths = append(lengths, n)
		}
	}
	if len(lengths) == 0 {
		return 0
	}
	sort.Ints(lengths)
	return lengths[len(lengths)/2]
}

func truncateRunes(text string, limit int) string {
	runes := []rune(text)
	if len(runes) <= limit {
		return text
	}
	cut := runes[:limit]
	if idx := strings.LastIndexAny(string(cut), ".!?"); idx > limit/2 {
		return string(cut)[:idx+1]
	}
	return strings.TrimSpace(string(cut)) + "…"
}

// TotalLen — суммарная длина выдержек в рунах, для метрик и тестов.
func TotalLen(examples []StyleExample) int {
	total := 0
	for _, item := range examples {
		total += len([]rune(item.Excerpt))
	}
	return total
}
