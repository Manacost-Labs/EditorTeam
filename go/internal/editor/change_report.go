package editor

import (
	"strings"
	"unicode/utf8"

	"github.com/Manacost-Labs/EditorTeam/go/internal/analyzer"
)

const (
	maxReportedChanges = 20
	maxLinePreview     = 180
	minMovedWords      = 4 // короче — совпадение случайное, а не перестановка
)

// summarizeChanges — построчная сводка правки для AG-UI: changed / added /
// removed / moved. Строки сопоставляются по наибольшей общей
// подпоследовательности, поэтому переставленный абзац не выглядит как
// «удалён здесь и написан заново там»: он показывается один раз как moved.
func summarizeChanges(before, after string) []Change {
	if before == after {
		return []Change{}
	}
	oldLines := diffLines(before)
	newLines := diffLines(after)

	ops := lineOps(oldLines, newLines)

	// перестановки: удалённая строка, дословно появившаяся среди добавленных
	added := map[string][]int{}
	for _, op := range ops {
		if op.kind == "added" && movable(op.text) {
			added[op.text] = append(added[op.text], op.newIndex)
		}
	}
	moved := map[int]bool{} // индексы добавленных строк, которые на самом деле переставлены
	movedFrom := map[int]int{}
	for _, op := range ops {
		if op.kind != "removed" || !movable(op.text) {
			continue
		}
		if idx := added[op.text]; len(idx) > 0 {
			moved[idx[0]] = true
			movedFrom[idx[0]] = op.oldIndex
			added[op.text] = idx[1:]
		}
	}

	changes := make([]Change, 0)
	pendingRemoved := []lineOp{}
	flushRemoved := func() {
		for _, op := range pendingRemoved {
			changes = appendChange(changes, Change{
				Kind: "removed", Line: op.oldIndex + 1, Before: previewLine(op.text),
			})
		}
		pendingRemoved = pendingRemoved[:0]
	}
	for _, op := range ops {
		switch op.kind {
		case "equal":
			flushRemoved()
		case "removed":
			if _, isMoved := movedTarget(op, movedFrom); isMoved {
				continue // покажем один раз на новом месте
			}
			pendingRemoved = append(pendingRemoved, op)
		case "added":
			if moved[op.newIndex] {
				flushRemoved()
				changes = appendChange(changes, Change{
					Kind: "moved", Line: op.newIndex + 1, After: previewLine(op.text),
				})
				continue
			}
			// удалённая строка перед добавленной — это замена, а не два события
			if len(pendingRemoved) > 0 {
				rm := pendingRemoved[0]
				pendingRemoved = pendingRemoved[1:]
				changes = appendChange(changes, Change{
					Kind: "changed", Line: op.newIndex + 1,
					Before: previewLine(rm.text), After: previewLine(op.text),
				})
				continue
			}
			changes = appendChange(changes, Change{
				Kind: "added", Line: op.newIndex + 1, After: previewLine(op.text),
			})
		}
	}
	flushRemoved()
	return changes
}

type lineOp struct {
	kind     string // equal | removed | added
	text     string
	oldIndex int
	newIndex int
}

func movedTarget(op lineOp, movedFrom map[int]int) (int, bool) {
	for newIndex, oldIndex := range movedFrom {
		if oldIndex == op.oldIndex {
			return newIndex, true
		}
	}
	return 0, false
}

func movable(text string) bool {
	return len(strings.Fields(text)) >= minMovedWords
}

// lineOps — правки между двумя списками строк по LCS. Тексты статей —
// сотни строк, квадратичная таблица здесь дешевле любых эвристик.
func lineOps(oldLines, newLines []string) []lineOp {
	n, m := len(oldLines), len(newLines)
	lcs := make([][]int, n+1)
	for i := range lcs {
		lcs[i] = make([]int, m+1)
	}
	for i := n - 1; i >= 0; i-- {
		for j := m - 1; j >= 0; j-- {
			if oldLines[i] == newLines[j] {
				lcs[i][j] = lcs[i+1][j+1] + 1
			} else if lcs[i+1][j] >= lcs[i][j+1] {
				lcs[i][j] = lcs[i+1][j]
			} else {
				lcs[i][j] = lcs[i][j+1]
			}
		}
	}
	ops := make([]lineOp, 0, n+m)
	i, j := 0, 0
	for i < n && j < m {
		switch {
		case oldLines[i] == newLines[j]:
			ops = append(ops, lineOp{kind: "equal", text: oldLines[i], oldIndex: i, newIndex: j})
			i++
			j++
		case lcs[i+1][j] >= lcs[i][j+1]:
			ops = append(ops, lineOp{kind: "removed", text: oldLines[i], oldIndex: i, newIndex: j})
			i++
		default:
			ops = append(ops, lineOp{kind: "added", text: newLines[j], oldIndex: i, newIndex: j})
			j++
		}
	}
	for ; i < n; i++ {
		ops = append(ops, lineOp{kind: "removed", text: oldLines[i], oldIndex: i, newIndex: j})
	}
	for ; j < m; j++ {
		ops = append(ops, lineOp{kind: "added", text: newLines[j], oldIndex: i, newIndex: j})
	}
	return ops
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
