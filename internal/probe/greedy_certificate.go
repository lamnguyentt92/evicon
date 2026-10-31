package probe

// Release 083 (PREREG_extra_083.md T5): the greedy continuation certificate of additive domains
// (the paper's Section 3.6).
//
// In an ADD-only domain with non-negative values and one scalar upper bound (one component with a
// maximum, or a sum maximum), a candidate a blocked by the guard becomes safe exactly when the
// unresolved commands assumed STOPPED take away at least the deficit x0 + Res + v_a - ub from the
// worst reachable state. Assuming more commands STOPPED never makes the candidate less safe, and the
// k largest unresolved commands remove the most that any k commands can remove, so the smallest k
// for which the k largest suffice is the smallest size of any certificate. The search sorts the
// unresolved commands by size (largest first) and finds that k by bisection with the guard itself
// (inspectAssumptions: the same belief and RelationalGuard as admission), O(log n) guard evaluations
// of the product-form belief, O(n log n) in all, with no size bound and no state cap: it never gives
// up. It then drops every member whose removal still leaves the candidate safe (none, for a
// minimum-size set under the sum semantics; the pass makes inclusion-minimality hold whatever the
// guard computes).
//
// As every certificate, the result is a HYPOTHESIS that tells the resolver which commands are worth
// resolving; it is never evidence and never admits anything. Domains outside its premise (SET
// commands, negative values, several bounded components) get ErrNotAdditive, and the caller uses the
// enumeration.

import (
	"errors"
	"sort"

	"evicon/internal/domain"
	"evicon/internal/journal"
	"evicon/internal/protocol"
)

// ErrNotAdditive: the greedy certificate does not apply to this domain or candidate.
var ErrNotAdditive = errors.New("greedy certificate: not an additive domain with one scalar upper bound")

// GreedyApplies reports whether the greedy certificate's premise holds: every command (terminal or
// not) and the candidate are ADD with non-negative values, and the hard bound has exactly one upper
// constraint (a one-dimensional domain with a component maximum and no sum maximum, or a sum maximum
// and no component maximum).
func GreedyApplies(d journal.Domain, cs []journal.CommandRecord, req protocol.CommandRequest) bool {
	scalar := (d.Dimension == 1 && len(d.ComponentMax) == 1 && d.SumMax == nil) || (len(d.ComponentMax) == 0 && d.SumMax != nil)
	if !scalar || req.Operation != domain.OpAdd || !nonNegative(req.Value) {
		return false
	}
	for _, c := range cs {
		if c.Operation != domain.OpAdd || !nonNegative(c.Value) {
			return false
		}
	}
	return true
}

func nonNegative(v []int64) bool {
	for _, x := range v {
		if x < 0 {
			return false
		}
	}
	return true
}

func greedyMagnitude(c journal.CommandRecord) int64 {
	v := c.Reservation
	if len(v) == 0 {
		v = c.Value
	}
	var s int64
	for _, x := range v {
		if x < 0 {
			x = -x
		}
		if n, ok := domain.AddChecked(s, x); ok {
			s = n
		} else {
			return int64(^uint64(0) >> 1)
		}
	}
	return s
}

// GreedyContinuationCertificate returns the greedy certificate of a blocked candidate: none when the
// candidate is already safe or when no set of unresolved commands assumed STOPPED would admit it
// (Theorem 3: evidence cannot help), otherwise exactly one certificate of minimum size, minimal by
// inclusion, whose members are all assumed STOPPED.
func GreedyContinuationCertificate(d journal.Domain, cs []journal.CommandRecord, req protocol.CommandRequest, cfgLimit int) ([]ContinuationCertificate, error) {
	if !GreedyApplies(d, cs, req) {
		return nil, ErrNotAdditive
	}
	id, err := certifiedSnapshotSHA(d, cs, req)
	if err != nil {
		return nil, err
	}
	safe, err := inspectAssumptions(d, cs, req, nil, cfgLimit)
	if err != nil {
		return nil, err
	}
	if safe {
		return nil, nil
	}
	u := Unresolved(cs, nil)
	sort.SliceStable(u, func(i, j int) bool {
		mi, mj := greedyMagnitude(u[i]), greedyMagnitude(u[j])
		if mi != mj {
			return mi > mj
		}
		return u[i].CommandID < u[j].CommandID
	})
	stopped := func(k int) []TerminalAssumption {
		out := make([]TerminalAssumption, k)
		for i := 0; i < k; i++ {
			out[i] = TerminalAssumption{CommandID: u[i].CommandID, Outcome: "STOPPED"}
		}
		return out
	}
	if len(u) == 0 {
		return nil, nil
	}
	all, err := inspectAssumptions(d, cs, req, stopped(len(u)), cfgLimit)
	if err != nil {
		return nil, err
	}
	if !all {
		return nil, nil // even every unresolved command STOPPED would not admit the candidate
	}
	lo, hi := 1, len(u) // smallest k with the k largest sufficient lies in [lo, hi]
	for lo < hi {
		mid := (lo + hi) / 2
		ok, err := inspectAssumptions(d, cs, req, stopped(mid), cfgLimit)
		if err != nil {
			return nil, err
		}
		if ok {
			hi = mid
		} else {
			lo = mid + 1
		}
	}
	set := stopped(lo)
	// inclusion-minimality: drop a member (smallest first) whenever the rest still suffices
	for i := len(set) - 1; i >= 0 && len(set) > 1; i-- {
		rest := append(append([]TerminalAssumption(nil), set[:i]...), set[i+1:]...)
		ok, err := inspectAssumptions(d, cs, req, rest, cfgLimit)
		if err != nil {
			return nil, err
		}
		if ok {
			set = rest
		}
	}
	sort.Slice(set, func(i, j int) bool { return set[i].CommandID < set[j].CommandID })
	return []ContinuationCertificate{{SnapshotSHA256: id, DomainID: d.ID, Generation: d.Generation, Revision: d.Revision,
		CandidateID: req.CommandID, Required: set, AssumptionsOnly: true, GuardSafeIfVerified: true}}, nil
}
