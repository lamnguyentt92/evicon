package probe

// Bounded certificate enumeration is a research diagnostic, NOT an admission
// mechanism. All outcomes in a certificate are counterfactual until the
// runtime receives matching validated terminal receipts and rechecks safety.

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"sort"
	"time"

	"evicon/internal/journal"
	"evicon/internal/protocol"
)

type WitnessAssumption struct {
	CommandID string `json:"command_id"`
	Outcome   string `json:"hypothetical_outcome"`
}

type WitnessCertificate struct {
	CandidateID     string              `json:"candidate_id"`
	Revision        uint64              `json:"revision"`
	Assumptions     []WitnessAssumption `json:"assumptions"`
	Fingerprint     string              `json:"snapshot_certificate_sha256"`
	SafetyRechecked bool                `json:"safety_rechecked"`
}

func witnessHash(d journal.Domain, cs []journal.CommandRecord, req protocol.CommandRequest, assumptions []WitnessAssumption) string {
	ordered := cloneCommands(cs)
	sort.Slice(ordered, func(i, j int) bool { return ordered[i].CommandID < ordered[j].CommandID })
	raw, _ := json.Marshal(struct {
		Domain     journal.Domain          `json:"domain"`
		Commands   []journal.CommandRecord `json:"commands"`
		Candidate  protocol.CommandRequest `json:"candidate"`
		Hypotheses []WitnessAssumption     `json:"hypotheses"`
	}{d, ordered, req, assumptions})
	h := sha256.Sum256(raw)
	return hex.EncodeToString(h[:])
}

// ExtractWitnesses enumerates minimally sufficient terminal-outcome sets.
// Every emitted set is checked by the unchanged RelationalGuard. An exhausted
// analysis returns an error, never a possibly incomplete "complete" set.
func ExtractWitnesses(d journal.Domain, cs []journal.CommandRecord, req protocol.CommandRequest, maxDepth, maxCertificates, maxCfg int, budget time.Duration) ([]WitnessCertificate, error) {
	if maxDepth < 1 || maxCertificates < 1 || maxCfg < 1 || budget <= 0 {
		return nil, ErrPlannerBudget
	}
	started := time.Now()
	timeout := func() bool { return time.Since(started) >= budget }
	candidates := Unresolved(cs, nil)
	sort.Slice(candidates, func(i, j int) bool { return candidates[i].CommandID < candidates[j].CommandID })
	if maxDepth > len(candidates) {
		maxDepth = len(candidates)
	}
	out := []WitnessCertificate{}
	var walk func([]WitnessAssumption, int, int) error
	walk = func(prefix []WitnessAssumption, position, left int) error {
		if timeout() {
			return ErrPlannerBudget
		}
		if left == 0 {
			// Do not emit a non-minimal superset of an earlier witness.
			for _, existing := range out {
				if len(existing.Assumptions) >= len(prefix) {
					continue
				}
				n := 0
				for _, a := range existing.Assumptions {
					for _, b := range prefix {
						if a == b {
							n++
							break
						}
					}
				}
				if n == len(existing.Assumptions) {
					return nil
				}
			}
			next := cloneCommands(cs)
			for _, a := range prefix {
				next = assumedOutcome(next, a.CommandID, a.Outcome)
			}
			safe, err := projectedSafe(d, next, req, maxCfg)
			if err != nil {
				return err
			}
			if timeout() {
				return ErrPlannerBudget
			}
			if safe {
				if len(out) >= maxCertificates {
					return errors.New("witness certificate cap exhausted")
				}
				a := append([]WitnessAssumption(nil), prefix...)
				out = append(out, WitnessCertificate{CandidateID: req.CommandID, Revision: d.Revision,
					Assumptions: a, Fingerprint: witnessHash(d, cs, req, a), SafetyRechecked: true})
			}
			return nil
		}
		for i := position; i+left <= len(candidates); i++ {
			for _, outcome := range []string{"APPLIED", "STOPPED"} {
				extended := append(append([]WitnessAssumption(nil), prefix...), WitnessAssumption{CommandID: candidates[i].CommandID, Outcome: outcome})
				if err := walk(extended, i+1, left-1); err != nil {
					return err
				}
			}
		}
		return nil
	}
	for depth := 1; depth <= maxDepth; depth++ {
		if err := walk(nil, 0, depth); err != nil {
			return nil, err
		}
	}
	return out, nil
}
