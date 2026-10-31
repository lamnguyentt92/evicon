package runtime

// M1W (release 083, PREREG_extra_083.md T1): QueryOnly with the window-full trigger.
//
// M1 (QueryOnly) queries the owner only when the guard blocks a candidate on the budget; a
// candidate deferred because the window of unresolved commands is full (DEFER
// OUTSTANDING_BUDGET_EXHAUSTED) is a plain deferral for it. EVICON, on the same deferral, resolves
// the aged ambiguous commands, because any terminal answer frees a window slot (modePendingBound in
// evicon.go). M1W adds exactly that trigger to M1 and nothing else: the same targets (unresolved
// commands at least AmbiguityAfter old, oldest sequence first, skipping those queried in the last
// AmbiguityAfter), the same per-evaluation action budget (ProbeBudget), the same query path
// (queryDirect: receipt validation and the contract's admissibility rule, so an APPLIED answer and,
// with owner expiry, a NOT_FOUND after the expiry are recorded), and no stop, fence, escalation or
// background sweep. The admission guard is unchanged.

import (
	"fmt"
	"sort"
	"time"

	"evicon/internal/analyzer"
	"evicon/internal/contract"
	"evicon/internal/journal"
	"evicon/internal/probe"
	"evicon/internal/protocol"
	"evicon/internal/trace"
)

const windowReason = "OUTSTANDING_BUDGET_EXHAUSTED"

// evaluateQueryOnlyWindow runs the window-full trigger for a candidate the window defers.
// Caller holds a.mu.
func (a *Authority) evaluateQueryOnlyWindow(req protocol.CommandRequest, stats policyStats) (analyzer.Result, policyStats, error) {
	if ec := a.cfg.EvidenceContract; !(ec.Has(contract.CapQueryApplied) || ec.Has(contract.CapAtomic)) {
		return analyzer.Result{Verdict: analyzer.Deferred, Reason: windowReason + ";M1W_NO_QUERY_IN_CONTRACT"}, stats, nil
	}
	for {
		dcur, err := a.cfg.Store.GetDomain(req.DomainID)
		if err != nil {
			return analyzer.Result{}, stats, err
		}
		res, err := a.evaluateCandidate(dcur, req)
		stats.AnalyzedRevision = dcur.Revision
		if err != nil {
			return analyzer.Result{}, stats, err
		}
		if res.Verdict != analyzer.Deferred || res.Reason != windowReason {
			// admitted, or now a guard verdict: the next submission takes M1's path for a block
			return res, stats, nil
		}
		if int(stats.ProbeCount) >= a.cfg.ProbeBudget {
			return analyzer.Result{Verdict: analyzer.Deferred, Reason: windowReason + ";M1W_ACTION_BUDGET_EXHAUSTED"}, stats, nil
		}
		targets, err := a.windowQueryTargets(dcur, a.cfg.ProbeBudget-int(stats.ProbeCount))
		if err != nil {
			return analyzer.Result{}, stats, err
		}
		if len(targets) == 0 {
			return analyzer.Result{Verdict: analyzer.Deferred, Reason: windowReason + ";M1W_WAIT:NOTHING_TO_QUERY"}, stats, nil
		}
		if a.cfg.Trace != nil {
			ids := make([]string, len(targets))
			for i, t := range targets {
				ids[i] = t.CommandID
			}
			_ = a.cfg.Trace.Write(trace.Event{DomainID: req.DomainID, CommandID: req.CommandID, Event: "QUERY_ONLY_WINDOW_TRIGGER",
				Policy: string(a.cfg.Policy), Revision: dcur.Revision, Reason: fmt.Sprintf("targets=%d;%v", len(ids), ids)})
		}
		for _, t := range targets {
			if err := a.queryDirect(req.CommandID, t, &stats); err != nil {
				return analyzer.Result{}, stats, err
			}
		}
	}
}

// windowQueryTargets returns at most n unresolved commands that have been ambiguous for at least
// AmbiguityAfter and were not queried in the last AmbiguityAfter, lowest sequence first (the order of
// resolve.Decide's queries).
func (a *Authority) windowQueryTargets(dcur journal.Domain, n int) ([]journal.CommandRecord, error) {
	cmds, err := a.cfg.Store.ListCommandsForBelief(dcur.ID, dcur.CutoffSequence)
	if err != nil {
		return nil, err
	}
	unresolved := probe.Unresolved(cmds, nil)
	if len(unresolved) == 0 || n <= 0 {
		return nil, nil
	}
	created, err := a.cfg.Store.CommandCreatedTimes(dcur.ID)
	if err != nil {
		return nil, err
	}
	now := time.Now()
	out := []journal.CommandRecord{}
	for _, u := range unresolved {
		t0, ok := created[u.CommandID]
		if !ok || now.Sub(t0) < a.cfg.AmbiguityAfter || a.recentlyQueried(u.CommandID) {
			continue
		}
		out = append(out, u)
	}
	sort.SliceStable(out, func(i, j int) bool {
		if out[i].Sequence != out[j].Sequence {
			return out[i].Sequence < out[j].Sequence
		}
		return out[i].CommandID < out[j].CommandID
	})
	if len(out) > n {
		out = out[:n]
	}
	return out, nil
}
