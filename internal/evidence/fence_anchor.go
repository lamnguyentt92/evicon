package evidence

// Release 086 (verifier finding B2-02): the anchor of a complete fence computed from evidence.
//
// Releases 061-085 replaced the authority's anchor by the owner's reported state after a complete fence
// (no UNCERTAIN entry): sound only under a premise the paper never stated (the owner's aggregate state is
// true). FenceAnchorStates computes the anchor from evidence instead: starting from the previous anchor,
// every identity of the batch (the admitted commands above the previous cutoff and at or below the fence's
// cutoff) whose knowledge after the fence's intake is APPLIED is applied with the domain's semantics (ADD
// and SET, every dimension); every other identity is STOPPED and contributes nothing. It is the exact
// terminal reducer of the belief (BuildBeliefWithStats on a fully terminal history), so for an ADD-only
// batch it is the previous anchor plus the sum of the APPLIED vectors (sequence order and any other order
// give the same state), and with SETs the largest-sequence APPLIED SET is the last SET (the SET frontier)
// while an APPLIED ADD may have taken effect before or after it: the evidence then allows one state per
// such placement, and every one is returned. The true owner state is one of the returned states whenever
// premises FC (the answer lists every identity at or below the cutoff that has taken effect or may have)
// and DR (terminal receipts are durable and true) hold.

import (
	"fmt"
	"sort"

	"evicon/internal/domain"
	"evicon/internal/journal"
)

// FenceAnchorStates returns the distinct states the evidence allows for the owner after a complete fence:
// d is the domain before the fence (its anchor and cutoff), batch the commands above d.CutoffSequence with
// their terminal status after the intake (APPLIED or STOPPED; any other status is an error). The states are
// sorted by their key; ErrModelInconsistency when no state is consistent (e.g. an APPLIED command of
// another generation), ErrBeliefBudget when more than limit states are possible.
func FenceAnchorStates(d journal.Domain, batch []journal.CommandRecord, limit int) ([]domain.State, error) {
	for _, c := range batch {
		if c.Sequence <= d.CutoffSequence {
			return nil, fmt.Errorf("fence anchor: %s (sequence %d) is covered by the previous anchor (cutoff %d)", c.CommandID, c.Sequence, d.CutoffSequence)
		}
		if c.Status != "APPLIED" && c.Status != "STOPPED" {
			return nil, fmt.Errorf("fence anchor: %s has no terminal knowledge (%q)", c.CommandID, c.Status)
		}
	}
	if len(batch) == 0 {
		return []domain.State{domain.State(append([]int64(nil), d.Anchor...))}, nil
	}
	b, _, err := BuildBeliefWithStats(d, batch, limit)
	if err != nil {
		return nil, err
	}
	seen := map[string]domain.State{}
	for _, cfg := range b.Configurations {
		seen[stateKey(cfg.State)] = cfg.State.Clone()
	}
	keys := make([]string, 0, len(seen))
	for k := range seen {
		keys = append(keys, k)
	}
	sort.Strings(keys)
	out := make([]domain.State, 0, len(keys))
	for _, k := range keys {
		out = append(out, seen[k])
	}
	if len(out) == 0 {
		return nil, ErrModelInconsistency
	}
	return out, nil
}
