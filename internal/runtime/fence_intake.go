package runtime

// Release 084 (rule R5', PREREG_extra_084.md (a)): per-identity intake of a complete fence answer.
//
// Rule R5 (releases 062-083, the default FenceIntakeAllOrNothing) aborts a fence whose answer lists any
// identity the owner cannot certify (UNCERTAIN, e.g. a crash orphan): it un-pauses the domain, seals
// nothing and anchors nothing. That is a conservative choice of the release, not a requirement of the
// contract: premise FC says the answer lists every identity at or below the cutoff that has taken effect
// or may have, and the cutoff is installed before the answer, so
//   - a listed terminal answer (APPLIED or STOPPED) is owner evidence about that identity alone;
//   - an unlisted identity at or below the cutoff has not taken effect and never will (STOPPED);
// whatever the answer says about the other identities. With FenceIntakePerIdentity (rule R5',
// `evicon-authority -fence-intake per-identity`, policy EVICON_FI) the authority takes in the listed
// terminal answers through the normal fence intake, seals the unlisted identities as STOPPED, keeps every
// identity listed UNCERTAIN pending (its reservation and window slot kept) and marks it owner-uncertain;
// it anchors nothing (an identity at or below the cutoff is still unresolved) and un-pauses the domain as
// on completion. Evidence conflicts and invalid receipts abort exactly as under R5 (runBarrierLocked
// checks them first). An answer without UNCERTAIN identities completes exactly as under R5 (anchor and
// seal), so R5' differs from R5 only on the answers R5 aborts.

import (
	"errors"
	"strings"
	"time"

	"evicon/internal/journal"
	"evicon/internal/protocol"
	"evicon/internal/trace"
)

// commitPerIdentityLocked ends the PENDING barrier br with the per-identity intake of its complete answer
// rc: recs are the validated terminal receipts, uncertain the identities listed UNCERTAIN. Caller holds a.mu.
func (a *Authority) commitPerIdentityLocked(br protocol.BarrierRequest, d journal.Domain, rc protocol.BarrierReceipt, recs []journal.ReceiptRecord, uncertain []string) (int, any) {
	rev, sealed, err := a.cfg.Store.CommitBarrierPerIdentity(br.DomainID, br.Token, d.Generation, rc.CutoffSequence, rc.State, rc.OwnerVersion, recs, uncertain)
	if errors.Is(err, journal.ErrTerminalReversal) {
		// a terminal outcome committed concurrently (dispatch reply) contradicts the owner
		return a.barrierConflict(br, d.Generation, []evidenceConflict{{Detail: err.Error()}})
	}
	if errors.Is(err, journal.ErrBarrierPartial) {
		return 409, map[string]any{"error": "BARRIER_PARTIAL"}
	}
	if errors.Is(err, journal.ErrBarrierAlreadyComplete) {
		prev, _ := a.cfg.Store.GetBarrierLifecycle(br.DomainID, d.Generation, br.Token)
		return 200, map[string]any{"barrier": protocol.BarrierReceipt{SchemaVersion: 1, DomainID: prev.DomainID, Generation: prev.Generation, Token: prev.Token, CutoffSequence: prev.CutoffSequence, State: prev.Anchor, OwnerVersion: prev.OwnerVersion, Reason: "AUTHORITY_BARRIER_REPLAY"}, "revision": prev.CompleteRevision}
	}
	if err != nil {
		return 500, map[string]any{"error": err.Error()}
	}
	a.markOwnerUncertain(uncertain...)
	a.noteFenceUncertain(uncertain...)
	if a.cfg.Trace != nil {
		for _, r := range recs {
			_ = a.cfg.Trace.Write(trace.Event{DomainID: br.DomainID, CommandID: r.CommandID, Event: "EVIDENCE_COMMITTED", Revision: rev, Outcome: r.Outcome, Reason: "BARRIER_RECEIPT:" + br.Token})
		}
		for _, cid := range sealed {
			_ = a.cfg.Trace.Write(trace.Event{DomainID: br.DomainID, CommandID: cid, Event: "EVIDENCE_COMMITTED", Revision: rev, Outcome: "STOPPED", Reason: "BARRIER_SEALED_UNCLAIMED:" + br.Token})
		}
		_ = a.cfg.Trace.Write(trace.Event{DomainID: br.DomainID, Event: "BARRIER_PER_IDENTITY_COMMITTED", Revision: rev,
			Reason: truncate(br.Token+":OWNER_UNCERTAIN:"+strings.Join(uncertain, ","), 400)})
	}
	return 200, map[string]any{"barrier": rc, "revision": rev, "intake": "PER_IDENTITY", "terminal": len(recs), "sealed": len(sealed), "uncertain": uncertain}
}

func (a *Authority) noteFenceUncertain(ids ...string) {
	a.queriedMu.Lock()
	defer a.queriedMu.Unlock()
	if a.fenceUncertain == nil {
		a.fenceUncertain = map[string]time.Time{}
	}
	for _, id := range ids {
		if _, seen := a.fenceUncertain[id]; !seen {
			a.fenceUncertain[id] = time.Now()
		}
	}
}

// isFenceUncertain: under FenceIntakePerIdentity, a complete fence answer listed id UNCERTAIN.
func (a *Authority) isFenceUncertain(id string) bool {
	if a.cfg.FenceIntake != FenceIntakePerIdentity {
		return false
	}
	a.queriedMu.Lock()
	defer a.queriedMu.Unlock()
	_, ok := a.fenceUncertain[id]
	return ok
}

// excludedFromTargets: identities the resolver leaves alone. Owner-uncertain (a stop or fence answered
// UNCERTAIN in the last OwnerUncertainRetry; every policy, as before) and, under per-identity intake,
// listed UNCERTAIN by a complete fence answer (for the authority's lifetime).
func (a *Authority) excludedFromTargets(id string) bool {
	return a.isOwnerUncertain(id) || a.isFenceUncertain(id)
}

// fenceCoversOnlyUncertain (rule R5'): every unresolved identity (a full-frontier fence covers all of them)
// is excluded from targets, so a fence could certify nothing new and is not sent.
func (a *Authority) fenceCoversOnlyUncertain(unresolved []journal.CommandRecord) bool {
	for _, u := range unresolved {
		if !a.excludedFromTargets(u.CommandID) {
			return false
		}
	}
	return true
}
