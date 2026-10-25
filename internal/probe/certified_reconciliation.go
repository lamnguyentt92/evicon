package probe

// EviCon 059: experimental, read-only certified continuation mechanism.
// These are HYPOTHETICAL sufficient outcome sets; they are never receipts,
// reservations, admission decisions, or barrier invocations.
// Runtime admission continues exclusively through validated evidence and guard.
import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"sort"
	"time"

	"evicon/internal/journal"
	"evicon/internal/protocol"
)

// TerminalAssumption refers to an outcome that MAY become verified later.
// No planner may write this status to a journal or use it to admit a request.
type TerminalAssumption struct {
	CommandID string `json:"command_id"`
	Outcome   string `json:"outcome"`
}

type ContinuationCertificate struct {
	SnapshotSHA256      string               `json:"snapshot_sha256"`
	DomainID            string               `json:"domain_id"`
	Generation          uint64               `json:"generation"`
	Revision            uint64               `json:"revision"`
	CandidateID         string               `json:"candidate_id"`
	Required            []TerminalAssumption `json:"required_outcomes"`
	AssumptionsOnly     bool                 `json:"assumptions_only"`
	GuardSafeIfVerified bool                 `json:"guard_safe_if_verified"`
}

// Fingerprint includes the revision, candidate, owner contract and all tracked
// command records. Sorting makes semantically identical snapshots stable.
func certifiedSnapshotSHA(d journal.Domain, commands []journal.CommandRecord, req protocol.CommandRequest) (string, error) {
	cs := cloneCommands(commands)
	sort.Slice(cs, func(i, j int) bool { return cs[i].CommandID < cs[j].CommandID })
	raw, err := json.Marshal(struct {
		Domain    journal.Domain          `json:"domain"`
		Commands  []journal.CommandRecord `json:"commands"`
		Candidate protocol.CommandRequest `json:"candidate"`
	}{d, cs, req})
	if err != nil {
		return "", err
	}
	h := sha256.Sum256(raw)
	return hex.EncodeToString(h[:]), nil
}

func inspectAssumptions(d journal.Domain, cmds []journal.CommandRecord, req protocol.CommandRequest, assumptions []TerminalAssumption, cfgLimit int) (bool, error) {
	if cfgLimit < 1 {
		return false, fmt.Errorf("invalid configuration cap")
	}
	cloned := cloneCommands(cmds)
	byID := make(map[string]int, len(cloned))
	for i, c := range cloned {
		if c.CommandID == "" {
			return false, fmt.Errorf("empty command identity")
		}
		if _, ok := byID[c.CommandID]; ok {
			return false, fmt.Errorf("duplicate command identity")
		}
		byID[c.CommandID] = i
	}
	seen := map[string]bool{}
	for _, x := range assumptions {
		idx, ok := byID[x.CommandID]
		if !ok || seen[x.CommandID] {
			return false, fmt.Errorf("untracked or repeated assumption")
		}
		if x.Outcome != "STOPPED" && x.Outcome != "APPLIED" {
			return false, fmt.Errorf("nonterminal assumption")
		}
		if cloned[idx].Status == "STOPPED" || cloned[idx].Status == "APPLIED" {
			return false, fmt.Errorf("assumption for terminal command")
		}
		if cloned[idx].Generation != d.Generation {
			return false, fmt.Errorf("generation mismatch")
		}
		seen[x.CommandID] = true
		cloned[idx].Status = x.Outcome
	}
	return projectedSafe(d, cloned, req, cfgLimit)
}

// VerifyContinuationCertificate RECHECKS the current snapshot/guard and
// cannot return an actual ADMIT. It only verifies conditional sufficiency.
func VerifyContinuationCertificate(d journal.Domain, cs []journal.CommandRecord, req protocol.CommandRequest, c ContinuationCertificate, cfgLimit int) (bool, error) {
	id, err := certifiedSnapshotSHA(d, cs, req)
	if err != nil {
		return false, err
	}
	if c.SnapshotSHA256 != id || c.DomainID != d.ID || c.Generation != d.Generation || c.Revision != d.Revision || c.CandidateID != req.CommandID || !c.AssumptionsOnly || !c.GuardSafeIfVerified {
		return false, nil
	}
	if len(c.Required) == 0 {
		return false, nil
	}
	for i := 1; i < len(c.Required); i++ {
		if c.Required[i-1].CommandID >= c.Required[i].CommandID {
			return false, nil
		}
	}
	return inspectAssumptions(d, cs, req, c.Required, cfgLimit)
}

// EnumerateContinuationCertificates explores bounded, hypothetical terminal
// outcome sets and returns subset-minimal sufficient sets. Search exhaustion
// is an error, not a silent/truncated claim of completeness.
func EnumerateContinuationCertificates(d journal.Domain, cs []journal.CommandRecord, req protocol.CommandRequest, depth, cfgLimit, maxStates int, budget time.Duration) ([]ContinuationCertificate, error) {
	if depth < 1 || depth > 4 || cfgLimit < 1 || maxStates < 1 || budget <= 0 {
		return nil, fmt.Errorf("invalid certificate search bounds")
	}
	start := time.Now()
	id, err := certifiedSnapshotSHA(d, cs, req)
	if err != nil {
		return nil, err
	}
	cmds := Unresolved(cs, nil)
	if len(cmds) < depth {
		depth = len(cmds)
	}
	results := []ContinuationCertificate{}
	explored := 0
	// Enumerate sizes in ascending order, allowing minimality filtering.
	for n := 1; n <= depth; n++ {
		var fixed func(int, []TerminalAssumption, int) error
		fixed = func(pos int, assumptions []TerminalAssumption, needed int) error {
			if time.Since(start) >= budget {
				return ErrPlannerBudget
			}
			if needed == 0 {
				explored++
				if explored > maxStates {
					return fmt.Errorf("certificate state cap exceeded")
				}
				for _, prior := range results {
					if len(prior.Required) >= len(assumptions) {
						continue
					}
					have := map[string]string{}
					for _, a := range assumptions {
						have[a.CommandID] = a.Outcome
					}
					match := true
					for _, p := range prior.Required {
						if have[p.CommandID] != p.Outcome {
							match = false
							break
						}
					}
					if match {
						return nil
					}
				}
				safe, err := inspectAssumptions(d, cs, req, assumptions, cfgLimit)
				if err != nil {
					return err
				}
				if safe {
					results = append(results, ContinuationCertificate{id, d.ID, d.Generation, d.Revision, req.CommandID, append([]TerminalAssumption(nil), assumptions...), true, true})
				}
				return nil
			}
			if pos >= len(cmds) || len(cmds)-pos < needed {
				return nil
			}
			if err := fixed(pos+1, assumptions, needed); err != nil {
				return err
			}
			for _, outcome := range []string{"APPLIED", "STOPPED"} {
				cp := append(append([]TerminalAssumption(nil), assumptions...), TerminalAssumption{cmds[pos].CommandID, outcome})
				if err := fixed(pos+1, cp, needed-1); err != nil {
					return err
				}
			}
			return nil
		}
		if err := fixed(0, nil, n); err != nil {
			return nil, err
		}
	}
	sort.Slice(results, func(i, j int) bool {
		if len(results[i].Required) != len(results[j].Required) {
			return len(results[i].Required) < len(results[j].Required)
		}
		ai, _ := json.Marshal(results[i].Required)
		aj, _ := json.Marshal(results[j].Required)
		return string(ai) < string(aj)
	})
	return results, nil
}

// ReconciliationAction is only a predicted information-acquisition action.
// Barrier is selectable solely with an explicitly supplied verified *terminal
// per-command* owner capability. It NEVER means 'every command STOPPED'.
type ReconciliationCapability struct {
	VerifiedTerminalBarrier  bool  `json:"verified_terminal_barrier"`
	BarrierExpectedCostUS    int64 `json:"barrier_expected_cost_us"`
	BarrierLostWorkPenaltyUS int64 `json:"barrier_lost_work_penalty_us"`
}
type ReconciliationAction struct {
	Kind                   string                  `json:"kind"` // QUERY, BARRIER or DEFER
	CommandID              string                  `json:"command_id,omitempty"`
	ConditionalCertificate ContinuationCertificate `json:"conditional_certificate"`
	ProxyCostUS            int64                   `json:"proxy_cost_us"`
	PredictionOnly         bool                    `json:"prediction_only"`
}

// SuggestReconciliation deliberately uses a transparent deterministic cost
// proxy, NOT a probability of fulfillment. Baseline M4 must be granted exactly
// the same barrier capability in any future comparison.
func SuggestReconciliation(certs []ContinuationCertificate, costs CostModel, cap ReconciliationCapability) ReconciliationAction {
	best := ReconciliationAction{Kind: "DEFER", PredictionOnly: true}
	bestCost := int64(^uint64(0) >> 1)
	for _, cert := range certs {
		if !cert.AssumptionsOnly || !cert.GuardSafeIfVerified || len(cert.Required) == 0 {
			continue
		}
		total := int64(0)
		feasible := true
		for _, a := range cert.Required {
			v := costs.CostUS(a.CommandID)
			if v <= 0 || v > bestCost-total {
				feasible = false
				break
			}
			total += v
		}
		if feasible && total < bestCost {
			bestCost = total
			best = ReconciliationAction{Kind: "QUERY", CommandID: cert.Required[0].CommandID, ConditionalCertificate: cert, ProxyCostUS: total, PredictionOnly: true}
		}
		if cap.VerifiedTerminalBarrier && cap.BarrierExpectedCostUS > 0 && cap.BarrierLostWorkPenaltyUS >= 0 {
			if cap.BarrierExpectedCostUS <= bestCost && cap.BarrierLostWorkPenaltyUS <= bestCost-cap.BarrierExpectedCostUS {
				bcost := cap.BarrierExpectedCostUS + cap.BarrierLostWorkPenaltyUS
				if bcost < bestCost {
					bestCost = bcost
					best = ReconciliationAction{Kind: "BARRIER", ConditionalCertificate: cert, ProxyCostUS: bcost, PredictionOnly: true}
				}
			}
		}
	}
	return best
}
