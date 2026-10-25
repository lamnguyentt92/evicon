package runtime

// The two release baselines of release 077 (PREREG_review_077.md). UNSAFE BY DESIGN.
//
// RELEASE_ON_TIMEOUT models a common client practice: a command that times out is treated as
// failed. Every command that is still unresolved ReleaseAfter after its creation is committed
// STOPPED without any evidence from the owner, which breaks rule R2 of the EviCon protocol on
// purpose: the reservation is released, the client sees STOPPED and re-issues the intent under a
// fresh identity, and the original request may still take effect (a late delivery from a queue,
// or an effect whose reply was lost).
//
// RELEASE_ON_READ models state-read reconciliation (a device shadow): ReleaseAfter after its
// creation, a command without terminal evidence is looked up at the owner. A valid APPLIED or
// STOPPED receipt is recorded as the owner's answer. A command the owner does not report, because
// it answers NOT_FOUND or cannot be reached (its reported state then does not show the effect), is
// treated as failed and committed STOPPED: absence of evidence of application is taken for
// evidence of non-occurrence, which no contract without a stop, a fence or an owner expiry
// supports. An UNCERTAIN answer (a claim without a receipt after an owner crash) is read again on
// the next sweep.
//
// In both, the admission guard is unchanged; only the evidence rule is broken. The checker then
// counts false STOPPED, logical duplicates and bound violations. This file is the only place that
// commits terminal knowledge without an owner answer.

import (
	"context"
	"errors"
	"math"
	"sort"
	"sync"
	"time"

	"evicon/internal/journal"
	"evicon/internal/probe"
	"evicon/internal/trace"
)

// MaxReadsPerSweep bounds the owner reads of one RELEASE_ON_READ sweep (oldest commands first);
// the rest wait for the next sweep.
const MaxReadsPerSweep = 16

// ReleaseSweepOnce runs one sweep of the policy's release baseline and returns the number of
// commands committed STOPPED without owner evidence.
func (a *Authority) ReleaseSweepOnce() (int, error) {
	switch a.cfg.Policy {
	case probe.ReleaseOnTimeout:
		return a.releaseOnTimeoutOnce()
	case probe.ReleaseOnRead:
		return a.releaseOnReadOnce()
	}
	return 0, nil
}

// agedUnresolved returns the unresolved commands older than ReleaseAfter, oldest first.
// The caller holds a.mu.
func (a *Authority) agedUnresolved() (journal.Domain, []journal.CommandRecord, error) {
	dcur, err := a.cfg.Store.GetDomain(a.cfg.Domain.ID)
	if err != nil {
		return dcur, nil, err
	}
	cmds, err := a.cfg.Store.ListCommandsForBelief(dcur.ID, dcur.CutoffSequence)
	if err != nil {
		return dcur, nil, err
	}
	unresolved := probe.Unresolved(cmds, nil)
	if len(unresolved) == 0 {
		return dcur, nil, nil
	}
	created, err := a.cfg.Store.CommandCreatedTimes(dcur.ID)
	if err != nil {
		return dcur, nil, err
	}
	now := time.Now()
	var out []journal.CommandRecord
	for _, u := range unresolved {
		if t0, ok := created[u.CommandID]; ok && now.Sub(t0) >= a.cfg.ReleaseAfter {
			out = append(out, u)
		}
	}
	sort.SliceStable(out, func(i, j int) bool { return created[out[i].CommandID].Before(created[out[j].CommandID]) })
	return dcur, out, nil
}

// releaseWithoutEvidence commits a synthetic STOPPED receipt (no owner answered). The versions
// only satisfy the journal's format checks (a later owner receipt is then refused as stale or as
// a reversal, which the baseline would not look at either). The caller holds a.mu.
func (a *Authority) releaseWithoutEvidence(u journal.CommandRecord, dim int, reason, event string) (bool, error) {
	rec := journal.ReceiptRecord{DomainID: u.DomainID, CommandID: u.CommandID, IntentHash: u.IntentHash, Outcome: "STOPPED",
		OwnerVersion: 1, ReceiptVersion: math.MaxInt32, State: make([]int64, dim)}
	rev, er := a.cfg.Store.RecordEvidence(rec, reason, "POLICY_"+string(a.cfg.Policy), true)
	if errors.Is(er, journal.ErrTerminalReversal) || errors.Is(er, journal.ErrNotFound) {
		return false, nil // became terminal meanwhile
	}
	if er != nil {
		return false, er
	}
	if a.cfg.Trace != nil {
		_ = a.cfg.Trace.Write(trace.Event{DomainID: u.DomainID, CommandID: u.CommandID, Event: event, Revision: rev,
			Policy: string(a.cfg.Policy), Reason: "STOPPED_WITHOUT_OWNER_EVIDENCE:" + reason})
	}
	return true, nil
}

func (a *Authority) releaseOnTimeoutOnce() (int, error) {
	a.mu.Lock()
	defer a.mu.Unlock()
	dcur, aged, err := a.agedUnresolved()
	if err != nil {
		return 0, err
	}
	n := 0
	for _, u := range aged {
		ok, er := a.releaseWithoutEvidence(u, dcur.Dimension, "RELEASE_ON_TIMEOUT_NO_EVIDENCE", "RELEASE_ON_TIMEOUT")
		if er != nil {
			return n, er
		}
		if ok {
			n++
		}
	}
	return n, nil
}

type ownerRead struct {
	cmd     journal.CommandRecord
	outcome string // APPLIED, STOPPED (valid owner receipt), NOT_FOUND, UNREACHABLE, UNCERTAIN
	reason  string
	rc      journal.ReceiptRecord
}

func (a *Authority) releaseOnReadOnce() (int, error) {
	a.mu.Lock()
	dcur, aged, err := a.agedUnresolved()
	a.mu.Unlock()
	if err != nil || len(aged) == 0 {
		return 0, err
	}
	if len(aged) > MaxReadsPerSweep {
		aged = aged[:MaxReadsPerSweep]
	}
	// The reads run without the authority's lock (an unreachable owner costs up to the HTTP
	// client's timeout); RecordEvidence refuses a command that became terminal meanwhile.
	reads := make([]ownerRead, len(aged))
	var wg sync.WaitGroup
	sem := make(chan struct{}, 4)
	for i, c := range aged {
		wg.Add(1)
		go func(i int, c journal.CommandRecord) {
			defer wg.Done()
			sem <- struct{}{}
			defer func() { <-sem }()
			reads[i] = a.readOwner(c, dcur.Dimension)
		}(i, c)
	}
	wg.Wait()
	a.mu.Lock()
	defer a.mu.Unlock()
	n := 0
	for _, r := range reads {
		if a.cfg.Trace != nil {
			_ = a.cfg.Trace.Write(trace.Event{DomainID: r.cmd.DomainID, CommandID: r.cmd.CommandID, Event: "RELEASE_ON_READ_RESULT",
				Policy: string(a.cfg.Policy), Outcome: r.outcome, Reason: truncate(r.reason, 200)})
		}
		switch r.outcome {
		case "APPLIED", "STOPPED":
			rev, er := a.cfg.Store.RecordEvidence(r.rc, r.reason, "POLICY_READ", true)
			if errors.Is(er, journal.ErrTerminalReversal) {
				a.noteEvidenceConflict(r.cmd.DomainID, r.cmd.CommandID, "POLICY_READ", r.cmd.Status, r.outcome, er.Error())
				continue
			}
			if er != nil && !errors.Is(er, journal.ErrNotFound) {
				return n, er
			}
			if er == nil && a.cfg.Trace != nil {
				_ = a.cfg.Trace.Write(trace.Event{DomainID: r.cmd.DomainID, CommandID: r.cmd.CommandID, Event: "EVIDENCE_COMMITTED", Revision: rev,
					Outcome: r.outcome, Reason: "POLICY_READ:" + r.outcome})
			}
		case "NOT_FOUND", "UNREACHABLE":
			ok, er := a.releaseWithoutEvidence(r.cmd, dcur.Dimension, "RELEASE_ON_READ_NOT_REPORTED_"+r.outcome, "RELEASE_ON_READ")
			if er != nil {
				return n, er
			}
			if ok {
				n++
			}
		}
	}
	return n, nil
}

// readOwner reads the owner's receipt of one command (the baseline's "reported state").
func (a *Authority) readOwner(c journal.CommandRecord, dim int) ownerRead {
	rc, status, _, _, err := a.fetchReceiptMeasured(c)
	if err != nil {
		// The owner (or the path to it) cannot be reached, or refuses the read: its reported state
		// does not show the effect.
		return ownerRead{cmd: c, outcome: "UNREACHABLE", reason: truncate(err.Error(), 120)}
	}
	if status == 404 || rc.Outcome == "NOT_FOUND" {
		return ownerRead{cmd: c, outcome: "NOT_FOUND", reason: "NO_OWNER_RECORD"}
	}
	if verr := validateReceipt(c, rc, dim); verr != nil {
		return ownerRead{cmd: c, outcome: "UNCERTAIN", reason: "INVALID_EVIDENCE:" + verr.Error()}
	}
	if rc.Outcome != "APPLIED" && rc.Outcome != "STOPPED" {
		return ownerRead{cmd: c, outcome: "UNCERTAIN", reason: rc.Outcome + ":" + rc.Reason}
	}
	return ownerRead{cmd: c, outcome: rc.Outcome, reason: rc.Reason,
		rc: journal.ReceiptRecord{DomainID: rc.DomainID, CommandID: rc.CommandID, IntentHash: rc.IntentHash, Outcome: rc.Outcome,
			OwnerVersion: rc.OwnerVersion, ReceiptVersion: rc.ReceiptVersion, State: rc.State}}
}

// RunReleaseSweep calls ReleaseSweepOnce every interval until ctx is done.
func (a *Authority) RunReleaseSweep(ctx context.Context, interval time.Duration) {
	if interval <= 0 || !probe.IsReleaseBaseline(a.cfg.Policy) {
		return
	}
	t := time.NewTicker(interval)
	defer t.Stop()
	for {
		select {
		case <-ctx.Done():
			return
		case <-t.C:
			if _, err := a.ReleaseSweepOnce(); err != nil && a.cfg.Trace != nil {
				_ = a.cfg.Trace.Write(trace.Event{DomainID: a.cfg.Domain.ID, Event: "RELEASE_SWEEP_ERROR", Reason: truncate(err.Error(), 200)})
			}
		}
	}
}
