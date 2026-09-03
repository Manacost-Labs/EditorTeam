package rules

import (
	"github.com/Manacost-Labs/EditorTeam/go/internal/analyzer"
	"strings"
	"testing"
)

func TestBuildIsCompactAndKeepsClaimContract(t *testing.T) {
	r := &analyzer.Rules{Game: "hearthstone", Profile: "analytics-article", Keep: []string{"винрейт"}, Replace: []map[string]string{{"from": "дека", "to": "колода"}}}
	b := Build(r, "GUIDE", "обычная", "ru-RU", []map[string]any{{"claim_id": "c1", "meaning": "fact", "source": "secret"}})
	if b.Task == "" || len(b.SourceClaims) != 1 || len(b.ProtectedEntities) == 0 {
		t.Fatalf("неполный bundle: %+v", b)
	}
	prompt := b.Prompt()
	if strings.Contains(prompt, "secret") {
		t.Fatalf("backstage-поле попало в prompt: %s", prompt)
	}
	if !strings.Contains(prompt, "винрейт") || !strings.Contains(prompt, "дека → колода") {
		t.Fatalf("термины потеряны: %s", prompt)
	}
}
