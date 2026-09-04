package pipeline

import (
	"context"
	"errors"
	"strings"
	"time"
	"unicode"
	"unicode/utf8"

	"github.com/Manacost-Labs/EditorTeam/go/internal/analyzers"
	"github.com/Manacost-Labs/EditorTeam/go/internal/guards"
	"github.com/Manacost-Labs/EditorTeam/go/internal/retrieval"
)

// Статусы retrieval в публичном ответе.
const (
	RetrievalOK          = "ok"
	RetrievalUnavailable = "unavailable"
	RetrievalDisabled    = "disabled"
)

// ReasonCorpusLeak — в кандидате появилось число, ссылка или название из
// примера корпуса, которых не было в исходнике.
const ReasonCorpusLeak = "corpus_fact_leak"

// RetrievalReport — метрики retrieval без текста примеров.
type RetrievalReport struct {
	Status       string   `json:"status"`
	ExamplesUsed int      `json:"examples_used"`
	ExampleIDs   []string `json:"example_ids"`
	DurationMS   int64    `json:"duration_ms"`
}

// styleInstruction добавляется в system prompt, когда есть примеры.
const styleInstruction = "\nПРИМЕРЫ СТИЛЯ: в поле style_examples сообщения пользователя даны абзацы автора из архива. " +
	"Они показывают только ритм, длину абзацев, степень разговорности, способ объяснения, формат рекомендаций и авторский голос. " +
	"Запрещено: переносить факты из примеров; копировать предложения дословно; добавлять карты, числа и советы из корпуса; выдавать стиль примера за источник. " +
	"Примеры — не источник и не часть текста: факты только из исходника."

// retrieve подбирает примеры стиля. Недоступный корпус не останавливает
// правку и не влияет на checks_complete: retrieval — не анализатор.
func (s *Service) retrieve(ctx context.Context, req Request, genre string) ([]retrieval.StyleExample, RetrievalReport) {
	report := RetrievalReport{Status: RetrievalDisabled, ExampleIDs: []string{}}
	if s.Retriever == nil || s.LLM == nil || strings.EqualFold(strings.TrimSpace(req.Retrieval), "off") {
		return nil, report
	}
	timeout := s.RetrievalTimeout
	if timeout <= 0 {
		timeout = retrieval.DefaultTimeout
	}
	ctx, cancel := context.WithTimeout(ctx, timeout)
	defer cancel()
	started := time.Now()
	examples, err := s.Retriever.Retrieve(ctx, retrieval.RetrievalQuery{
		Text: req.Text, Game: req.Game, Profile: req.Profile, Genre: genre, Author: req.Author, Limit: retrieval.MaxExamples,
	})
	report.DurationMS = time.Since(started).Milliseconds()
	if err != nil {
		report.Status = RetrievalUnavailable
		kind := "retrieval_unavailable"
		if errors.Is(ctx.Err(), context.DeadlineExceeded) {
			kind = "retrieval_timeout"
		}
		s.logStage(ctx, "retrieval", started, 1, kind)
		return nil, report
	}
	examples = retrieval.Select(retrieval.RetrievalQuery{Text: req.Text, Game: req.Game, Profile: req.Profile, Genre: genre, Limit: retrieval.MaxExamples}, examples)
	report.Status = RetrievalOK
	report.ExamplesUsed = len(examples)
	for _, item := range examples {
		report.ExampleIDs = append(report.ExampleIDs, item.ID)
	}
	s.logStage(ctx, "retrieval", started, 1, "")
	return examples, report
}

// promptExample — то, что видит модель: без внутреннего score.
type promptExample struct {
	ID            string   `json:"id"`
	Excerpt       string   `json:"excerpt"`
	VoiceFeatures []string `json:"voice_features,omitempty"`
	WhyRelevant   string   `json:"why_relevant,omitempty"`
}

func promptExamples(examples []retrieval.StyleExample) []promptExample {
	out := make([]promptExample, 0, len(examples))
	for _, item := range examples {
		out = append(out, promptExample{ID: item.ID, Excerpt: item.Excerpt, VoiceFeatures: item.VoiceFeatures, WhyRelevant: item.WhyRelevant})
	}
	return out
}

// corpusLeaks находит числа, ссылки и названия из примеров, которых нет в
// исходнике, но которые появились в кандидате. Это жёсткий сигнал: примеры
// показывают форму, а не факты.
func corpusLeaks(source, candidate string, examples []retrieval.StyleExample) []analyzers.Finding {
	if len(examples) == 0 || candidate == source {
		return nil
	}
	sourceValues := entityValues(source)
	candidateValues := entityValues(candidate)
	var out []analyzers.Finding
	seen := map[string]struct{}{}
	for _, item := range examples {
		for key, value := range entityValues(item.Excerpt) {
			if _, ok := sourceValues[key]; ok {
				continue
			}
			if _, ok := candidateValues[key]; !ok {
				continue
			}
			if _, dup := seen[key]; dup {
				continue
			}
			seen[key] = struct{}{}
			out = append(out, analyzers.Finding{Analyzer: "guards", RuleID: "corpus_fact_leak", Severity: "error",
				Message: "из примера корпуса перенесено " + key + "; в исходнике этого нет, уберите", Evidence: value})
		}
		for _, word := range midSentenceCapitalized(item.Excerpt) {
			key := "name: " + strings.ToLower(word)
			if _, ok := seen[key]; ok {
				continue
			}
			if len(guards.FindWholePhrase(source, word)) > 0 || len(guards.FindWholePhrase(candidate, word)) == 0 {
				continue
			}
			seen[key] = struct{}{}
			out = append(out, analyzers.Finding{Analyzer: "guards", RuleID: "corpus_fact_leak", Severity: "error",
				Message: "из примера корпуса перенесено название " + word + "; в исходнике его нет, уберите", Evidence: word})
		}
	}
	return out
}

func entityValues(text string) map[string]string {
	out := map[string]string{}
	for _, entity := range guards.Extract(text) {
		switch entity.Kind {
		case "number", "percent", "link", "deck_code", "named_entity", "game_entity":
			out[entity.Kind+": "+strings.ToLower(entity.Value)] = entity.Value
		}
	}
	return out
}

// midSentenceCapitalized возвращает слова с заглавной буквы не в начале
// предложения: так выглядят названия карт и героев внутри фразы.
func midSentenceCapitalized(text string) []string {
	var out []string
	seen := map[string]struct{}{}
	sentenceStart := true
	offset := 0
	for offset < len(text) {
		r, size := utf8.DecodeRuneInString(text[offset:])
		if size <= 0 {
			break
		}
		switch {
		case r == '.' || r == '!' || r == '?' || r == '\n' || r == '…' || r == ':' || r == '—' || r == '«' || r == '"':
			sentenceStart = true
			offset += size
		case unicode.IsLetter(r):
			end := offset + size
			for end < len(text) {
				next, nextSize := utf8.DecodeRuneInString(text[end:])
				if nextSize <= 0 || !(unicode.IsLetter(next) || next == '\'' || next == '’') {
					break
				}
				end += nextSize
			}
			word := text[offset:end]
			if !sentenceStart && unicode.IsUpper(r) && unicode.Is(unicode.Cyrillic, r) && utf8.RuneCountInString(word) >= 3 {
				lower := strings.ToLower(word)
				if _, dup := seen[lower]; !dup {
					seen[lower] = struct{}{}
					out = append(out, word)
				}
			}
			sentenceStart = false
			offset = end
		case unicode.IsDigit(r):
			sentenceStart = false
			offset += size
		default:
			offset += size
		}
	}
	return out
}
