package runtime

// Release 084 (PREREG_extra_084.md (c)): the full periodic reconciliation baseline PERIODIC_FULL.
//
// `evicon-authority -policy EVICON -resolve-trigger PERIODIC_FULL -resolve-sweep 1s` (policy name
// PERIODIC_FULL in the campaign configs, installed by evaluation/ext084.py). It is guarded and uses owner
// evidence only, exactly as EVICON: admission goes through the same relational guard and the same window
// of unresolved commands, every answer through the same receipt validation and contract admissibility,
// every fence through the same intake (R5, or R5' with -fence-intake per-identity). It differs from
// EVICON only in when and on what it acts:
//   - no certificate search, and no window-full or guard-block trigger: a blocked or deferred candidate is
//     answered at once (as RES_PERIODIC, the SWEEP_ONLY trigger of release 062);
//   - every sweep (the EVICON sweep cadence, 1 s on the testbed) it targets every unresolved command that
//     has been unresolved for at least the ambiguity threshold (ambiguity_after) and is not owner-uncertain,
//     oldest first, and spends at most probe_budget owner calls on them (EVICON's per-evaluation budget):
//       1. a query (if the contract has Q_A or X) of each target not queried in the last ambiguity_after,
//          oldest first, keeping one call for step 2 when the contract has a backward action (Q_S or F);
//       2. for the targets still unresolved after the queries: the action package resolve chooses for
//          them with EVICON's threshold rule (theta = cost / (hold rate x magnitude of the targets), the
//          rule of EVICON's own sweep) without the query-first step: a stop of each (Q_S, oldest first,
//          within the budget), or else one full-frontier fence (F), or a query after owner expiry (T with
//          Q_A), or nothing (WAIT / ESCALATE).
// The theta rule is kept (not "no theta") so that PERIODIC_FULL differs from EVICON only in its trigger:
// with theta = 0 it would also differ in its threshold, which the matched-cadence comparison of release 071
// (RES_PERIODIC, IMMEDIATE) already covers, and a fence every second would pause admission and seal young
// commands for nothing.

import (
	"fmt"
	"sort"
	"strings"
	"time"

	"evicon/internal/contract"
	"evicon/internal/journal"
	"evicon/internal/resolve"
	"evicon/internal/trace"
)

const modePeriodic = "PERIODIC"

// periodicFullLocked runs one PERIODIC_FULL sweep over targets (the aged unresolved commands). Caller holds a.mu.
func (a *Authority) periodicFullLocked(dcur journal.Domain, unresolved []journal.CommandRecord, targets map[string]bool, created map[string]time.Time, stats *policyStats) error {
	c := a.cfg.EvidenceContract
	if a.evidenceConflicted(dcur.ID) {
		if a.cfg.Trace != nil {
			_ = a.cfg.Trace.Write(trace.Event{DomainID: dcur.ID, Event: "EVICON_DECISION", Policy: string(a.cfg.Policy),
				Outcome: string(contract.ActionEscalate), Revision: dcur.Revision, Reason: "mode=" + modePeriodic + ";EVIDENCE_CONFLICT;targets=;threshold_ms=0;age_ms=0"})
		}
		return nil
	}
	order := make([]journal.CommandRecord, 0, len(targets))
	for _, u := range unresolved {
		if targets[u.CommandID] && !a.excludedFromTargets(u.CommandID) {
			order = append(order, u)
		}
	}
	if len(order) == 0 {
		return nil
	}
	sort.Slice(order, func(i, j int) bool {
		if order[i].Sequence != order[j].Sequence {
			return order[i].Sequence < order[j].Sequence
		}
		return order[i].CommandID < order[j].CommandID
	})
	backward := c.Has(contract.CapStop) || c.Has(contract.CapFence) || c.ExpiryRevealsStopped()
	// step 1: queries
	if c.Has(contract.CapQueryApplied) || c.Has(contract.CapAtomic) {
		limit := a.cfg.ProbeBudget
		if backward && limit > 1 {
			limit-- // keep one owner call for step 2
		}
		q := []string{}
		for _, u := range order {
			if len(q) >= limit {
				break
			}
			if !a.recentlyQueried(u.CommandID) {
				q = append(q, u.CommandID)
			}
		}
		if len(q) > 0 {
			if a.cfg.Trace != nil {
				_ = a.cfg.Trace.Write(trace.Event{DomainID: dcur.ID, Event: "EVICON_DECISION", Policy: string(a.cfg.Policy), Outcome: string(contract.ActionQuery),
					Revision: dcur.Revision, Reason: "mode=" + modePeriodic + ";QUERY_AGED_TARGETS;targets=" + strings.Join(q, ",") + ";threshold_ms=0;age_ms=0"})
			}
			byID := map[string]journal.CommandRecord{}
			for _, u := range order {
				byID[u.CommandID] = u
			}
			for _, id := range q {
				if err := a.queryDirect("", byID[id], stats); err != nil {
					return err
				}
			}
		}
	}
	if !backward || int(stats.ProbeCount) >= a.cfg.ProbeBudget {
		return nil
	}
	// step 2: the targets the queries left unresolved, on the fresh journal
	d2, err := a.cfg.Store.GetDomain(dcur.ID)
	if err != nil {
		return err
	}
	cmds, err := a.cfg.Store.ListCommandsForBelief(d2.ID, d2.CutoffSequence)
	if err != nil {
		return err
	}
	still := map[string]journal.CommandRecord{}
	var pendingNow []journal.CommandRecord
	for _, x := range cmds {
		if x.Status == "APPLIED" || x.Status == "STOPPED" {
			continue
		}
		pendingNow = append(pendingNow, x)
		if targets[x.CommandID] && !a.excludedFromTargets(x.CommandID) {
			still[x.CommandID] = x
		}
	}
	if len(still) == 0 {
		return nil
	}
	nowMS := time.Now().UnixMilli()
	in := resolve.Input{NowMS: nowMS, Contract: c, Blocked: true, QueryBudget: a.cfg.ProbeBudget - int(stats.ProbeCount)}
	for _, u := range order {
		x, ok := still[u.CommandID]
		if !ok {
			continue
		}
		t0 := created[x.CommandID]
		var mag int64
		for _, v := range x.Reservation {
			if v < 0 {
				v = -v
			}
			mag += v
		}
		in.Ambiguous = append(in.Ambiguous, resolve.Ambiguous{ID: x.CommandID, Sequence: x.Sequence, Magnitude: mag,
			AmbiguousSinceMS: t0.Add(a.cfg.AmbiguityAfter).UnixMilli(), FirstSendMS: t0.UnixMilli(), LastAttemptMS: t0.UnixMilli(),
			Queried: true, StopAttempted: a.recentlyStopped(x.CommandID), InCertificate: true})
	}
	pol := a.cfg.ResolvePolicy
	pol.QueryFirst = false // step 1 did the queries
	dec, err := resolve.Decide(in, a.cfg.ResolveCosts, pol)
	if err != nil {
		return err
	}
	stats.LastAction = dec.Action
	if a.cfg.Trace != nil {
		_ = a.cfg.Trace.Write(trace.Event{DomainID: d2.ID, Event: "EVICON_DECISION", Policy: string(a.cfg.Policy), Outcome: string(dec.Action), Revision: d2.Revision,
			Reason: fmt.Sprintf("mode=%s;%s;targets=%s;threshold_ms=%d;age_ms=%d", modePeriodic, dec.Reason, strings.Join(dec.Targets, ","), dec.ThresholdMS, dec.AgeMS)})
	}
	switch dec.Action {
	case contract.ActionStop:
		for _, id := range dec.Targets {
			if int(stats.ProbeCount) >= a.cfg.ProbeBudget {
				break
			}
			if err := a.stopOne("", still[id], stats); err != nil {
				return err
			}
		}
	case contract.ActionQuery: // owner expiry with a truthful query (T with Q_A)
		for _, id := range dec.Targets {
			if int(stats.ProbeCount) >= a.cfg.ProbeBudget {
				break
			}
			if err := a.queryDirect("", still[id], stats); err != nil {
				return err
			}
		}
	case contract.ActionFence:
		if a.cfg.FenceIntake == FenceIntakePerIdentity {
			if a.fenceCoversOnlyUncertain(pendingNow) {
				return nil
			}
		} else {
			for _, x := range pendingNow {
				if a.excludedFromTargets(x.CommandID) && !c.Has(contract.CapStop) {
					return nil // rule R5: the fence would abort again (EVICON_FENCE_BLOCKED_BY_OWNER_UNCERTAIN)
				}
			}
		}
		_, err := a.fenceLocked(d2, "", modePeriodic, stats)
		return err
	}
	return nil
}
