package adapter

import (
	"testing"

	"evicon/internal/contract"
	"evicon/internal/protocol"
)

func stopReq(t *testing.T, id string, v int64, seq uint64) (protocol.CommandRequest, protocol.StopRequest) {
	t.Helper()
	r := e12req(id, v, seq)
	if err := r.ValidateAndFillHash(1); err != nil {
		t.Fatal(err)
	}
	return r, protocol.StopRequest{SchemaVersion: 1, DomainID: "d", Generation: 1, CommandID: id, IntentHash: r.IntentHash, Sequence: seq}
}

// F21: a stop for an identity that never reached the owner records a durable
// tombstone; the delayed request later arrives and must not take effect.
func TestF21StopTombstoneBlocksLateArrival(t *testing.T) {
	o := newE12(t, "E2")
	cr, sr := stopReq(t, "late", 3, 1)
	rc, err := o.Stop(sr)
	if err != nil || rc.Outcome != "STOPPED" || rc.Reason != "STOP_TOMBSTONE" {
		t.Fatalf("stop: %#v %v", rc, err)
	}
	if err := protocol.ValidateReceipt(rc, "d", 1, "late", cr.IntentHash, 1); err != nil {
		t.Fatalf("stop receipt must validate as terminal evidence: %v", err)
	}
	late, err := o.Apply(cr)
	if err != nil || late.Outcome != "STOPPED" {
		t.Fatalf("late arrival must be STOPPED: %#v %v", late, err)
	}
	s, effects, _, _, _, _ := o.Snapshot()
	if effects != 0 || s[0] != 0 {
		t.Fatalf("no effect allowed after tombstone: state=%v effects=%d", s, effects)
	}
}

// F22: crash after the effect committed but before the receipt; the claim is
// left without a terminal receipt. Stop must reconcile to APPLIED (never
// report STOPPED for an applied identity).
func TestF22StopReconcilesClaimWithEffectToApplied(t *testing.T) {
	o := newE12(t, "E2")
	cr, sr := stopReq(t, "x", 4, 1)
	if _, err := o.Apply(cr); err != nil {
		t.Fatal(err)
	}
	// Simulate the crash window: the receipt was never written.
	if _, err := o.claims.Exec(`UPDATE claims SET status='CLAIMED',receipt_version=0,owner_version=0,state_json='[]',reason='CLAIMED' WHERE domain_id='d' AND command_id='x'`); err != nil {
		t.Fatal(err)
	}
	q, _ := o.GetReceipt("d", "x")
	if q.Outcome != "UNCERTAIN" {
		t.Fatalf("precondition: query must be UNCERTAIN, got %s", q.Outcome)
	}
	rc, err := o.Stop(sr)
	if err != nil || rc.Outcome != "APPLIED" || rc.Reason != "STOP_RECONCILED_APPLIED" {
		t.Fatalf("stop must reconcile to APPLIED: %#v %v", rc, err)
	}
	if len(rc.State) != 1 || rc.State[0] != 4 {
		t.Fatalf("receipt state %v", rc.State)
	}
}

// F22b: claim without effect (crash after claim, before effect): no replay
// after crash, so stop confirms STOPPED and a retry cannot apply.
func TestF22bStopConfirmsClaimWithoutEffect(t *testing.T) {
	o := newE12(t, "E2")
	cr, sr := stopReq(t, "y", 2, 1)
	if err := o.insertClaim(cr); err != nil {
		t.Fatal(err)
	}
	rc, err := o.Stop(sr)
	if err != nil || rc.Outcome != "STOPPED" || rc.Reason != "STOP_CONFIRMED_NOT_EFFECTED" {
		t.Fatalf("%#v %v", rc, err)
	}
	again, err := o.Apply(cr)
	if err != nil || again.Outcome != "STOPPED" {
		t.Fatalf("retry after stop must stay STOPPED: %#v %v", again, err)
	}
	_, effects, _, _, _, _ := o.Snapshot()
	if effects != 0 {
		t.Fatal("no effect allowed")
	}
}

// F23: stop is idempotent; a different intent hash is an identity conflict;
// stop is unsupported on E1; stop after a normal APPLIED returns APPLIED.
func TestF23StopIdempotenceAndIdentity(t *testing.T) {
	o := newE12(t, "E2")
	_, sr := stopReq(t, "z", 1, 1)
	a, err := o.Stop(sr)
	if err != nil {
		t.Fatal(err)
	}
	b, err := o.Stop(sr)
	if err != nil || b.Outcome != a.Outcome || b.ReceiptVersion != a.ReceiptVersion || b.OwnerVersion != a.OwnerVersion {
		t.Fatalf("stop must be idempotent: %#v vs %#v (%v)", a, b, err)
	}
	bad := sr
	bad.IntentHash = "deadbeef"
	if _, err := o.Stop(bad); !IsIdentityConflict(err) {
		t.Fatalf("expected identity conflict, got %v", err)
	}
	cr2, sr2 := stopReq(t, "w", 2, 2)
	if _, err := o.Apply(cr2); err != nil {
		t.Fatal(err)
	}
	rc, err := o.Stop(sr2)
	if err != nil || rc.Outcome != "APPLIED" {
		t.Fatalf("stop after APPLIED returns the receipt: %#v %v", rc, err)
	}
	e1 := newE12(t, "E1")
	if _, err := e1.Stop(sr); err == nil {
		t.Fatal("E1 must not offer a per-identity stop")
	}
	bad2 := sr
	bad2.Sequence = 0
	if _, err := o.Stop(bad2); err == nil {
		t.Fatal("sequence 0 must be rejected")
	}
}

// F24: crash after the tombstone claim but before the terminal receipt; the
// claim stays CLAIMED without effect. A retried stop confirms STOPPED, and the
// late request is never applied.
func TestF24CrashDuringStopIsSafe(t *testing.T) {
	o := newE12(t, "E2")
	cr, sr := stopReq(t, "c", 3, 1)
	if err := o.insertClaim(cr); err != nil { // state after the simulated crash
		t.Fatal(err)
	}
	late, err := o.Apply(cr)
	if err != nil || late.Outcome != "UNCERTAIN" {
		t.Fatalf("late arrival during crash window must not apply: %#v %v", late, err)
	}
	rc, err := o.Stop(sr)
	if err != nil || rc.Outcome != "STOPPED" {
		t.Fatalf("%#v %v", rc, err)
	}
	_, effects, _, _, _, _ := o.Snapshot()
	if effects != 0 {
		t.Fatal("no effect allowed")
	}
}

// The 061 E2 stop profile grants Q_S only through verified premises.
func TestE2StopProfileGrantsQS(t *testing.T) {
	declared, _ := contract.EvidenceContractForProfile("E2")
	rc, err := contract.RuntimeContract("e2", declared, E2StopProfile())
	if err != nil || !rc.Has(contract.CapStop) || !rc.Has(contract.CapFence) {
		t.Fatalf("%v %v", rc.Capabilities, err)
	}
	legacy, err := contract.RuntimeContract("e2-legacy", declared, P2E2Profile())
	if err != nil || legacy.Has(contract.CapStop) {
		t.Fatalf("legacy E2 must not have Q_S: %v %v", legacy.Capabilities, err)
	}
}
