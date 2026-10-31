package runtime

// The EVICON evidence-contract resolution policy (release 061, activation
// paths added in 062 after review RV01).
//
// EVICON runs (a) when the guard blocks a candidate, (b) when the outstanding
// budget is full, and (c) from a background sweep over aged ambiguous
// reservations. In each case EVICON
//  1. enumerates minimal continuation certificates (hypothetical terminal
//     outcomes that would make the candidate safe; never evidence),
//  2. marks the commands whose STOPPED outcome appears in a certificate,
//  3. asks package resolve which contract-allowed action to take (QUERY,
//     per-identity STOP, owner FENCE via the E2 barrier, WAIT, ESCALATE),
//  4. executes it, validates the returned evidence through the same receipt
//     validation + contract admissibility rules, records it in the journal,
//     and re-evaluates the guard on the fresh belief.
//
// Only validated owner evidence ever changes the belief; certificates,
// thresholds and predictions never do.

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"strings"
	"time"

	"evicon/internal/analyzer"
	"evicon/internal/contract"
	"evicon/internal/journal"
	"evicon/internal/probe"
	"evicon/internal/protocol"
	"evicon/internal/resolve"
	"evicon/internal/trace"
)

func blockedVerdict(r analyzer.Result) bool {
	return r.Verdict == analyzer.Counterexample || r.Verdict == analyzer.ConservativeBlock
}

// resolveMode says why the resolver runs.
const (
	modeCertificate  = "CERTIFICATE"   // guard block: STOPPED for certificate members would admit the candidate
	modePendingBound = "PENDING_BOUND" // outstanding budget full: any terminal outcome frees a slot
	modeSweep        = "SWEEP"         // background: aged ambiguous reservations, no candidate needed
)

func (a *Authority) evaluateEvicon(req protocol.CommandRequest, stats policyStats) (analyzer.Result, policyStats, error) {
	for iter := 0; ; iter++ {
		dcur, err := a.cfg.Store.GetDomain(req.DomainID)
		if err != nil {
			return analyzer.Result{}, stats, err
		}
		res, err := a.evaluateCandidate(dcur, req)
		stats.AnalyzedRevision = dcur.Revision
		if err != nil {
			return analyzer.Result{}, stats, err
		}
		if res.Verdict == analyzer.Safe {
			return res, stats, nil
		}
		pendingBound := res.Verdict == analyzer.Deferred && res.Reason == "OUTSTANDING_BUDGET_EXHAUSTED"
		if !blockedVerdict(res) && !pendingBound {
			return res, stats, nil
		}
		// Release 072: a DEFER whose cause is the window of unresolved commands keeps that
		// cause first ("OUTSTANDING_BUDGET_EXHAUSTED;<EVICON reason>"), so traces and the
		// per-cell defer counts can tell window-bound waits from capacity-bound ones.
		deferReason := func(r string) string {
			if pendingBound {
				return "OUTSTANDING_BUDGET_EXHAUSTED;" + r
			}
			return r
		}
		if iter >= a.cfg.ProbeBudget || int(stats.ProbeCount) >= a.cfg.ProbeBudget {
			if pendingBound {
				return analyzer.Result{Verdict: analyzer.Deferred, Reason: deferReason("EVICON_ACTION_BUDGET_EXHAUSTED")}, stats, nil
			}
			return analyzer.Result{Verdict: analyzer.Deferred, Reason: "EVICON_ACTION_BUDGET_EXHAUSTED:" + res.Reason}, stats, nil
		}
		cmds, err := a.cfg.Store.ListCommandsForBelief(dcur.ID, dcur.CutoffSequence)
		if err != nil {
			return analyzer.Result{}, stats, err
		}
		unresolved := probe.Unresolved(cmds, nil)
		if len(unresolved) == 0 {
			return res, stats, nil // true exhaustion: no evidence can help
		}
		targets := map[string]bool{}
		mode := modeCertificate
		created, err := a.cfg.Store.CommandCreatedTimes(dcur.ID)
		if err != nil {
			return analyzer.Result{}, stats, err
		}
		if pendingBound {
			// Every terminal outcome (APPLIED or STOPPED) frees a pending slot,
			// so every aged ambiguous command is a target. The guard still
			// decides admission afterwards.
			mode = modePendingBound
			now := time.Now()
			for _, u := range unresolved {
				if now.Sub(created[u.CommandID]) >= a.cfg.AmbiguityAfter {
					targets[u.CommandID] = true
				}
			}
		} else {
			start := time.Now()
			certs, cerr := a.continuationCertificates(dcur, cmds, req)
			stats.SelectorNS += time.Since(start).Nanoseconds()
			if cerr != nil {
				return analyzer.Result{Verdict: analyzer.Deferred, Reason: "EVICON_CERTIFICATE_SEARCH_FAILED"}, stats, nil
			}
			for _, cert := range certs {
				for _, x := range cert.Required {
					if x.Outcome == "STOPPED" {
						targets[x.CommandID] = true
					}
				}
			}
		}
		if len(targets) == 0 {
			if pendingBound {
				return analyzer.Result{Verdict: analyzer.Deferred, Reason: deferReason("EVICON_WAIT:PENDING_NOT_AGED")}, stats, nil
			}
			// T3: if no STOPPED assumption admits the candidate, evidence cannot.
			return res, stats, nil
		}
		act, err := a.resolveLocked(dcur, req.CommandID, mode, unresolved, targets, created, &stats)
		if err != nil {
			return analyzer.Result{}, stats, err
		}
		switch act {
		case "":
			// an action ran; re-evaluate on the fresh belief
		default:
			return analyzer.Result{Verdict: analyzer.Deferred, Reason: deferReason(act)}, stats, nil
		}
	}
}

// continuationCertificates (release 083) runs the configured certificate search: the bounded
// enumeration (default; releases 061-082) or, with Certificate = greedy, the greedy certificate of an
// additive domain (Section 3.6; probe.GreedyContinuationCertificate), which has no size bound and never
// gives up. A domain outside the greedy certificate's premise uses the enumeration.
func (a *Authority) continuationCertificates(d journal.Domain, cmds []journal.CommandRecord, req protocol.CommandRequest) ([]probe.ContinuationCertificate, error) {
	if a.cfg.Certificate == CertificateGreedy {
		certs, err := probe.GreedyContinuationCertificate(d, cmds, req, a.cfg.MaxConfigurations)
		if !errors.Is(err, probe.ErrNotAdditive) {
			return certs, err
		}
	}
	return probe.EnumerateContinuationCertificates(d, cmds, req, a.cfg.CertificateDepth, a.cfg.MaxConfigurations, 20000, 20*a.cfg.PlannerComputeBudget)
}

// resolveLocked asks package resolve for an action over targets and executes
// it. It returns "" when an action was executed (the caller re-evaluates) or
// a DEFER reason. Caller holds a.mu.
func (a *Authority) resolveLocked(dcur journal.Domain, origin, mode string, unresolved []journal.CommandRecord, targets map[string]bool, created map[string]time.Time, stats *policyStats) (string, error) {
	c := a.cfg.EvidenceContract
	if a.evidenceConflicted(dcur.ID) {
		// Release 068: the owner contradicted a committed terminal outcome. Its query, stop
		// and fence evidence cannot be trusted any more for this domain: escalate, do not
		// act (067 testbed: the same barrier was retried every second until the cell ended).
		if a.cfg.Trace != nil {
			_ = a.cfg.Trace.Write(trace.Event{DomainID: dcur.ID, CommandID: origin, Event: "EVICON_DECISION", Policy: string(a.cfg.Policy),
				Outcome: string(contract.ActionEscalate), Revision: dcur.Revision, Reason: "mode=" + mode + ";EVIDENCE_CONFLICT;targets=;threshold_ms=0;age_ms=0"})
		}
		return "EVICON_ESCALATE:EVIDENCE_CONFLICT", nil
	}
	blockedByUncertain := []string{}
	for _, u := range unresolved {
		if a.isOwnerUncertain(u.CommandID) {
			blockedByUncertain = append(blockedByUncertain, u.CommandID)
			delete(targets, u.CommandID) // T2: the owner cannot certify its past
		}
	}
	if len(targets) == 0 {
		return "EVICON_OWNER_UNCERTAIN", nil
	}
	nowMS := time.Now().UnixMilli()
	budget := a.cfg.ProbeBudget - int(stats.ProbeCount)
	in := resolve.Input{NowMS: nowMS, Contract: c, Blocked: true, QueryBudget: budget}
	byID := map[string]journal.CommandRecord{}
	for _, u := range unresolved {
		byID[u.CommandID] = u
		t0 := created[u.CommandID]
		var mag int64
		for _, v := range u.Reservation {
			if v < 0 {
				v = -v
			}
			mag += v
		}
		in.Ambiguous = append(in.Ambiguous, resolve.Ambiguous{ID: u.CommandID, Sequence: u.Sequence, Magnitude: mag,
			AmbiguousSinceMS: t0.Add(a.cfg.AmbiguityAfter).UnixMilli(), FirstSendMS: t0.UnixMilli(), LastAttemptMS: t0.UnixMilli(),
			Queried: a.recentlyQueried(u.CommandID), StopAttempted: a.recentlyStopped(u.CommandID), InCertificate: targets[u.CommandID]})
	}
	d, err := resolve.Decide(in, a.cfg.ResolveCosts, a.cfg.ResolvePolicy)
	if err != nil {
		return "", err
	}
	if a.cfg.Trace != nil {
		_ = a.cfg.Trace.Write(trace.Event{DomainID: dcur.ID, CommandID: origin, Event: "EVICON_DECISION", Policy: string(a.cfg.Policy),
			Outcome: string(d.Action), Revision: dcur.Revision, Reason: fmt.Sprintf("mode=%s;%s;targets=%s;threshold_ms=%d;age_ms=%d", mode, d.Reason, strings.Join(d.Targets, ","), d.ThresholdMS, d.AgeMS)})
	}
	switch d.Action {
	case contract.ActionQuery:
		for _, id := range d.Targets {
			if int(stats.ProbeCount) >= a.cfg.ProbeBudget {
				break
			}
			if err := a.queryDirect(origin, byID[id], stats); err != nil {
				return "", err
			}
		}
		return "", nil
	case contract.ActionStop:
		for _, id := range d.Targets {
			if int(stats.ProbeCount) >= a.cfg.ProbeBudget {
				break
			}
			if err := a.stopOne(origin, byID[id], stats); err != nil {
				return "", err
			}
		}
		return "", nil
	case contract.ActionFence:
		if len(blockedByUncertain) > 0 && !c.Has(contract.CapStop) {
			// a full-frontier fence would include an identity the owner cannot
			// certify; it would abort again (see runBarrierLocked)
			return "EVICON_FENCE_BLOCKED_BY_OWNER_UNCERTAIN", nil
		}
		stats.ProbeCount++
		token := fmt.Sprintf("evicon-%s-%s-r%d", strings.ToLower(mode), origin, dcur.Revision)
		// A pending barrier (an earlier fence whose owner call failed, e.g. the
		// device was offline) keeps the domain paused: retry that barrier with
		// its own token and cutoff instead of starting a new one.
		if pb, ok, err := a.cfg.Store.PendingBarrier(dcur.ID); err != nil {
			return "", err
		} else if ok {
			token = pb.Token
		} else {
			// A barrier aborted at this revision (the owner could not certify a crash orphan)
			// is never retried under the same token: BeginBarrier refuses an ABORTED token
			// forever, and the revision does not move while admission is blocked, so the
			// domain could never fence again (release 066; 065 testbed run: 331 x
			// 409 BARRIER_ABORTED in one cell). A fresh token contacts the owner again; the
			// owner-uncertain back-off above keeps such retries rare.
			base := token
			for n := 1; ; n++ {
				prev, er := a.cfg.Store.GetBarrierLifecycle(dcur.ID, dcur.Generation, token)
				if errors.Is(er, journal.ErrNotFound) {
					break
				}
				if er != nil {
					return "", er
				}
				if prev.Status != "ABORTED" {
					break
				}
				token = fmt.Sprintf("%s-a%d", base, n)
			}
		}
		status, out := a.runBarrierLocked(protocol.BarrierRequest{SchemaVersion: 1, DomainID: dcur.ID, Generation: dcur.Generation, Token: token})
		if a.cfg.Trace != nil {
			raw, _ := json.Marshal(out)
			_ = a.cfg.Trace.Write(trace.Event{DomainID: dcur.ID, CommandID: origin, Event: "EVICON_FENCE_RESULT", Policy: string(a.cfg.Policy), Outcome: fmt.Sprint(status), Reason: truncate(string(raw), 400)})
		}
		if status/100 != 2 {
			return fmt.Sprintf("EVICON_FENCE_FAILED_%d", status), nil
		}
		return "", nil
	case contract.ActionEscalate:
		return "EVICON_NO_BACKWARD_EVIDENCE", nil
	default:
		return "EVICON_WAIT:" + d.Reason, nil
	}
}

// recentlyQueried: a query in the last AmbiguityAfter window counts as done,
// so the resolver moves on to backward actions instead of re-querying.
func (a *Authority) recentlyQueried(id string) bool {
	a.queriedMu.Lock()
	defer a.queriedMu.Unlock()
	t, ok := a.queriedAt[id]
	return ok && time.Since(t) < a.cfg.AmbiguityAfter
}

// OwnerUncertainRetry is how long backward resolution leaves an identity alone
// after the owner answered UNCERTAIN for it (stop or fence).
const OwnerUncertainRetry = 30 * time.Second

func (a *Authority) markOwnerUncertain(ids ...string) {
	a.queriedMu.Lock()
	defer a.queriedMu.Unlock()
	if a.ownerUncertain == nil {
		a.ownerUncertain = map[string]time.Time{}
	}
	for _, id := range ids {
		a.ownerUncertain[id] = time.Now()
	}
}

func (a *Authority) isOwnerUncertain(id string) bool {
	a.queriedMu.Lock()
	defer a.queriedMu.Unlock()
	t, ok := a.ownerUncertain[id]
	return ok && time.Since(t) < OwnerUncertainRetry
}

// recentlyStopped: a stop in the last AmbiguityAfter window counts as issued
// (an UNCERTAIN stop reply is not retried in a tight loop).
func (a *Authority) recentlyStopped(id string) bool {
	a.queriedMu.Lock()
	defer a.queriedMu.Unlock()
	t, ok := a.stoppedAt[id]
	return ok && time.Since(t) < a.cfg.AmbiguityAfter
}

// queryDirect reads the owner's receipt for one command and records it only if
// it validates and the evidence contract admits it on the QUERY channel.
func (a *Authority) queryDirect(origin string, cmd journal.CommandRecord, stats *policyStats) error {
	stats.ProbeCount++
	a.queriedMu.Lock()
	if a.queriedAt == nil {
		a.queriedAt = map[string]time.Time{}
	}
	a.queriedAt[cmd.CommandID] = time.Now()
	a.queriedMu.Unlock()
	start := time.Now()
	rc, _, _, _, err := a.fetchReceiptMeasured(cmd)
	elapsed := time.Since(start).Microseconds()
	stats.ProbeCostUS += elapsed
	outcome, reason := "UNCERTAIN", "QUERY_TRANSPORT_ERROR"
	if err == nil {
		outcome, reason = rc.Outcome, rc.Reason
		// Release 072, rule (A4): the age is taken when the query was SENT, a
		// lower bound on when the owner answered, and from the command's
		// creation, which is the anchor of the expires_at_ms deadline that the
		// dispatcher sends to the owner. Before 072 it was taken when the reply
		// arrived, which overestimates it.
		age := int64(0)
		if times, er := a.cfg.Store.CommandCreatedTimes(cmd.DomainID); er == nil {
			if created, ok := times[cmd.CommandID]; ok {
				age = start.Sub(created).Milliseconds()
			}
		}
		if verr := validateReceipt(cmd, rc, a.cfg.Domain.Dimension); verr != nil {
			outcome, reason = "INVALID_EVIDENCE", verr.Error()
		} else if adm := a.cfg.EvidenceContract.Admit(contract.Outcome(rc.Outcome), contract.ChannelQuery, age); adm.Admissible && (rc.Outcome == "APPLIED" || rc.Outcome == "STOPPED") {
			rev, er := a.cfg.Store.RecordEvidence(journal.ReceiptRecord{DomainID: rc.DomainID, CommandID: rc.CommandID, IntentHash: rc.IntentHash, Outcome: rc.Outcome, OwnerVersion: rc.OwnerVersion, ReceiptVersion: rc.ReceiptVersion, State: rc.State}, rc.Reason, "POLICY_QUERY", true)
			if errors.Is(er, journal.ErrTerminalReversal) {
				a.noteEvidenceConflict(cmd.DomainID, cmd.CommandID, "POLICY_QUERY", cmd.Status, rc.Outcome, er.Error())
				outcome, reason = "EVIDENCE_CONFLICT", er.Error()
			} else if er != nil && !errors.Is(er, journal.ErrNotFound) {
				return er
			} else if a.cfg.Trace != nil {
				_ = a.cfg.Trace.Write(trace.Event{DomainID: cmd.DomainID, CommandID: cmd.CommandID, Event: "EVIDENCE_COMMITTED", Revision: rev, Outcome: rc.Outcome, Reason: "POLICY_QUERY:" + rc.Outcome})
			}
		} else if !adm.Admissible && (rc.Outcome == "APPLIED" || rc.Outcome == "STOPPED") {
			reason = "QUERY_NOT_ADMISSIBLE:" + adm.Reason
		}
	} else {
		reason = err.Error()
	}
	if a.cfg.Trace != nil {
		_ = a.cfg.Trace.Write(trace.Event{DomainID: cmd.DomainID, CommandID: origin, Event: "EVICON_QUERY_RESULT", Policy: string(a.cfg.Policy), QueryCommandID: cmd.CommandID, ActualCostUS: elapsed, Outcome: outcome, Reason: truncate(reason, 200)})
	}
	return nil
}

// SweepOnce resolves aged ambiguous reservations without waiting for a
// blocked candidate, so clients learn terminal outcomes (and can reissue) even
// when no new command arrives. Same contract, same actions, same threshold.
func (a *Authority) SweepOnce() error {
	if a.cfg.Policy != probe.EVICON {
		return nil
	}
	a.mu.Lock()
	defer a.mu.Unlock()
	dcur, err := a.cfg.Store.GetDomain(a.cfg.Domain.ID)
	if err != nil {
		return err
	}
	// A pending barrier pauses admission until it completes or aborts. Drive it
	// to an end first, whatever the current targets are (the owner may have
	// been unreachable when it started).
	if pb, ok, err := a.cfg.Store.PendingBarrier(dcur.ID); err != nil {
		return err
	} else if ok {
		status, out := a.runBarrierLocked(protocol.BarrierRequest{SchemaVersion: 1, DomainID: dcur.ID, Generation: dcur.Generation, Token: pb.Token})
		if a.cfg.Trace != nil {
			raw, _ := json.Marshal(out)
			_ = a.cfg.Trace.Write(trace.Event{DomainID: dcur.ID, Event: "EVICON_FENCE_RESULT", Policy: string(a.cfg.Policy), Outcome: fmt.Sprint(status),
				Reason: truncate("retry:"+pb.Token+":"+string(raw), 400)})
		}
		return nil
	}
	cmds, err := a.cfg.Store.ListCommandsForBelief(dcur.ID, dcur.CutoffSequence)
	if err != nil {
		return err
	}
	unresolved := probe.Unresolved(cmds, nil)
	if len(unresolved) == 0 {
		return nil
	}
	created, err := a.cfg.Store.CommandCreatedTimes(dcur.ID)
	if err != nil {
		return err
	}
	targets := map[string]bool{}
	now := time.Now()
	for _, u := range unresolved {
		if now.Sub(created[u.CommandID]) >= a.cfg.AmbiguityAfter {
			targets[u.CommandID] = true
		}
	}
	if len(targets) == 0 {
		return nil
	}
	stats := policyStats{}
	_, err = a.resolveLocked(dcur, "", modeSweep, unresolved, targets, created, &stats)
	return err
}

// RunSweep calls SweepOnce every interval until ctx is done.
func (a *Authority) RunSweep(ctx context.Context, interval time.Duration) {
	if interval <= 0 || a.cfg.Policy != probe.EVICON {
		return
	}
	t := time.NewTicker(interval)
	defer t.Stop()
	for {
		select {
		case <-ctx.Done():
			return
		case <-t.C:
			if err := a.SweepOnce(); err != nil && a.cfg.Trace != nil {
				_ = a.cfg.Trace.Write(trace.Event{DomainID: a.cfg.Domain.ID, Event: "EVICON_SWEEP_ERROR", Reason: truncate(err.Error(), 200)})
			}
		}
	}
}

func truncate(s string, n int) string {
	if len(s) <= n {
		return s
	}
	return s[:n]
}

// stopOne issues a per-identity stop (Q_S) to the endpoint and records the
// returned terminal receipt if, and only if, it validates and the evidence
// contract admits it on the STOP channel.
func (a *Authority) stopOne(origin string, cmd journal.CommandRecord, stats *policyStats) error {
	stats.ProbeCount++ // a stop consumes the same action budget as a query
	if !a.cfg.EvidenceContract.Has(contract.CapStop) {
		return fmt.Errorf("stop requested but contract %s has no Q_S", a.cfg.EvidenceContract.ID)
	}
	a.queriedMu.Lock()
	if a.stoppedAt == nil {
		a.stoppedAt = map[string]time.Time{}
	}
	a.stoppedAt[cmd.CommandID] = time.Now()
	a.queriedMu.Unlock()
	body, _ := json.Marshal(protocol.StopRequest{SchemaVersion: 1, DomainID: cmd.DomainID, Generation: cmd.Generation, CommandID: cmd.CommandID, IntentHash: cmd.IntentHash, Sequence: cmd.Sequence})
	u := strings.TrimRight(a.cfg.EndpointURL, "/") + "/v1/stop"
	start := time.Now()
	resp, err := a.hc.Post(u, "application/json", bytes.NewReader(body))
	elapsed := time.Since(start).Microseconds()
	stats.ProbeCostUS += elapsed
	outcome, reason := "UNCERTAIN", "STOP_TRANSPORT_ERROR"
	if err == nil {
		raw, _ := io.ReadAll(io.LimitReader(resp.Body, 1<<20))
		resp.Body.Close()
		if resp.StatusCode/100 != 2 {
			reason = fmt.Sprintf("STOP_HTTP_%d", resp.StatusCode)
		} else {
			var rc protocol.Receipt
			if err := json.Unmarshal(raw, &rc); err != nil {
				reason = "STOP_MALFORMED_RECEIPT"
			} else if err := validateReceipt(cmd, rc, a.cfg.Domain.Dimension); err != nil {
				reason = "STOP_INVALID_RECEIPT:" + err.Error()
			} else if rc.Outcome != "APPLIED" && rc.Outcome != "STOPPED" {
				outcome, reason = rc.Outcome, "STOP_NOT_TERMINAL:"+rc.Reason
				a.markOwnerUncertain(cmd.CommandID)
			} else if adm := a.cfg.EvidenceContract.Admit(contract.Outcome(rc.Outcome), contract.ChannelStop, 0); !adm.Admissible {
				reason = "STOP_NOT_ADMISSIBLE:" + adm.Reason
			} else {
				rev, er := a.cfg.Store.RecordEvidence(journal.ReceiptRecord{DomainID: rc.DomainID, CommandID: rc.CommandID, IntentHash: rc.IntentHash, Outcome: rc.Outcome, OwnerVersion: rc.OwnerVersion, ReceiptVersion: rc.ReceiptVersion, State: rc.State}, rc.Reason, "POLICY_STOP", true)
				if errors.Is(er, journal.ErrTerminalReversal) {
					a.noteEvidenceConflict(cmd.DomainID, cmd.CommandID, "POLICY_STOP", cmd.Status, rc.Outcome, er.Error())
					outcome, reason = "EVIDENCE_CONFLICT", er.Error()
				} else if er != nil && !errors.Is(er, journal.ErrNotFound) {
					return er
				} else {
					outcome, reason = rc.Outcome, rc.Reason
				}
				if er == nil && a.cfg.Trace != nil {
					_ = a.cfg.Trace.Write(trace.Event{DomainID: cmd.DomainID, CommandID: cmd.CommandID, Event: "EVIDENCE_COMMITTED", Revision: rev, Reason: "POLICY_STOP:" + rc.Outcome})
				}
			}
		}
	}
	if a.cfg.Trace != nil {
		_ = a.cfg.Trace.Write(trace.Event{DomainID: cmd.DomainID, CommandID: origin, Event: "EVICON_STOP_RESULT", Policy: string(a.cfg.Policy), QueryCommandID: cmd.CommandID, ActualCostUS: elapsed, Outcome: outcome, Reason: reason})
	}
	return nil
}

// RetryAllowedFunc returns the outbox guard implementing Theorem T4 for the
// authority's evidence contract: a same-identity re-dispatch is allowed only
// while the contract's forward-retry deadline has not passed. For the EviCon
// E1/E2/E3 profiles the dedup horizon is unbounded and every retry is allowed.
func RetryAllowedFunc(store *journal.Store, c contract.EvidenceContract, maxDelay time.Duration) func(domainID, commandID string, attempts int64) bool {
	return RetryAllowedUntilFunc(store, c, maxDelay, 0)
}

// RetryAllowedUntilFunc (release 077) is RetryAllowedFunc with the intent's validity: when validity > 0,
// a command is resent only until created + validity (minus the one-way delay margin when the delay is
// bounded), the rule of Eq. (4) and of the simulator. A resend after that could only take effect late.
// validity <= 0 keeps the rule of releases 072-076 (no validity cut).
func RetryAllowedUntilFunc(store *journal.Store, c contract.EvidenceContract, maxDelay, validity time.Duration) func(domainID, commandID string, attempts int64) bool {
	return func(domainID, commandID string, attempts int64) bool {
		if attempts == 0 {
			return true
		}
		times, err := store.CommandCreatedTimes(domainID)
		if err != nil {
			return false
		}
		t0, ok := times[commandID]
		if !ok {
			// Release 072: a command that is no longer unresolved (terminal) is never sent
			// again. Up to 071 this returned true ("terminal commands are not dispatched
			// anyway"), but the dispatcher worked on a snapshot and re-sent a command whose
			// APPLIED evidence a stop had just committed; without dedup it took effect twice.
			return false
		}
		d := maxDelay.Milliseconds()
		if maxDelay < 0 {
			d = -1
		}
		tm := contract.CommandTiming{FirstSendMS: t0.UnixMilli(), NowMS: time.Now().UnixMilli(), MaxNetworkDelayMS: d}
		if validity > 0 {
			tm.IntentValidUntilMS = t0.Add(validity).UnixMilli()
		}
		return c.Compile(tm).Allows(contract.ActionForwardRetry)
	}
}

var _ = http.MethodPost
