package runtime

import (
	"errors"
	"fmt"
	"sort"
	"sync"
	"time"

	"evicon/internal/analyzer"
	"evicon/internal/contract"
	"evicon/internal/journal"
	"evicon/internal/probe"
	"evicon/internal/protocol"
	"evicon/internal/trace"
)

type policyStats struct {
	ProbeCount       int64
	ProbeCostUS      int64
	SelectorNS       int64
	AnalyzedRevision uint64
}

type fetchedProbe struct {
	cmd           journal.CommandRecord
	rc            protocol.Receipt
	err           error
	actualUS      int64
	expectedUS    int64
	index         int64
	requestBytes  int64
	responseBytes int64
}

func (a *Authority) evaluateWithPolicy(d journal.Domain, req protocol.CommandRequest) (analyzer.Result, policyStats, error) {
	res, err := a.evaluateCandidate(d, req)
	stats := policyStats{AnalyzedRevision: d.Revision}
	if err != nil {
		return res, stats, err
	}
	if a.cfg.Policy == probe.EVICON && res.Verdict != analyzer.Safe {
		if a.cfg.ResolveTrigger == TriggerSweepOnly {
			// periodic baseline: only the background sweep resolves
			return res, stats, nil
		}
		// The resolver also handles a full outstanding budget (review RV01),
		// which the M-policies treat as a plain DEFER.
		return a.evaluateEvicon(req, stats)
	}
	if a.cfg.Policy == probe.M1W && res.Verdict == analyzer.Deferred && res.Reason == "OUTSTANDING_BUDGET_EXHAUSTED" {
		// Release 083 (T1): the window-full trigger of EVICON, without anything else of EVICON.
		return a.evaluateQueryOnlyWindow(req, stats)
	}
	if res.Verdict == analyzer.Safe || a.cfg.Policy == probe.M0NoProbe || probe.IsReleaseBaseline(a.cfg.Policy) || (res.Verdict != analyzer.Counterexample && res.Verdict != analyzer.ConservativeBlock) {
		return res, stats, nil
	}
	if (a.cfg.Policy == probe.M5 || a.cfg.Policy == probe.M5V2 || a.cfg.Policy == probe.M5V2D1) && res.Verdict == analyzer.Counterexample {
		cmds, err := a.cfg.Store.ListCommandsForBelief(d.ID, d.CutoffSequence)
		if err != nil {
			return analyzer.Result{}, stats, err
		}
		if len(probe.Unresolved(cmds, nil)) == 0 {
			return res, stats, nil
		}
	}
	if ec := a.cfg.EvidenceContract; ec.ID != "" && !ec.Has(contract.CapQueryApplied) {
		// Release 068: a query selector has nothing to ask an owner without Q_A. Before 068
		// M1 sent queries that the K0 owner answered 409 UNSUPPORTED (067 confirmatory
		// campaign: 2 of 16 K0 episodes, 46-66 queries each; outcomes unchanged).
		return analyzer.Result{Verdict: analyzer.Deferred, Reason: "NO_QUERY_IN_CONTRACT:" + res.Reason}, stats, nil
	}
	used, costUS, err := a.cfg.Store.ProbeUsage(req.DomainID, req.CommandID, req.IntentHash)
	if err != nil {
		return analyzer.Result{}, stats, err
	}
	stats.ProbeCount, stats.ProbeCostUS = used, costUS
	if int(used) >= a.cfg.ProbeBudget {
		return analyzer.Result{Verdict: analyzer.Deferred, Reason: "PROBE_BUDGET_EXHAUSTED"}, stats, nil
	}
	excluded, err := a.cfg.Store.ProbeQueriedIDs(req.DomainID, req.CommandID, req.IntentHash)
	if err != nil {
		return analyzer.Result{}, stats, err
	}

	for int(stats.ProbeCount) < a.cfg.ProbeBudget {
		dcur, err := a.cfg.Store.GetDomain(req.DomainID)
		if err != nil {
			return analyzer.Result{}, stats, err
		}
		res, err = a.evaluateCandidate(dcur, req)
		stats.AnalyzedRevision = dcur.Revision
		if err != nil {
			return analyzer.Result{}, stats, err
		}
		if res.Verdict == analyzer.Safe {
			return res, stats, nil
		}
		if res.Verdict != analyzer.Counterexample && res.Verdict != analyzer.ConservativeBlock {
			return res, stats, nil
		}

		cmds, err := a.cfg.Store.ListCommandsForBelief(dcur.ID, dcur.CutoffSequence)
		if err != nil {
			return analyzer.Result{}, stats, err
		}
		// Fix8: a definitive counterexample over fully resolved history must stay
		// a counterexample. The planner is only for resolving ambiguity.
		if (a.cfg.Policy == probe.M5 || a.cfg.Policy == probe.M5V2 || a.cfg.Policy == probe.M5V2D1) && res.Verdict == analyzer.Counterexample && len(probe.Unresolved(cmds, nil)) == 0 {
			return res, stats, nil
		}
		remaining := a.cfg.ProbeBudget - int(stats.ProbeCount)
		if remaining <= 0 {
			break
		}

		if probe.IsQueryOnly(a.cfg.Policy) { // M1, and M1W on a guard block (release 083)
			start := time.Now()
			ranked, err := probe.SelectSimple(probe.M1, dcur, cmds, req, a.cfg.QueryCosts, excluded, a.cfg.MaxConfigurations)
			stats.SelectorNS += time.Since(start).Nanoseconds()
			if err != nil {
				return analyzer.Result{}, stats, err
			}
			if len(ranked) == 0 {
				return analyzer.Result{Verdict: analyzer.Deferred, Reason: "NO_UNQUERIED_EVIDENCE"}, stats, nil
			}
			if len(ranked) > remaining {
				ranked = ranked[:remaining]
			}
			if err := a.queryBatch(req, ranked, excluded, &stats); err != nil {
				return analyzer.Result{}, stats, err
			}
			continue
		}

		var chosen journal.CommandRecord
		selectStart := time.Now()
		switch a.cfg.Policy {
		case probe.M0:
			// Reconciliation is explicitly frozen before the integration runs.
			// M1 retains historical sequential-ID behavior; M2/M3/M4 reuse the
			// same ranking implementation as their corresponding comparator.
			ranked, err := probe.SelectSimple(a.cfg.LedgerReconcile, dcur, cmds, req, a.cfg.QueryCosts, excluded, a.cfg.MaxConfigurations)
			stats.SelectorNS += time.Since(selectStart).Nanoseconds()
			if err != nil {
				return analyzer.Result{}, stats, err
			}
			if len(ranked) == 0 {
				return analyzer.Result{Verdict: analyzer.Deferred, Reason: "NO_UNQUERIED_EVIDENCE"}, stats, nil
			}
			chosen = ranked[0]
		case probe.M2, probe.M3, probe.M4:
			ranked, err := probe.SelectSimple(a.cfg.Policy, dcur, cmds, req, a.cfg.QueryCosts, excluded, a.cfg.MaxConfigurations)
			stats.SelectorNS += time.Since(selectStart).Nanoseconds()
			if err != nil {
				return analyzer.Result{}, stats, err
			}
			if len(ranked) == 0 {
				return analyzer.Result{Verdict: analyzer.Deferred, Reason: "NO_UNQUERIED_EVIDENCE"}, stats, nil
			}
			chosen = ranked[0]
		case probe.M4P:
			ranked, err := probe.SelectM4P(dcur, cmds, req, a.cfg.QueryCosts, a.cfg.OutcomeModel, excluded, a.cfg.MaxConfigurations)
			stats.SelectorNS += time.Since(selectStart).Nanoseconds()
			if err != nil {
				return analyzer.Result{}, stats, err
			}
			if len(ranked) == 0 {
				return analyzer.Result{Verdict: analyzer.Deferred, Reason: "NO_UNQUERIED_EVIDENCE"}, stats, nil
			}
			chosen = ranked[0]
		case probe.M5V2, probe.M5V2D1:
			depth := a.cfg.PlannerDepth
			if a.cfg.Policy == probe.M5V2D1 {
				depth = 1
			}
			if depth > remaining {
				depth = remaining
			}
			planner := probe.AdaptivePlanner{Model: a.cfg.OutcomeModel, Costs: a.cfg.QueryCosts, MaxConfigurations: a.cfg.MaxConfigurations}
			choice, ok, planErr := planner.Choose(dcur, cmds, req, depth, excluded, a.cfg.PlannerComputeBudget)
			elapsed := time.Since(selectStart).Nanoseconds()
			stats.SelectorNS += elapsed
			reason := "SELECTED"
			if !ok {
				reason = "NO_POSITIVE_UTILITY"
			}
			if planErr != nil {
				reason = "ERROR"
			}
			if errors.Is(planErr, probe.ErrPlannerBudget) {
				reason = "BUDGET_EXHAUSTED"
			}
			if a.cfg.Trace != nil {
				predicted := choice.SuccessProb
				utility := choice.Utility
				if traceErr := a.cfg.Trace.Write(trace.Event{DomainID: req.DomainID, CommandID: req.CommandID, Event: "PLANNER_V2_EVALUATED", Policy: string(a.cfg.Policy), Outcome: reason, OutcomeModelHash: a.cfg.OutcomeModel.Hash, PlannerDepth: depth, PlannerElapsedNS: elapsed, PlannerBudgetNS: a.cfg.PlannerComputeBudget.Nanoseconds(), PredictedSafeProb: &predicted, PredictedUtility: &utility, Revision: dcur.Revision}); traceErr != nil {
					return analyzer.Result{}, stats, traceErr
				}
			}
			if errors.Is(planErr, probe.ErrPlannerBudget) {
				return analyzer.Result{Verdict: analyzer.Deferred, Reason: "PLANNER_COMPUTE_BUDGET_EXHAUSTED"}, stats, nil
			}
			if planErr != nil {
				return analyzer.Result{}, stats, planErr
			}
			cid := choice.CommandID
			fallback := ""
			if !ok {
				// Fail safely to M4's QUERY RANKING only. Never use a model
				// prediction or partial tree to produce a SAFE verdict.
				ranked, e := probe.SelectSimple(probe.M4, dcur, cmds, req, a.cfg.QueryCosts, excluded, a.cfg.MaxConfigurations)
				stats.SelectorNS += time.Since(selectStart).Nanoseconds() - elapsed
				if e != nil {
					return analyzer.Result{}, stats, e
				}
				if len(ranked) == 0 {
					return analyzer.Result{Verdict: analyzer.Deferred, Reason: "NO_UNQUERIED_EVIDENCE"}, stats, nil
				}
				cid = ranked[0].CommandID
				fallback = "NO_POSITIVE_UTILITY"
			}
			found := false
			for _, c := range cmds {
				if c.CommandID == cid && c.Status != "APPLIED" && c.Status != "STOPPED" && !excluded[cid] {
					chosen = c
					found = true
					break
				}
			}
			if !found {
				return analyzer.Result{}, stats, fmt.Errorf("M5V2 chose invalid query %s", cid)
			}
			if a.cfg.Trace != nil {
				if traceErr := a.cfg.Trace.Write(trace.Event{DomainID: req.DomainID, CommandID: req.CommandID, Event: "PLANNER_V2_SELECTED", Policy: string(a.cfg.Policy), QueryCommandID: cid, OutcomeModelHash: a.cfg.OutcomeModel.Hash, PlannerFallbackReason: fallback, ExpectedCostUS: a.cfg.QueryCosts.CostUS(cid), Revision: dcur.Revision}); traceErr != nil {
					return analyzer.Result{}, stats, traceErr
				}
			}
		case probe.M5:
			depth := a.cfg.PlannerDepth
			if depth > remaining {
				depth = remaining
			}
			cacheKey := fmt.Sprintf("p=%s|r=%d|h=%s|d=%d|b=%d", a.cfg.Profile, dcur.Revision, req.IntentHash, depth, remaining)
			plan, ok, err := a.planner.Plan(cacheKey, dcur, cmds, req, depth, excluded, a.cfg.PlannerComputeBudget)
			elapsed := time.Since(selectStart).Nanoseconds()
			stats.SelectorNS += elapsed
			outcome := "NO_FEASIBLE_PLAN"
			if ok {
				outcome = "SELECTED"
			}
			if err != nil {
				outcome = "ERROR"
			}
			if errors.Is(err, probe.ErrPlannerBudget) {
				outcome = "BUDGET_EXHAUSTED"
			}
			if a.cfg.Trace != nil {
				hit, enabled := plan.CacheHit, a.cfg.PlannerCache
				if traceErr := a.cfg.Trace.Write(trace.Event{DomainID: req.DomainID, CommandID: req.CommandID,
					Event: "PLANNER_EVALUATED", Policy: string(a.cfg.Policy), Outcome: outcome,
					PlannerDepth: depth, PlannerCacheHit: &hit, PlannerCacheEnabled: &enabled,
					PlannerElapsedNS: elapsed, PlannerBudgetNS: a.cfg.PlannerComputeBudget.Nanoseconds(), Revision: dcur.Revision}); traceErr != nil {
					return analyzer.Result{}, stats, traceErr
				}
			}
			if errors.Is(err, probe.ErrPlannerBudget) {
				return analyzer.Result{Verdict: analyzer.Deferred, Reason: "PLANNER_COMPUTE_BUDGET_EXHAUSTED"}, stats, nil
			}
			if err != nil {
				return analyzer.Result{}, stats, err
			}
			if !ok || len(plan.Steps) == 0 {
				return analyzer.Result{Verdict: analyzer.Deferred, Reason: "NO_FEASIBLE_PROBE_PLAN"}, stats, nil
			}
			cid := plan.Steps[0].CommandID
			found := false
			for _, c := range cmds {
				if c.CommandID == cid {
					chosen = c
					found = true
					break
				}
			}
			if !found {
				return analyzer.Result{}, stats, fmt.Errorf("planner selected unknown command %s", cid)
			}
			if a.cfg.Trace != nil {
				hit := plan.CacheHit
				if traceErr := a.cfg.Trace.Write(trace.Event{DomainID: req.DomainID, CommandID: req.CommandID, Event: "PLANNER_SELECTED", Policy: string(a.cfg.Policy), QueryCommandID: cid, ExpectedCostUS: a.cfg.QueryCosts.CostUS(cid), PlannerDepth: depth, PlannerCacheHit: &hit, Revision: dcur.Revision, Reason: fmt.Sprintf("path_cost_us=%d;steps=%d", plan.CostUS, len(plan.Steps))}); traceErr != nil {
					return analyzer.Result{}, stats, traceErr
				}
			}
		default:
			return analyzer.Result{}, stats, fmt.Errorf("unsupported policy %s", a.cfg.Policy)
		}
		if err := a.queryOne(req, chosen, excluded, &stats); err != nil {
			return analyzer.Result{}, stats, err
		}
	}
	dcur, err := a.cfg.Store.GetDomain(req.DomainID)
	if err != nil {
		return analyzer.Result{}, stats, err
	}
	res, err = a.evaluateCandidate(dcur, req)
	stats.AnalyzedRevision = dcur.Revision
	if err != nil {
		return analyzer.Result{}, stats, err
	}
	if res.Verdict == analyzer.Safe {
		return res, stats, nil
	}
	return analyzer.Result{Verdict: analyzer.Deferred, Reason: "PROBE_BUDGET_EXHAUSTED"}, stats, nil
}

func (a *Authority) reserveProbeRecords(candidate protocol.CommandRequest, cmds []journal.CommandRecord, startIndex int64) ([]fetchedProbe, error) {
	d, err := a.cfg.Store.GetDomain(candidate.DomainID)
	if err != nil {
		return nil, err
	}
	recs := make([]journal.ProbeRecord, len(cmds))
	fps := make([]fetchedProbe, len(cmds))
	for i, c := range cmds {
		idx := startIndex + int64(i)
		expected := a.cfg.QueryCosts.CostUS(c.CommandID)
		recs[i] = journal.ProbeRecord{DomainID: candidate.DomainID, CandidateID: candidate.CommandID, CandidateHash: candidate.IntentHash, ProbeIndex: idx, QueriedCommand: c.CommandID, Policy: string(a.cfg.Policy), ExpectedCostUS: expected, RevisionBefore: d.Revision, RevisionAfter: d.Revision, Status: "RESERVED"}
		fps[i] = fetchedProbe{cmd: c, expectedUS: expected, index: idx}
	}
	if err := a.cfg.Store.ReserveProbeAttempts(recs); err != nil {
		return nil, err
	}
	if a.cfg.Trace != nil {
		for _, fp := range fps {
			_ = a.cfg.Trace.Write(trace.Event{DomainID: candidate.DomainID, CommandID: candidate.CommandID, Event: "PROBE_RESERVED", Policy: string(a.cfg.Policy), QueryCommandID: fp.cmd.CommandID, ProbeIndex: fp.index, ExpectedCostUS: fp.expectedUS, Revision: d.Revision})
		}
	}
	return fps, nil
}

func (a *Authority) queryOne(candidate protocol.CommandRequest, cmd journal.CommandRecord, excluded map[string]bool, stats *policyStats) error {
	fps, err := a.reserveProbeRecords(candidate, []journal.CommandRecord{cmd}, stats.ProbeCount+1)
	if err != nil {
		return err
	}
	stats.ProbeCount++ // budget is consumed durably before the network send
	fp := a.fetchReservedProbe(fps[0], candidate)
	return a.commitFetchedProbe(candidate, fp, excluded, stats)
}

func (a *Authority) queryBatch(candidate protocol.CommandRequest, cmds []journal.CommandRecord, excluded map[string]bool, stats *policyStats) error {
	concurrency := a.cfg.QueryConcurrency
	if concurrency <= 0 {
		concurrency = 2
	}
	reserved, err := a.reserveProbeRecords(candidate, cmds, stats.ProbeCount+1)
	if err != nil {
		return err
	}
	stats.ProbeCount += int64(len(reserved)) // all batch slots reserved before any send
	for start := 0; start < len(reserved); start += concurrency {
		end := start + concurrency
		if end > len(reserved) {
			end = len(reserved)
		}
		batch := reserved[start:end]
		out := make([]fetchedProbe, len(batch))
		var wg sync.WaitGroup
		for i, fp := range batch {
			wg.Add(1)
			go func(i int, fp fetchedProbe) { defer wg.Done(); out[i] = a.fetchReservedProbe(fp, candidate) }(i, fp)
		}
		wg.Wait()
		sort.SliceStable(out, func(i, j int) bool { return out[i].index < out[j].index })
		for _, fp := range out {
			if err := a.commitFetchedProbe(candidate, fp, excluded, stats); err != nil {
				return err
			}
		}
	}
	return nil
}

func (a *Authority) fetchReservedProbe(fp fetchedProbe, candidate protocol.CommandRequest) fetchedProbe {
	if err := a.cfg.Store.MarkProbeStarted(candidate.DomainID, candidate.CommandID, candidate.IntentHash, fp.index); err != nil {
		fp.err = err
		return fp
	}
	if a.cfg.Trace != nil {
		_ = a.cfg.Trace.Write(trace.Event{DomainID: candidate.DomainID, CommandID: candidate.CommandID, Event: "PROBE_ATTEMPT", Policy: string(a.cfg.Policy), QueryCommandID: fp.cmd.CommandID, ProbeIndex: fp.index, ExpectedCostUS: fp.expectedUS})
	}
	st := time.Now()
	fp.rc, _, fp.requestBytes, fp.responseBytes, fp.err = a.fetchReceiptMeasured(fp.cmd)
	fp.actualUS = time.Since(st).Microseconds()
	if fp.actualUS < 0 {
		fp.actualUS = 0
	}
	if a.cfg.Faults != nil {
		a.cfg.Faults.Hit("AFTER_PROBE_RESPONSE_BEFORE_ACCOUNTING", candidate.CommandID)
	}
	return fp
}

func (a *Authority) commitFetchedProbe(candidate protocol.CommandRequest, fp fetchedProbe, excluded map[string]bool, stats *policyStats) error {
	before, err := a.cfg.Store.GetDomain(candidate.DomainID)
	if err != nil {
		return err
	}
	outcome := "UNCERTAIN"
	reason := "QUERY_ERROR"
	informative := false
	beliefChanged := false
	verdictChanged := false
	afterRev := before.Revision
	beforeVerdict, beforeEvalErr := a.evaluateCandidate(before, candidate)
	if fp.err == nil {
		outcome = fp.rc.Outcome
		reason = fp.rc.Reason
		if err := validateReceipt(fp.cmd, fp.rc, a.cfg.Domain.Dimension); err != nil {
			outcome = "INVALID_EVIDENCE"
			reason = err.Error()
			if a.cfg.Trace != nil {
				_ = a.cfg.Trace.Write(trace.Event{DomainID: candidate.DomainID, CommandID: candidate.CommandID, Event: "PROBE_EVIDENCE_REJECTED", Policy: string(a.cfg.Policy), QueryCommandID: fp.cmd.CommandID, ProbeIndex: fp.index, ActualCostUS: fp.actualUS, QueryRequestBytes: fp.requestBytes, QueryResponseBytes: fp.responseBytes, Outcome: outcome, Reason: reason})
			}
		} else {
			rev, er := a.cfg.Store.RecordEvidence(journal.ReceiptRecord{DomainID: fp.rc.DomainID, CommandID: fp.rc.CommandID, IntentHash: fp.rc.IntentHash, Outcome: fp.rc.Outcome, OwnerVersion: fp.rc.OwnerVersion, ReceiptVersion: fp.rc.ReceiptVersion, State: fp.rc.State}, fp.rc.Reason, "POLICY_QUERY", false)
			if errors.Is(er, journal.ErrTerminalReversal) {
				// release 068: owner evidence contradicts a committed outcome; keep the committed
				// outcome, record the conflict, never treat it as informative
				a.noteEvidenceConflict(fp.cmd.DomainID, fp.cmd.CommandID, "POLICY_QUERY", fp.cmd.Status, fp.rc.Outcome, er.Error())
				outcome, reason, rev = "EVIDENCE_CONFLICT", er.Error(), before.Revision
			} else if er != nil {
				return er
			}
			afterRev = rev
			beliefChanged = outcome != "EVIDENCE_CONFLICT" && (fp.rc.Outcome == "APPLIED" || fp.rc.Outcome == "STOPPED") && fp.cmd.Status != fp.rc.Outcome
			afterDomain, derr := a.cfg.Store.GetDomain(candidate.DomainID)
			if derr == nil && beforeEvalErr == nil {
				afterVerdict, aerr := a.evaluateCandidate(afterDomain, candidate)
				if aerr == nil {
					verdictChanged = beforeVerdict.Verdict != afterVerdict.Verdict
				}
			}
			informative = beliefChanged || verdictChanged
		}
	} else {
		reason = fp.err.Error()
	}
	pr := journal.ProbeRecord{DomainID: candidate.DomainID, CandidateID: candidate.CommandID, CandidateHash: candidate.IntentHash, ProbeIndex: fp.index, QueriedCommand: fp.cmd.CommandID, Policy: string(a.cfg.Policy), ExpectedCostUS: fp.expectedUS, ActualCostUS: fp.actualUS, Outcome: outcome, Informative: informative, BeliefChanged: beliefChanged, VerdictChanged: verdictChanged, RevisionBefore: before.Revision, RevisionAfter: afterRev, Reason: reason, Status: "COMPLETED"}
	pr.QueryRequestBytes, pr.QueryResponseBytes = fp.requestBytes, fp.responseBytes
	if err := a.cfg.Store.CompleteProbeAttempt(pr); err != nil {
		return err
	}
	excluded[fp.cmd.CommandID] = true
	stats.ProbeCostUS += fp.actualUS
	if a.cfg.Trace != nil {
		inf := informative
		_ = a.cfg.Trace.Write(trace.Event{DomainID: candidate.DomainID, CommandID: candidate.CommandID, Event: "PROBE_RESULT", Policy: string(a.cfg.Policy), QueryCommandID: fp.cmd.CommandID, ProbeIndex: fp.index, ExpectedCostUS: fp.expectedUS, ActualCostUS: fp.actualUS, QueryRequestBytes: fp.requestBytes, QueryResponseBytes: fp.responseBytes, Outcome: outcome, Informative: &inf, Revision: afterRev, Reason: fmt.Sprintf("%s;belief_changed=%t;verdict_changed=%t", reason, beliefChanged, verdictChanged)})
	}
	return nil
}
