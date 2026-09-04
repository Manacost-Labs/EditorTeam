package guards

import (
	"strings"
	"testing"
)

func spansText(text string, spans []Span) []string {
	out := []string{}
	for _, span := range spans {
		out = append(out, text[span.Start:span.End])
	}
	return out
}

func TestFindWholePhraseFindsCyrillicEntitiesWithByteOffsets(t *testing.T) {
	text := "«Темные дары» усиливают **Рыцарь смерти**, а Маг — нет; Поля сражений, Огненный шар! Воин."
	for _, phrase := range []string{"Воин", "Маг", "Рыцарь смерти", "Поля сражений", "Темные дары", "Огненный шар"} {
		spans := FindWholePhrase(text, phrase)
		if len(spans) != 1 {
			t.Fatalf("%s: %d spans", phrase, len(spans))
		}
		got := text[spans[0].Start:spans[0].End]
		if !strings.EqualFold(got, phrase) {
			t.Fatalf("%s: byte offsets point at %q", phrase, got)
		}
		if spans[0].Start != strings.Index(text, got) {
			t.Fatalf("%s: offset %d, want %d", phrase, spans[0].Start, strings.Index(text, got))
		}
	}
}

func TestFindWholePhraseDoesNotMatchInsideOtherWords(t *testing.T) {
	for _, item := range []struct{ text, phrase string }{
		{"Магия и Магистр играют", "Маг"},
		{"Воинственный дух", "Воин"},
		{"Полякова", "Поля"},
		{"Маг_1 и Маг2", "Маг"},
		{"Темные дарыа", "Темные дары"},
	} {
		if spans := FindWholePhrase(item.text, item.phrase); len(spans) != 0 {
			t.Fatalf("%q must not contain %q as a whole phrase: %v", item.text, item.phrase, spansText(item.text, spans))
		}
	}
}

func TestFindWholePhraseIsCaseInsensitiveAndCountsRepeats(t *testing.T) {
	text := "маг, МАГ и Маг: три мага и один маг."
	spans := FindWholePhrase(text, "Маг")
	if got := spansText(text, spans); strings.Join(got, ",") != "маг,МАГ,Маг,маг" {
		t.Fatalf("repeats: %v", got)
	}
}

func TestFindWholePhraseHandlesPunctuationDashesQuotesAndMarkdown(t *testing.T) {
	text := "Маг—Воин; (Жрец) [Шаман] `Друид` «Паладин» \"Разбойник\" *Чернокнижник* Охотник."
	for _, phrase := range []string{"Маг", "Воин", "Жрец", "Шаман", "Друид", "Паладин", "Разбойник", "Чернокнижник", "Охотник"} {
		if len(FindWholePhrase(text, phrase)) != 1 {
			t.Fatalf("%s not found next to punctuation", phrase)
		}
	}
	if len(FindWholePhrase("Охотник на демонов и Охотник", "Охотник на демонов")) != 1 {
		t.Fatal("multi-word phrase with lowercase words")
	}
}

func TestExtractProtectsCyrillicGameEntities(t *testing.T) {
	text := "Рыцарь смерти играет против Мага, а Поля сражений ждут."
	kinds := map[string]int{}
	for _, entity := range Extract(text) {
		kinds[entity.Kind+":"+entity.Value]++
	}
	if kinds["game_entity:Рыцарь смерти"] != 1 || kinds["game_entity:Поля сражений"] != 1 {
		t.Fatalf("game entities: %v", kinds)
	}
	if kinds["game_entity:Маг"] != 0 {
		t.Fatalf("«Мага» is an inflected form and must not match «Маг» as a whole word: %v", kinds)
	}
	if kinds["named_entity:Рыцарь смерти"] != 0 {
		// two capitalised words are required; «Рыцарь смерти» has one
		t.Fatalf("named_entity false positive: %v", kinds)
	}
	named := Extract("Замок Нафрия и Гафф Вольное Сердце. Кел'Тузад Маг.")
	values := []string{}
	for _, entity := range named {
		if entity.Kind == "named_entity" {
			values = append(values, entity.Value)
		}
	}
	if strings.Join(values, "|") != "Замок Нафрия|Гафф Вольное Сердце|Кел'Тузад Маг" {
		t.Fatalf("named entities: %v", values)
	}
}

func TestCompareBlocksRemovedOrChangedRussianNames(t *testing.T) {
	source := "Рыцарь смерти держит Огненный шар и Темные дары до шестого хода."
	for name, damaged := range map[string]string{
		"removed class":    "Держит Огненный шар и Темные дары до шестого хода.",
		"renamed gift":     "Рыцарь смерти держит Огненный шар и подарки до шестого хода.",
		"dropped repeat":   "Рыцарь смерти держит Огненный шар и Темные дары; Темные дары кончились.",
		"changed spelling": "Рыцарь Смерти держит Огненный шар и Темные дары до шестого хода.",
	} {
		report := Compare(source, damaged)
		if name == "dropped repeat" {
			if report.HasHardChanges() {
				t.Fatalf("%s: adding a repeat is not a hard change: %+v", name, report)
			}
			continue
		}
		if !report.HasHardChanges() {
			t.Fatalf("%s must be a hard change: %+v", name, report)
		}
	}
	if report := Compare(source+" Темные дары. Темные дары.", source+" Темные дары."); !report.HasHardChanges() {
		t.Fatalf("dropped repetition must be a hard change: %+v", report)
	}
	if report := Compare("Карта за 3 маны и Темные дары.", "Карта за 4 маны и Темные дары."); !report.HasHardChanges() {
		t.Fatalf("changed number must still be hard: %+v", report)
	}
}
