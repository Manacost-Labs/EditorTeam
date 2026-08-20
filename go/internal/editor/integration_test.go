package editor

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"strings"
	"testing"
	"time"

	"github.com/Manacost-Labs/EditorTeam/go/internal/analyzer"
	"github.com/Manacost-Labs/EditorTeam/go/internal/llm"
)

// Сквозная проверка с настоящим Python-сайдкаром. Пропускается, если он
// не поднят: набор должен проходить и без внешних зависимостей.
//
//	python3 -m editorteam.server --port 8731
//	EDITOR_TEST_ANALYZER=http://127.0.0.1:8731 go test ./...
func sidecarOrSkip(t *testing.T) *analyzer.Client {
	t.Helper()
	url := os.Getenv("EDITOR_TEST_ANALYZER")
	if url == "" {
		t.Skip("нет EDITOR_TEST_ANALYZER — сквозная проверка пропущена")
	}
	c := analyzer.New(url, 60*time.Second)
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	if err := c.Health(ctx); err != nil {
		t.Skipf("сайдкар недоступен: %v", err)
	}
	return c
}

const liveText = "Оставляйте Гарпунную пушку во всех матч-апах. Но не спешите играть ее рано. " +
	"Хотя соблазн велик, вы потеряете темп. Раскапывайте механизмы (особенно против Мага). " +
	"Оставляйте Гарпунную пушку во всех матч-апах. Но не спешите играть ее рано. " +
	"Хотя соблазн велик, вы потеряете темп. Раскапывайте механизмы (особенно против Мага)."

// Та же длина, но голос вычищен: безличные обороты вместо советов читателю.
const flattened = "Гарпунную пушку следует оставлять во всех матч-апах данной колоды. " +
	"Играть ее рано не рекомендуется, поскольку это приводит к потере темпа в партии. " +
	"Механизмы требуется раскапывать заранее, что обеспечивает стабильность игры. " +
	"Гарпунную пушку следует оставлять во всех матч-апах данной колоды в целом. " +
	"Играть ее рано не рекомендуется, поскольку это приводит к потере темпа в партии."

func llmReturning(t *testing.T, reply string) *httptest.Server {
	t.Helper()
	return httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		b, _ := json.Marshal(reply)
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write([]byte(`{"choices":[{"message":{"content":` + string(b) + `}}]}`))
	}))
}

func TestGuardrailRejectsFlattenedEdit(t *testing.T) {
	an := sidecarOrSkip(t)
	lm := llmReturning(t, flattened)
	defer lm.Close()

	c := llm.New("openrouter", "fake", "k", "", "", 30*time.Second)
	c.SetEndpointForTest(lm.URL)

	res, err := New(c, an, 2).Edit(context.Background(),
		Request{Text: liveText, Game: "hearthstone", Profile: "constructed-guide"})
	if err != nil {
		t.Fatal(err)
	}
	if res.Accepted {
		t.Fatal("правка вычистила голос и не должна была пройти затвор")
	}
	if res.Text != liveText {
		t.Fatal("при отказе должен возвращаться исходный текст")
	}

	var lost int
	for _, v := range res.Attempts[0].Violations {
		if v.Kind == "voice_lost" {
			lost++
		}
	}
	if lost < 3 {
		t.Fatalf("ожидалась потеря нескольких живых сигналов, найдено %d: %+v",
			lost, res.Attempts[0].Violations)
	}
}

func TestGuardrailAcceptsHarmlessEdit(t *testing.T) {
	an := sidecarOrSkip(t)
	// правка одной опечатки: голос, длина и ритм на месте
	fixed := strings.Replace(liveText, "Гарпунную пушку во всех", "Гарпунную пушку во всех", 1)
	fixed = strings.Replace(fixed, "ее рано", "её рано", 1)
	lm := llmReturning(t, fixed)
	defer lm.Close()

	c := llm.New("openrouter", "fake", "k", "", "", 30*time.Second)
	c.SetEndpointForTest(lm.URL)

	res, err := New(c, an, 2).Edit(context.Background(),
		Request{Text: liveText, Game: "hearthstone", Profile: "constructed-guide"})
	if err != nil {
		t.Fatal(err)
	}
	if !res.Accepted {
		t.Fatalf("безобидная правка должна проходить: %+v", res.Attempts)
	}
}

func TestWowNormsSurfaceAsCaveat(t *testing.T) {
	an := sidecarOrSkip(t)
	lm := llmReturning(t, "Ротация\nЖмите Испепеление, потом Тень.")
	defer lm.Close()

	c := llm.New("openrouter", "fake", "k", "", "", 30*time.Second)
	c.SetEndpointForTest(lm.URL)

	res, err := New(c, an, 1).Edit(context.Background(),
		Request{Text: "Ротация\nСначала Испепеление.", Game: "wow", Profile: "wow-guide"})
	if err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(strings.Join(res.Caveats, " "), "заимствован") {
		t.Fatalf("для WoW нормы заимствованы, это должно быть сказано: %v", res.Caveats)
	}
}
