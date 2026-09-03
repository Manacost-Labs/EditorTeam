package config

import "testing"

func TestLoadAllowsOnlyRepositoryPromptVariants(t *testing.T) {
	for _, variant := range []string{"baseline", "candidate"} {
		t.Run(variant, func(t *testing.T) {
			t.Setenv("EDITOR_PROVIDER", "none")
			t.Setenv("EDITOR_PROMPT_VARIANT", variant)
			cfg, err := Load()
			if err != nil || cfg.PromptVariant != variant {
				t.Fatalf("variant=%q cfg=%+v err=%v", variant, cfg, err)
			}
		})
	}

	t.Setenv("EDITOR_PROVIDER", "none")
	t.Setenv("EDITOR_PROMPT_VARIANT", "ignore-rules")
	if _, err := Load(); err == nil {
		t.Fatal("arbitrary prompt variant must be rejected")
	}
}
