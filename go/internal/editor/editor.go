// Package editor — цикл правки с затвором.
//
// Модель правит текст, анализаторы проверяют результат. Правка принимается,
// только если не вычистила живое, не потеряла защищённые элементы, не
// выровняла ритм и не усушила текст. Не прошла — модель получает конкретную
// причину и пробует снова.
//
// Это и есть смысл сервиса. Без затвора он был бы прокси к провайдеру.
package editor

import (
	"context"
	"fmt"
	"strings"

	"github.com/Manacost-Labs/EditorTeam/go/internal/analyzer"
	"github.com/Manacost-Labs/EditorTeam/go/internal/llm"
)

type Request struct {
	Text    string `json:"text"`
	Game    string `json:"game"`
	Profile string `json:"profile"`
	Mode    string `json:"mode"` // лёгкая | обычная | глубокая
}

type Attempt struct {
	N          int                  `json:"n"`
	Accepted   bool                 `json:"accepted"`
	Violations []analyzer.Violation `json:"violations,omitempty"`
}

type Change struct {
	Kind   string `json:"kind"` // changed | added | removed
	Line   int    `json:"line"`
	Before string `json:"before,omitempty"`
	After  string `json:"after,omitempty"`
}

type Result struct {
	Text       string            `json:"text"`
	Accepted   bool              `json:"accepted"`
	Attempts   []Attempt         `json:"attempts"`
	Changes    []Change          `json:"changes"`
	Preserved  []string          `json:"preserved"`
	Saved      bool              `json:"saved"`
	SaveStatus string            `json:"save_status"`
	Verdict    *analyzer.Verdict `json:"verdict"`
	Report     *analyzer.Report  `json:"report"`
	Model      string            `json:"model"`
	Caveats    []string          `json:"caveats,omitempty"`
}

type Service struct {
	llm         llm.Completer
	an          *analyzer.Client
	maxAttempts int
}

func New(l llm.Completer, a *analyzer.Client, maxAttempts int) *Service {
	if maxAttempts < 1 {
		maxAttempts = 1
	}
	return &Service{llm: l, an: a, maxAttempts: maxAttempts}
}

// Edit прогоняет текст через цикл «правка → проверка → повтор».
func (s *Service) Edit(ctx context.Context, req Request) (*Result, error) {
	rules, err := s.an.Rules(ctx, req.Game, req.Profile)
	if err != nil {
		return nil, fmt.Errorf("правила: %w", err)
	}

	res := &Result{
		Model:     s.llm.Model(),
		Changes:   []Change{},
		Preserved: []string{},
	}
	if prov, ok := rules.Norms["provisional"].(bool); ok && prov {
		res.Caveats = append(res.Caveats,
			"нормы для этой игры заимствованы: оценки голоса и ритма — ориентир, не эталон")
	}

	system := buildSystemPrompt(rules, req.Mode)
	messages := []llm.Message{
		{Role: "system", Content: system},
		{Role: "user", Content: req.Text},
	}

	best := req.Text
	for attempt := 1; attempt <= s.maxAttempts; attempt++ {
		out, err := s.llm.Complete(ctx, messages, 0)
		if err != nil {
			return nil, fmt.Errorf("попытка %d: %w", attempt, err)
		}
		out = stripFence(out)

		verdict, err := s.an.Validate(ctx, req.Text, out, req.Game, req.Profile)
		if err != nil {
			return nil, fmt.Errorf("проверка попытки %d: %w", attempt, err)
		}

		res.Attempts = append(res.Attempts, Attempt{
			N: attempt, Accepted: verdict.Accepted, Violations: verdict.Violations,
		})

		if verdict.Accepted {
			res.Text, res.Accepted, res.Verdict = out, true, verdict
			break
		}

		best = out
		if attempt == s.maxAttempts {
			// Отдаём исходник, а не последнюю неудачную правку: испорченный
			// текст хуже неправленого, и решать должен человек.
			res.Text, res.Accepted, res.Verdict = req.Text, false, verdict
			res.Caveats = append(res.Caveats,
				"правка не прошла проверку за "+plural(s.maxAttempts)+
					" — возвращён исходный текст")
			break
		}

		messages = append(messages,
			llm.Message{Role: "assistant", Content: out},
			llm.Message{Role: "user", Content: retryPrompt(verdict.Violations)},
		)
	}
	_ = best

	report, err := s.an.Analyze(ctx, res.Text, req.Game, req.Profile)
	if err == nil {
		res.Report = report
	}
	res.Changes = summarizeChanges(req.Text, res.Text)
	if res.Accepted {
		res.Preserved = preservedSummary(rules)
		res.SaveStatus = "результат возвращён в чат; исходный текст не перезаписывался"
	} else {
		res.Preserved = []string{"исходный текст возвращён без изменений"}
		res.SaveStatus = "правка не сохранена: проверка не пройдена, возвращён исходный текст"
	}
	return res, nil
}

func buildSystemPrompt(r *analyzer.Rules, mode string) string {
	var b strings.Builder
	b.WriteString("Ты редактируешь русскоязычный текст по игре ")
	b.WriteString(r.Game)
	b.WriteString(". Верни ТОЛЬКО отредактированный текст, без пояснений и без разметки кода.\n\n")

	b.WriteString("ГЛАВНОЕ ПРАВИЛО\n")
	b.WriteString("Правка — не улучшение по умолчанию. Меняй только то, что нужно менять:\n")
	b.WriteString("ошибки, согласование, двусмысленность, слышимый повтор, тяжёлую конструкцию,\n")
	b.WriteString("слова из списка замен, абзац-полотно, сломанную логическую связку.\n")
	b.WriteString("Всё остальное оставь как есть — в том числе то, что написал бы иначе.\n\n")

	switch mode {
	case "лёгкая":
		b.WriteString("РЕЖИМ: лёгкая. Только ошибки. Ни одного стилистического изменения.\n\n")
	case "глубокая":
		b.WriteString("РЕЖИМ: глубокая. Можно менять порядок абзацев и заголовки.\n\n")
	default:
		b.WriteString("РЕЖИМ: обычная. Ошибки, тяжёлые конструкции, словарь, абзацы.\n\n")
	}

	b.WriteString("НЕ ТРОГАЙ НИКОГДА\n")
	b.WriteString("- названия из игры, числа, проценты, характеристики, коды\n")
	b.WriteString("- факты, выводы и позицию автора\n")
	b.WriteString("- разговорные вставки, шутки, обращения к читателю, скобки\n")
	b.WriteString("- «Но» и «Однако» в начале предложения — не склеивай с предыдущим\n")
	b.WriteString("- императив читателю («оставляйте», «держите») — не обезличивай\n")
	b.WriteString("- «хотя» и «зато» — это авторская оговорка, а не лишнее слово\n")
	b.WriteString("- неровный ритм: короткая фраза рядом с длинной остаётся как есть\n")
	if len(r.Protected) > 0 {
		b.WriteString("- слова: " + strings.Join(r.Protected, ", ") + "\n")
	}
	b.WriteString("\n")

	if len(r.Replace) > 0 {
		b.WriteString("ЗАМЕНЯЙ\n")
		for _, p := range r.Replace {
			b.WriteString("- " + p["from"] + " → " + p["to"] + "\n")
		}
		b.WriteString("\n")
	}
	if len(r.Keep) > 0 {
		b.WriteString("НЕ ЗАМЕНЯЙ, это авторские слова: ")
		b.WriteString(strings.Join(r.Keep, ", ") + "\n\n")
	}

	b.WriteString("ОФОРМЛЕНИЕ\n")
	if yo, ok := r.Typography["yo"].(map[string]any); ok {
		if yo["decision"] == "remove" {
			b.WriteString("- букву ё не ставить: «ее», «еще», «все же». Исключение — официальные названия\n")
		}
	}
	if q, ok := r.Typography["quotes"].(map[string]any); ok {
		if q["decision"] == "straight" {
			b.WriteString("- кавычки прямые\n")
		}
	}
	b.WriteString("- названия в кавычки не берутся\n")
	b.WriteString("- архетипы и билды без дефиса, оба слова с заглавной\n\n")

	b.WriteString("ЗАПРЕЩЕНО ДОБАВЛЯТЬ\n")
	b.WriteString("«стоит отметить», «важно понимать», «давайте разберёмся», «подведём итог»,\n")
	b.WriteString("конструкцию «не просто X, а Y», новые факты, числа и выводы.\n")
	b.WriteString("Текст не должен стать короче больше чем на 5%.\n")
	return b.String()
}

func retryPrompt(vs []analyzer.Violation) string {
	var b strings.Builder
	b.WriteString("Правка не принята. Проверка нашла:\n\n")
	for _, v := range vs {
		b.WriteString("- " + v.Message + "\n")
	}
	b.WriteString("\nВерни текст заново. Сохрани всё, что перечислено выше: ")
	b.WriteString("верни на место живые обороты и защищённые элементы, ")
	b.WriteString("не выравнивай длину предложений. ")
	b.WriteString("Исправь только настоящие ошибки. Только текст, без пояснений.")
	return b.String()
}

// plural склоняет «попытка» — сообщение читает человек.
func plural(n int) string {
	word := "попыток"
	switch {
	case n%10 == 1 && n%100 != 11:
		word = "попытку"
	case n%10 >= 2 && n%10 <= 4 && (n%100 < 12 || n%100 > 14):
		word = "попытки"
	}
	return fmt.Sprintf("%d %s", n, word)
}

// stripFence снимает обрамление ```…```, если модель его добавила.
func stripFence(s string) string {
	s = strings.TrimSpace(s)
	if !strings.HasPrefix(s, "```") {
		return s
	}
	if i := strings.Index(s, "\n"); i >= 0 {
		s = s[i+1:]
	}
	if i := strings.LastIndex(s, "```"); i >= 0 {
		s = s[:i]
	}
	return strings.TrimSpace(s)
}
