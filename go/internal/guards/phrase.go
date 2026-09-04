package guards

import (
	"strings"
	"unicode"
	"unicode/utf8"
)

// Span — вхождение фразы в тексте в байтовых смещениях UTF-8.
type Span struct {
	Start, End int
}

// FindWholePhrase находит все вхождения phrase в text как целого слова или
// составного названия. Сравнение регистронезависимое (strings.EqualFold по
// рунам), границей считается любой символ, который не буква, не цифра и не
// «_», а также начало и конец текста. Go-регулярное \b здесь непригодно:
// это ASCII-граница, и рядом с кириллицей она не срабатывает.
func FindWholePhrase(text, phrase string) []Span {
	phrase = strings.TrimSpace(phrase)
	if phrase == "" || text == "" {
		return nil
	}
	target := []rune(phrase)
	var out []Span
	for offset := 0; offset < len(text); {
		r, size := utf8.DecodeRuneInString(text[offset:])
		if size <= 0 {
			break
		}
		if !isWordRune(r) || (offset > 0 && !boundaryBefore(text, offset)) {
			offset += size
			continue
		}
		end, ok := matchRunes(text, offset, target)
		if ok && boundaryAfter(text, end) {
			out = append(out, Span{Start: offset, End: end})
			offset = end
			continue
		}
		offset += size
	}
	return out
}

// matchRunes compares the text at offset with target rune by rune using
// unicode simple folding, returning the byte offset after the match.
func matchRunes(text string, offset int, target []rune) (int, bool) {
	pos := offset
	for _, want := range target {
		if pos >= len(text) {
			return 0, false
		}
		got, size := utf8.DecodeRuneInString(text[pos:])
		if size <= 0 || !strings.EqualFold(string(got), string(want)) {
			return 0, false
		}
		pos += size
	}
	return pos, true
}

func isWordRune(r rune) bool {
	return unicode.IsLetter(r) || unicode.IsDigit(r) || r == '_'
}

func boundaryBefore(text string, offset int) bool {
	if offset <= 0 {
		return true
	}
	prev, _ := utf8.DecodeLastRuneInString(text[:offset])
	return !isWordRune(prev)
}

func boundaryAfter(text string, offset int) bool {
	if offset >= len(text) {
		return true
	}
	next, _ := utf8.DecodeRuneInString(text[offset:])
	return !isWordRune(next)
}

// findCapitalizedPhrases returns spans of two or more consecutive words that
// each start with an uppercase letter followed by lowercase letters
// («Огненный шар» is not matched here: it is a card name handled by the
// Python reference; «Рыцарь Смерти», «Замок Нафрия» are). Words are separated
// by single spaces so a sentence boundary never glues two names together.
func findCapitalizedPhrases(text string) []Span {
	type word struct{ start, end int }
	var words []word
	var out []Span
	flush := func() {
		if len(words) >= 2 {
			out = append(out, Span{Start: words[0].start, End: words[len(words)-1].end})
		}
		words = nil
	}
	offset := 0
	for offset < len(text) {
		r, size := utf8.DecodeRuneInString(text[offset:])
		if size <= 0 {
			break
		}
		if !unicode.IsUpper(r) || !unicode.Is(unicode.Cyrillic, r) || !boundaryBefore(text, offset) {
			if r == ' ' && len(words) > 0 && offset == words[len(words)-1].end {
				// a single space between candidate words keeps the phrase open
				offset += size
				continue
			}
			flush()
			offset += size
			continue
		}
		end := offset + size
		for end < len(text) {
			next, nextSize := utf8.DecodeRuneInString(text[end:])
			if nextSize <= 0 {
				break
			}
			// «Кел'Тузад» is one word: an apostrophe joins letters of any case.
			if next == '\'' || next == '’' {
				after, afterSize := utf8.DecodeRuneInString(text[end+nextSize:])
				if afterSize > 0 && unicode.IsLetter(after) && unicode.Is(unicode.Cyrillic, after) {
					end += nextSize + afterSize
					continue
				}
				break
			}
			if !unicode.IsLower(next) || !unicode.Is(unicode.Cyrillic, next) {
				break
			}
			end += nextSize
		}
		if end == offset+size || !boundaryAfter(text, end) {
			flush()
			offset = end
			if end == offset {
				offset += size
			}
			continue
		}
		if len(words) > 0 && words[len(words)-1].end+1 != offset {
			flush()
		}
		words = append(words, word{start: offset, end: end})
		offset = end
	}
	flush()
	return out
}
