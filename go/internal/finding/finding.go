// Package finding defines the common finding shape used by every checker.
package finding

type Finding struct {
	Analyzer    string   `json:"analyzer"`
	RuleID      string   `json:"rule_id,omitempty"`
	Severity    string   `json:"severity"`
	Message     string   `json:"message"`
	Suggestions []string `json:"suggestions,omitempty"`
	Line        int      `json:"line,omitempty"`
	Column      int      `json:"column,omitempty"`
	Offset      int      `json:"offset,omitempty"`
	Length      int      `json:"length,omitempty"`
	Evidence    string   `json:"evidence,omitempty"`
	Confidence  float64  `json:"confidence,omitempty"`
	Tags        []string `json:"tags,omitempty"`
	Context     string   `json:"context,omitempty"`
	Field       string   `json:"field,omitempty"`
}
