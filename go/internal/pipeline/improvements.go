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
// candidate, они различаются после нормализации, reason конкретен, фрагмент
// не состоит из одних пробелов и знаков, а одно и то же изменение не
// повторяется. Сравнение точное и Unicode-safe: переводы строк и повторные
// пробелы схлопываются, регистр и слова не меняются. Fuzzy matching здесь
// намеренно не используется: утверждению critic без цитаты не верят.
func ValidateImprovements(source, candidate string, improvements []Improvement) []Improvement {
	normSource := normalizeSpace(source)
	normCandidate := normalizeSpace(candidate)
	seen := map[string]struct{}{}
	out := []Improvement{}
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
		if !concreteReason(item.Reason) {
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
	words := strings.FieldsFunc(reason, func(r rune) bool { return !unicode.IsLetter(r) && !unicode.IsDigit(r) })
	return len(words) >= 3
}
