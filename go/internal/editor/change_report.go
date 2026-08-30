package editor

import (
	"strings"
	"unicode/utf8"

	"github.com/Manacost-Labs/EditorTeam/go/internal/analyzer"
)

const (
	maxReportedChanges = 20
	maxLinePreview     = 180
)

func summarizeChanges(before, after string) []Change {
	if before == after {
		return []Change{}
	}

	oldLines := diffLines(before)
	newLines := diffLines(after)
	if len(oldLines) == len(newLines) {
		changes := make([]Change, 0)
		for i := range oldLines {
			if oldLines[i] == newLines[i] {
				continue
			}
			changes = appendChange(changes, Change{
				Kind:   "changed",
				Line:   i + 1,
				Before: previewLine(oldLines[i]),
				After:  previewLine(newLines[i]),
			})
		}
		return changes
	}

	start := 0
	for start < len(oldLines) && start < len(newLines) && oldLines[start] == newLines[start] {
		start++
	}
	oldEnd, newEnd := len(oldLines), len(newLines)
	for oldEnd > start && newEnd > start && oldLines[oldEnd-1] == newLines[newEnd-1] {
		oldEnd--
		newEnd--
	}

	changes := make([]Change, 0, oldEnd-start+newEnd-start)
	for i := 0; i < oldEnd-start || i < newEnd-start; i++ {
		oldIndex, newIndex := start+i, start+i
		switch {
		case oldIndex < oldEnd && newIndex < newEnd:
			changes = appendChange(changes, Change{
				Kind:   "changed",
				Line:   newIndex + 1,
				Before: previewLine(oldLines[oldIndex]),
				After:  previewLine(newLines[newIndex]),
			})
		case oldIndex < oldEnd:
			changes = appendChange(changes, Change{
				Kind:   "removed",
				Line:   oldIndex + 1,
				Before: previewLine(oldLines[oldIndex]),
			})
		default:
			changes = appendChange(changes, Change{
				Kind:  "added",
				Line:  newIndex + 1,
				After: previewLine(newLines[newIndex]),
			})
		}
	}
	return changes
}

func diffLines(text string) []string {
	if text == "" {
		return []string{}
	}
	return strings.Split(strings.TrimSuffix(text, "\n"), "\n")
}

func appendChange(changes []Change, change Change) []Change {
	if len(changes) < maxReportedChanges {
		return append(changes, change)
	}
	if len(changes) == maxReportedChanges {
		return append(changes, Change{Kind: "omitted"})
	}
	return changes
}

func previewLine(line string) string {
	line = strings.ReplaceAll(line, "\t", "⇥")
	if utf8.RuneCountInString(line) <= maxLinePreview {
		return line
	}
	runes := []rune(line)
	return string(runes[:maxLinePreview-1]) + "…"
}

func preservedSummary(rules *analyzer.Rules) []string {
	preserved := []string{
		"факты, числа и позиция автора — правка принята редакторской проверкой",
	}
	if len(rules.Protected) > 0 {
		preserved = append(preserved, "защищённые элементы: "+strings.Join(rules.Protected, ", "))
	}
	if len(rules.Keep) > 0 {
		preserved = append(preserved, "авторские слова: "+strings.Join(rules.Keep, ", "))
	}
	return preserved
}
