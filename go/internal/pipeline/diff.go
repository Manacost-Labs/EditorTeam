package pipeline

import "strings"

// Токенный diff для связи improvements с реальными изменениями. Работает по
// словам нормализованного текста (пробелы схлопнуты), поэтому Unicode-safe:
// сравниваются целые токены, а не байты.

type diffToken struct {
	text       string
	start, end int // байтовые смещения в нормализованной строке
}

// hunk — одно изменение: удалённый диапазон токенов source и вставленный
// диапазон токенов candidate. Для чистой вставки delStart == delEnd, для
// чистого удаления insStart == insEnd; точка указывает, где изменение
// произошло.
type hunk struct {
	delStart, delEnd int
	insStart, insEnd int
}

func (h hunk) empty() bool { return h.delStart == h.delEnd && h.insStart == h.insEnd }

func diffTokenize(norm string) []diffToken {
	var out []diffToken
	start := -1
	for i, r := range norm {
		if r == ' ' {
			if start >= 0 {
				out = append(out, diffToken{text: norm[start:i], start: start, end: i})
				start = -1
			}
			continue
		}
		if start < 0 {
			start = i
		}
	}
	if start >= 0 {
		out = append(out, diffToken{text: norm[start:], start: start, end: len(norm)})
	}
	return out
}

// maxDiffTokens ограничивает точный diff; выше него improvements проверяются
// одним общим hunk, то есть только по наличию цитат.
const maxDiffTokens = 6000

const (
	opEqual = iota
	opDelete
	opInsert
)

type diffOp struct {
	kind   int
	a, b   int // индексы токенов в source и candidate
	length int
}

// diffHunks строит diff Майерса по токенам и группирует соседние удаления и
// вставки в hunks.
func diffHunks(a, b []string) []hunk {
	if len(a) > maxDiffTokens || len(b) > maxDiffTokens {
		return []hunk{{delStart: 0, delEnd: len(a), insStart: 0, insEnd: len(b)}}
	}
	ops := myers(a, b)
	var out []hunk
	current := hunk{}
	open := false
	posA, posB := 0, 0
	flush := func() {
		if open && !current.empty() {
			out = append(out, current)
		}
		open = false
	}
	for _, op := range ops {
		switch op.kind {
		case opEqual:
			flush()
			posA += op.length
			posB += op.length
		case opDelete:
			if !open {
				current = hunk{delStart: posA, delEnd: posA, insStart: posB, insEnd: posB}
				open = true
			}
			posA += op.length
			current.delEnd = posA
		case opInsert:
			if !open {
				current = hunk{delStart: posA, delEnd: posA, insStart: posB, insEnd: posB}
				open = true
			}
			posB += op.length
			current.insEnd = posB
		}
	}
	flush()
	return dropMoves(out, a, b)
}

// dropMoves убирает пары hunks, которые вместе образуют перенос блока:
// чистая вставка, токены которой равны токенам чистого удаления в другом
// месте. Перестановка абзацев не считается изменением, за которое можно
// зачесть улучшение.
func dropMoves(hunks []hunk, a, b []string) []hunk {
	moved := make([]bool, len(hunks))
	for i, ins := range hunks {
		if ins.delStart != ins.delEnd || ins.insStart == ins.insEnd {
			continue
		}
		for j, del := range hunks {
			if i == j || moved[j] || del.insStart != del.insEnd || del.delStart == del.delEnd {
				continue
			}
			if strings.Join(b[ins.insStart:ins.insEnd], " ") == strings.Join(a[del.delStart:del.delEnd], " ") {
				moved[i], moved[j] = true, true
				break
			}
		}
	}
	out := hunks[:0]
	for i, h := range hunks {
		if !moved[i] {
			out = append(out, h)
		}
	}
	return out
}

// myers — классический алгоритм O((N+M)D) с сохранением трасс.
func myers(a, b []string) []diffOp {
	n, m := len(a), len(b)
	max := n + m
	if max == 0 {
		return nil
	}
	offset := max
	v := make([]int, 2*max+2)
	var traces [][]int
	for d := 0; d <= max; d++ {
		snapshot := make([]int, len(v))
		copy(snapshot, v)
		traces = append(traces, snapshot)
		for k := -d; k <= d; k += 2 {
			var x int
			if k == -d || (k != d && v[offset+k-1] < v[offset+k+1]) {
				x = v[offset+k+1]
			} else {
				x = v[offset+k-1] + 1
			}
			y := x - k
			for x < n && y < m && a[x] == b[y] {
				x++
				y++
			}
			v[offset+k] = x
			if x >= n && y >= m {
				return backtrack(traces, a, b, offset)
			}
		}
	}
	return nil
}

func backtrack(traces [][]int, a, b []string, offset int) []diffOp {
	x, y := len(a), len(b)
	var reversed []diffOp
	push := func(kind, ia, ib int) {
		if len(reversed) > 0 && reversed[len(reversed)-1].kind == kind {
			reversed[len(reversed)-1].length++
			reversed[len(reversed)-1].a = ia
			reversed[len(reversed)-1].b = ib
			return
		}
		reversed = append(reversed, diffOp{kind: kind, a: ia, b: ib, length: 1})
	}
	for d := len(traces) - 1; d >= 0 && (x > 0 || y > 0); d-- {
		v := traces[d]
		k := x - y
		var prevK int
		if k == -d || (k != d && v[offset+k-1] < v[offset+k+1]) {
			prevK = k + 1
		} else {
			prevK = k - 1
		}
		prevX := v[offset+prevK]
		prevY := prevX - prevK
		for x > prevX && y > prevY {
			x--
			y--
			push(opEqual, x, y)
		}
		if d > 0 {
			if x == prevX {
				y--
				push(opInsert, x, y)
			} else {
				x--
				push(opDelete, x, y)
			}
		}
	}
	for i, j := 0, len(reversed)-1; i < j; i, j = i+1, j-1 {
		reversed[i], reversed[j] = reversed[j], reversed[i]
	}
	return reversed
}

// tokenRanges находит все вхождения фрагмента в нормализованном тексте и
// переводит их в диапазоны токенов [start, end).
func tokenRanges(norm string, tokens []diffToken, fragment string) [][2]int {
	var out [][2]int
	if fragment == "" {
		return out
	}
	from := 0
	for {
		idx := strings.Index(norm[from:], fragment)
		if idx < 0 {
			break
		}
		start := from + idx
		end := start + len(fragment)
		first, last := -1, -1
		for i, token := range tokens {
			if token.end <= start {
				continue
			}
			if token.start >= end {
				break
			}
			if first < 0 {
				first = i
			}
			last = i
		}
		if first >= 0 {
			out = append(out, [2]int{first, last + 1})
		}
		from = start + 1
	}
	return out
}

// touches: диапазон токенов относится к изменению, если пересекает
// удалённый/вставленный диапазон, а для пустого диапазона (чистая вставка
// или удаление) — проходит через точку изменения.
func touches(rangeStart, rangeEnd, hunkStart, hunkEnd int) bool {
	if hunkStart < hunkEnd {
		return rangeStart < hunkEnd && rangeEnd > hunkStart
	}
	return rangeStart <= hunkStart && rangeEnd >= hunkStart
}
