package pipeline

import (
	"regexp"
	"strings"
	"unicode"

	"github.com/Manacost-Labs/EditorTeam/go/internal/analyzers"
	"github.com/Manacost-Labs/EditorTeam/go/internal/retrieval"
)

// Пороги копирования: 10–13 одинаковых подряд слов — warning, 14 и больше —
// error; два разных совпадения по 10 и больше слов из одного примера — error.
const (
	CopyWarningWords = 10
	CopyErrorWords   = 14
	copyEvidenceMax  = 160
)

// ReasonCorpusCopy — кандидат дословно копирует фрагмент стилевого примера.
const ReasonCorpusCopy = "corpus_copy"

var markdownNoise = regexp.MustCompile("(?m)^#{1,6}\\s+|^\\s*>\\s?|^\\s*[-*+]\\s+|^\\s*\\d+[.)]\\s+|\\*\\*|__|`+|~~")
var markdownLink = regexp.MustCompile(`\[([^\]]*)\]\([^)]*\)`)

// copyTokens: нижний регистр, без служебной Markdown-разметки, токены по
// Unicode-словам (буквы и цифры).
func copyTokens(text string) []string {
	cleaned := markdownLink.ReplaceAllString(text, "$1")
	cleaned = markdownNoise.ReplaceAllString(cleaned, " ")
	cleaned = strings.ToLower(cleaned)
	return strings.FieldsFunc(cleaned, func(r rune) bool { return !unicode.IsLetter(r) && !unicode.IsDigit(r) })
}

func shingles(tokens []string, size int) map[string][]int {
	out := map[string][]int{}
	for index := 0; index+size <= len(tokens); index++ {
		key := strings.Join(tokens[index:index+size], " ")
		out[key] = append(out[key], index)
	}
	return out
}

type copyRun struct {
	start, length int
}

// matchingRuns находит максимальные общие последовательности слов между
// candidate и одним примером длиной не меньше size, пропуская те, что
// целиком есть в source.
func matchingRuns(candidate, example, source []string, size int) []copyRun {
	exampleShingles := shingles(example, size)
	sourceText := " " + strings.Join(source, " ") + " "
	var runs []copyRun
	index := 0
	for index+size <= len(candidate) {
		key := strings.Join(candidate[index:index+size], " ")
		positions, ok := exampleShingles[key]
		if !ok {
			index++
			continue
		}
		longest := size
		for _, position := range positions {
			length := size
			for index+length < len(candidate) && position+length < len(example) && candidate[index+length] == example[position+length] {
				length++
			}
			if length > longest {
				longest = length
			}
		}
		fragment := " " + strings.Join(candidate[index:index+longest], " ") + " "
		if !strings.Contains(sourceText, fragment) {
			runs = append(runs, copyRun{start: index, length: longest})
		}
		index += longest
	}
	return runs
}

// DetectCorpusCopy сравнивает кандидата с каждым стилевым примером и
// возвращает findings corpus_copy. Распространённые короткие обороты в
// 2–5 слов ниже порога и никогда не блокируют текст.
func DetectCorpusCopy(source, candidate string, examples []retrieval.StyleExample) []analyzers.Finding {
	if len(examples) == 0 || candidate == "" {
		return nil
	}
	candidateTokens := copyTokens(candidate)
	sourceTokens := copyTokens(source)
	var out []analyzers.Finding
	for _, item := range examples {
		exampleTokens := copyTokens(item.Excerpt)
		runs := matchingRuns(candidateTokens, exampleTokens, sourceTokens, CopyWarningWords)
		if len(runs) == 0 {
			continue
		}
		longest := 0
		for _, run := range runs {
			if run.length > longest {
				longest = run.length
			}
		}
		severity := "warning"
		if longest >= CopyErrorWords || len(runs) >= 2 {
			severity = "error"
		}
		for _, run := range runs {
			evidence := strings.Join(candidateTokens[run.start:run.start+run.length], " ")
			if runes := []rune(evidence); len(runes) > copyEvidenceMax {
				evidence = string(runes[:copyEvidenceMax-1]) + "…"
			}
			out = append(out, analyzers.Finding{
				Analyzer: "guards", RuleID: ReasonCorpusCopy, Severity: severity,
				Message:  "Кандидат дословно копирует фрагмент стилевого примера",
				Evidence: evidence, Length: run.length, Field: item.ID,
			})
		}
	}
	return out
}
