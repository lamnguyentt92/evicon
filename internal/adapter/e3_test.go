package adapter

import (
	"errors"
	"path/filepath"
	"testing"

	"evicon/internal/domain"
	"evicon/internal/protocol"
)

func newOwner(t *testing.T) *E3Owner {
	t.Helper()
	max := int64(12)
	o, err := OpenE3(E3Config{DBPath: filepath.Join(t.TempDir(), "endpoint.db"), DomainID: "d", Generation: 1, InstalledEpoch: 1, InitialState: []int64{0}, Bound: domain.HardBound{ComponentMin: []int64{0}, ComponentMax: []int64{12}, SumMax: &max}})
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = o.Close() })
	return o
}
func req(id string, op domain.Operation, value int64, seq uint64) protocol.CommandRequest {
	return protocol.CommandRequest{SchemaVersion: 1, DomainID: "d", Generation: 1, CommandID: id, Operation: op, Value: []int64{value}, Epoch: 1, Sequence: seq}
}
func TestE3IdempotentEffectReceiptAndConflict(t *testing.T) {
	o := newOwner(t)
	r := req("c", domain.OpAdd, 3, 1)
	a, err := o.Apply(r)
	if err != nil {
		t.Fatal(err)
	}
	b, err := o.Apply(r)
	if err != nil {
		t.Fatal(err)
	}
	if a.Outcome != "APPLIED" || b.Outcome != "APPLIED" || a.IntentHash != b.IntentHash {
		t.Fatalf("receipts %#v %#v", a, b)
	}
	state, effects, receipts, err := o.Snapshot()
	if err != nil {
		t.Fatal(err)
	}
	if state[0] != 3 || effects != 1 || receipts != 1 {
		t.Fatalf("state=%v effects=%d receipts=%d", state, effects, receipts)
	}
	bad := req("c", domain.OpAdd, 4, 1)
	if _, err := o.Apply(bad); !errors.Is(err, ErrIdentityConflict) {
		t.Fatalf("conflict err=%v", err)
	}
}
func TestE3SetFrontierRejectsStaleSet(t *testing.T) {
	o := newOwner(t)
	a, err := o.Apply(req("s2", domain.OpSet, 5, 2))
	if err != nil {
		t.Fatal(err)
	}
	if a.Outcome != "APPLIED" {
		t.Fatalf("%#v", a)
	}
	b, err := o.Apply(req("s1", domain.OpSet, 4, 1))
	if err != nil {
		t.Fatal(err)
	}
	if b.Outcome != "STOPPED" || b.Reason != "STALE_SET_SEQUENCE" {
		t.Fatalf("%#v", b)
	}
	state, effects, receipts, err := o.Snapshot()
	if err != nil {
		t.Fatal(err)
	}
	if state[0] != 5 || effects != 1 || receipts != 2 {
		t.Fatalf("state=%v effects=%d receipts=%d", state, effects, receipts)
	}
}

func TestE3PilotForceStopIsTerminalWithoutEffect(t *testing.T) {
	o := newOwner(t)
	r := req("reject", domain.OpAdd, 5, 1)
	rc, err := o.ForceStop(r, "PILOT_PREDETERMINED_REJECT")
	if err != nil {
		t.Fatal(err)
	}
	if rc.Outcome != "STOPPED" || rc.Reason != "PILOT_PREDETERMINED_REJECT" {
		t.Fatalf("%#v", rc)
	}
	state, effects, receipts, err := o.Snapshot()
	if err != nil {
		t.Fatal(err)
	}
	if state[0] != 0 || effects != 0 || receipts != 1 {
		t.Fatalf("state=%v effects=%d receipts=%d", state, effects, receipts)
	}
	replay, err := o.Apply(r)
	if err != nil {
		t.Fatal(err)
	}
	if replay.Outcome != "STOPPED" {
		t.Fatalf("replay=%#v", replay)
	}
}

func TestE3ReadStateReportsDurableOwnerVersion(t *testing.T) {
	o := newOwner(t)
	state, ov, cutoff, err := o.ReadState()
	if err != nil {
		t.Fatal(err)
	}
	if state[0] != 0 || ov != 0 || cutoff != 0 {
		t.Fatalf("initial state=%v owner_version=%d cutoff=%d", state, ov, cutoff)
	}
	if _, err := o.Apply(req("a", domain.OpAdd, 3, 1)); err != nil {
		t.Fatal(err)
	}
	state, ov, cutoff, err = o.ReadState()
	if err != nil {
		t.Fatal(err)
	}
	if state[0] != 3 || ov != 1 || cutoff != 0 {
		t.Fatalf("after effect state=%v owner_version=%d cutoff=%d", state, ov, cutoff)
	}
	if _, err := o.ForceStop(req("b", domain.OpAdd, 1, 2), "TEST_STOP"); err != nil {
		t.Fatal(err)
	}
	state, ov, cutoff, err = o.ReadState()
	if err != nil {
		t.Fatal(err)
	}
	if state[0] != 3 || ov != 2 || cutoff != 0 {
		t.Fatalf("after terminal stop state=%v owner_version=%d cutoff=%d", state, ov, cutoff)
	}
}
