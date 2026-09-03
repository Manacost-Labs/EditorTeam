// Package guards защищает элементы, которые модель не имеет права менять.
package guards

import (
	"regexp"
	"sort"
	"strings"
)

type Entity struct {
	Kind, Value string
	Start, End  int
}

type Report struct {
	Missing []Entity `json:"missing,omitempty"`
	Changed []string `json:"changed,omitempty"`
	Added   []string `json:"added,omitempty"`
	Risks   []string `json:"risks,omitempty"`
}

var patterns = []struct {
	kind string
	re   *regexp.Regexp
}{
	{"link", regexp.MustCompile(`https?://[^\s)]+|\[[^\]]+\]\([^)]*\)`)},
	{"deck_code", regexp.MustCompile(`\b[A-Za-z0-9+/]{35,}={0,2}\b`)},
	{"percent", regexp.MustCompile(`\b\d+(?:[.,]\d+)?\s*%`)},
	{"number", regexp.MustCompile(`\b\d+(?:[.,]\d+)?`)},
	{"markdown", regexp.MustCompile(`(?m)^#{1,6}\s|\*\*[^*]+\*\*|` + "`" + `[^` + "`" + `]+` + "`" + `|(?m)^\s*[-*+]\s`)},
	{"quote", regexp.MustCompile(`(?m)^>\s?.+$`)},
	{"html", regexp.MustCompile(`</?[A-Za-z][^>]*>`)},
	// Многословные имена с заглавных букв — безопасный минимум для карт,
	// персонажей и названий дополнений, пока локальный справочник остаётся
	// Python-ответственностью.
	{"named_entity", regexp.MustCompile(`\b[А-ЯЁ][а-яё]+(?:\s+[А-ЯЁ][а-яё]+)+\b`)},
}

var protectedWords = []string{"Воин", "Маг", "Жрец", "Охотник", "Разбойник", "Шаман", "Чернокнижник", "Паладин", "Друид", "Охотник на демонов", "Рыцарь смерти", "Поля сражений", "Темные дары", "Темный дар"}

func Extract(text string) []Entity {
	var out []Entity
	for _, p := range patterns {
		for _, loc := range p.re.FindAllStringIndex(text, -1) {
			out = append(out, Entity{Kind: p.kind, Value: text[loc[0]:loc[1]], Start: loc[0], End: loc[1]})
		}
	}
	for _, word := range protectedWords {
		for _, loc := range regexp.MustCompile(`(?i)\b`+regexp.QuoteMeta(word)+`\b`).FindAllStringIndex(text, -1) {
			out = append(out, Entity{Kind: "game_entity", Value: text[loc[0]:loc[1]], Start: loc[0], End: loc[1]})
		}
	}
	sort.Slice(out, func(i, j int) bool {
		if out[i].Start == out[j].Start {
			return out[i].End < out[j].End
		}
		return out[i].Start < out[j].Start
	})
	return out
}

func Compare(before, after string) Report {
	result := Report{}
	beforeEntities, afterEntities := Extract(before), Extract(after)
	for _, want := range beforeEntities {
		if countValue(afterEntities, want.Kind, want.Value) < countValue(beforeEntities, want.Kind, want.Value) {
			result.Missing = append(result.Missing, want)
		}
	}
	for _, got := range afterEntities {
		if !containsValue(beforeEntities, got.Kind, got.Value) {
			result.Added = append(result.Added, got.Kind+": "+got.Value)
		}
	}
	if negationCount(before) != negationCount(after) {
		result.Changed = append(result.Changed, "отрицания")
		result.Risks = append(result.Risks, "проверьте, не изменился ли смысл отрицания")
	}
	if uncertaintyCount(before) != uncertaintyCount(after) {
		result.Changed = append(result.Changed, "осторожные формулировки")
		result.Risks = append(result.Risks, "проверьте, не стала ли осторожная оценка категоричной")
	}
	for _, missing := range result.Missing {
		result.Changed = append(result.Changed, missing.Kind+": "+missing.Value)
	}
	return result
}

func containsValue(items []Entity, kind, value string) bool {
	for _, item := range items {
		if item.Kind == kind && item.Value == value {
			return true
		}
	}
	return false
}

func countValue(items []Entity, kind, value string) int {
	n := 0
	for _, item := range items {
		if item.Kind == kind && item.Value == value {
			n++
		}
	}
	return n
}
func negationCount(s string) int {
	return len(regexp.MustCompile(`(?i)\bне\b|\bникогда\b|\bнельзя\b`).FindAllStringIndex(s, -1))
}
func uncertaintyCount(s string) int {
	return len(regexp.MustCompile(`(?i)\bможет\b|\bвозможно\b|\bобычно\b|\bчасто\b|\bредко\b|\bскорее\b`).FindAllStringIndex(s, -1))
}

func (r Report) HasHardChanges() bool { return len(r.Missing) > 0 || len(r.Changed) > 0 }

func Values(items []Entity, kind string) []string {
	out := []string{}
	for _, item := range items {
		if item.Kind == kind {
			out = append(out, item.Value)
		}
	}
	return out
}

func NormalizeWhitespace(s string) string { return strings.Join(strings.Fields(s), " ") }
