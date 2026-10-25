package analyzer

import (
	"evicon/internal/belief"
	"evicon/internal/domain"
)

// Verdicts intentionally separate a concrete/reachable safety violation from
// a conservative abstraction block. This is required by the TOIT semantics:
// BLOCK is an admission decision, not automatically a claim that a violating
// execution exists.
type Verdict string

const (
	Safe              Verdict = "SAFE"
	Counterexample    Verdict = "COUNTEREXAMPLE"
	ConservativeBlock Verdict = "CONSERVATIVE_BLOCK"
	Deferred          Verdict = "DEFER"
	NotApplicable     Verdict = "NOT_APPLICABLE"

	// Source-compatibility aliases for pre-005 tests. Their serialized values use
	// the new semantics above; no output uses the ambiguous strings UNSAFE/UNKNOWN.
	Unsafe  Verdict = Counterexample
	Unknown Verdict = Deferred
)

type Result struct {
	Verdict                     Verdict               `json:"verdict"`
	Reason                      string                `json:"reason"`
	Explored                    int                   `json:"explored,omitempty"`
	MaxOutstanding              int                   `json:"maxOutstanding,omitempty"`
	CounterexampleConfiguration *belief.Configuration `json:"counterexampleConfiguration,omitempty"`
	CounterexampleState         domain.State          `json:"counterexampleState,omitempty"`
}
