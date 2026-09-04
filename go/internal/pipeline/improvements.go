package pipeline

import (
	"strings"
	"unicode"
)

// MaxImprovementFragment — предел длины before/after: critic цитирует
// короткие выдержки, а не абзацы.
const MaxImprovementFragment = 300

// improvementCategories — единственные допустимые категории улучшений.
var improvementCategories = map[string]struct{}{
	"clarity": {}, "structure": {}, "usefulness": {}, "natural_russian": {},
	"author_voice": {}, "conciseness": {}, "terminology": {},
}

// ValidateImprovements оставляет только доказанные улучшения: категория из
// списка, before дословно встречается в source, after дословно встречается в
// candidate, оба относятся к одному изменённому hunk токенного diff (не к
// произвольным несвязанным местам и не к неизменённому тексту), они
// различаются после нормализации, reason конкретен и не пуст по смыслу,
// фрагмент не состоит из одних пробелов и знаков, а одно и то же изменение
// не повторяется. Сравнение точное и Unicode-safe: переводы строк и
// повторные пробелы схлопываются, регистр и слова не меняются. Fuzzy
// matching здесь намеренно не используется: утверждению critic без цитаты
// из настоящего изменения не верят.
func ValidateImprovements(source, candidate string, improvements []Improvement) []Improvement {
	normSource := normalizeSpace(source)
	normCandidate := normalizeSpace(candidate)
	sourceTokens := diffTokenize(normSource)
	candidateTokens := diffTokenize(normCandidate)
	hunks := diffHunks(tokenTexts(sourceTokens), tokenTexts(candidateTokens))
	seen := map[string]struct{}{}
	out := []Improvement{}
	if len(hunks) == 0 {
		// Текст не изменился (или отличается только пробелами): улучшать нечего.
		return out
	}
	for _, item := range improvements {
		category := strings.ToLower(strings.TrimSpace(item.Category))
		if _, ok := improvementCategories[category]; !ok {
			continue
		}
		before := normalizeSpace(item.Before)
		after := normalizeSpace(item.After)
		if !meaningfulFragment(before) || !meaningfulFragment(after) {
			continue
		}
		if len([]rune(before)) > MaxImprovementFragment || len([]rune(after)) > MaxImprovementFragment {
			continue
		}
		if before == after {
			continue
		}
		if !strings.Contains(normSource, before) || !strings.Contains(normCandidate, after) {
			continue
		}
		if !sameHunk(tokenRanges(normSource, sourceTokens, before), tokenRanges(normCandidate, candidateTokens, after), hunks) {
			continue
		}
		if !concreteReason(item.Reason) || genericReason(item.Reason) {
			continue
		}
		key := before + "\x00" + after
		if _, dup := seen[key]; dup {
			continue
		}
		seen[key] = struct{}{}
		out = append(out, Improvement{Category: category, Before: before, After: after, Reason: strings.TrimSpace(item.Reason)})
	}
	return out
}

// normalizeSpace превращает любые пробельные последовательности, включая
// переводы строк, в один пробел и обрезает края. Буквы не меняются.
func normalizeSpace(text string) string {
	return strings.Join(strings.FieldsFunc(text, unicode.IsSpace), " ")
}

// meaningfulFragment требует хотя бы одну букву или цифру: фрагмент из
// пробелов и знаков препинания ничего не доказывает.
func meaningfulFragment(fragment string) bool {
	if fragment == "" {
		return false
	}
	for _, r := range fragment {
		if unicode.IsLetter(r) || unicode.IsDigit(r) {
			return true
		}
	}
	return false
}

// concreteReason отвергает пустые и односложные объяснения вроде «лучше».
func concreteReason(reason string) bool {
	return len(reasonWords(reason)) >= 3
}

func reasonWords(reason string) []string {
	return strings.FieldsFunc(strings.ToLower(reason), func(r rune) bool { return !unicode.IsLetter(r) && !unicode.IsDigit(r) })
}

// genericReasons — заведомо пустые объяснения; их близкие варианты ловит
// fillerWords: если после снятия слов-заполнителей не остаётся двух
// содержательных слов, объяснение ничего не объясняет.
var genericReasons = map[string]struct{}{
	"стало намного лучше":          {},
	"стало лучше":                  {},
	"текст стал лучше":             {},
	"текст стал яснее":             {},
	"стало яснее":                  {},
	"текст стал понятнее":          {},
	"улучшена читаемость текста":   {},
	"улучшена читаемость":          {},
	"читаемость улучшена":          {},
	"теперь звучит естественнее":   {},
	"звучит естественнее":          {},
	"текст звучит естественнее":    {},
	"теперь читается легче":        {},
	"фраза стала лучше":            {},
	"формулировка стала точнее":    {},
	"улучшено качество текста":     {},
	"текст стал более читаемым":    {},
	"стало более понятно":          {},
	"так лучше":                    {},
	"стало гораздо лучше":          {},
	"текст стал значительно лучше": {},
}

var fillerWords = map[string]struct{}{
	"теперь": {}, "намного": {}, "гораздо": {}, "значительно": {}, "заметно": {}, "существенно": {}, "более": {}, "очень": {},
	"текст": {}, "фраза": {}, "формулировка": {}, "предложение": {}, "абзац": {},
	"стал": {}, "стала": {}, "стало": {}, "стали": {}, "становится": {}, "звучит": {}, "читается": {}, "выглядит": {},
	"лучше": {}, "яснее": {}, "понятнее": {}, "естественнее": {}, "легче": {}, "проще": {}, "точнее": {}, "чище": {}, "короче": {},
	"улучшена": {}, "улучшен": {}, "улучшено": {}, "улучшены": {}, "улучшение": {}, "читаемость": {}, "восприятие": {}, "качество": {},
	"хорошо": {}, "отлично": {}, "так": {}, "это": {}, "и": {}, "а": {}, "но": {}, "же": {}, "ещё": {}, "еще": {}, "в": {}, "на": {},
	"понятно": {}, "ясно": {}, "естественно": {}, "читаемым": {}, "читаемой": {}, "ровнее": {}, "плавнее": {},
}

// genericReason: точное совпадение со списком или вариант, в котором после
// снятия заполнителей остаётся меньше двух содержательных слов.
func genericReason(reason string) bool {
	words := reasonWords(reason)
	if _, ok := genericReasons[strings.Join(words, " ")]; ok {
		return true
	}
	informative := 0
	for _, word := range words {
		if _, filler := fillerWords[word]; !filler {
			informative++
		}
	}
	return informative < 2
}

func tokenTexts(tokens []diffToken) []string {
	out := make([]string, len(tokens))
	for i, token := range tokens {
		out[i] = token.text
	}
	return out
}

// sameHunk: хотя бы одна пара вхождений before/after относится к одному и
// тому же изменению.
func sameHunk(beforeRanges, afterRanges [][2]int, hunks []hunk) bool {
	for _, h := range hunks {
		for _, before := range beforeRanges {
			if !touches(before[0], before[1], h.delStart, h.delEnd) {
				continue
			}
			for _, after := range afterRanges {
				if touches(after[0], after[1], h.insStart, h.insEnd) {
					return true
				}
			}
		}
	}
	return false
}
