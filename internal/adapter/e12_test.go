package adapter

import (
	"evicon/internal/domain"
	"evicon/internal/protocol"
	"evicon/internal/sqlitecgo"
	"path/filepath"
	"testing"
	"time"
)

func newE12(t *testing.T, profile string) *E12Owner {
	t.Helper()
	dir := t.TempDir()
	o, err := OpenE12(E12Config{Profile: profile, ClaimDBPath: filepath.Join(dir, "claims.db"), EffectsDBPath: filepath.Join(dir, "effects.db"), DomainID: "d", Generation: 1, InstalledEpoch: 1, InitialState: []int64{0}, Bound: domain.HardBound{ComponentMin: []int64{0}, ComponentMax: []int64{12}}})
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = o.Close() })
	return o
}
func e12req(id string, v int64, seq uint64) protocol.CommandRequest {
	return protocol.CommandRequest{SchemaVersion: 1, DomainID: "d", Generation: 1, CommandID: id, Operation: domain.OpAdd, Value: []int64{v}, Epoch: 1, Sequence: seq}
}
func TestE12IdempotentClaim(t *testing.T) {
	o := newE12(t, "E2")
	a, err := o.Apply(e12req("a", 3, 1))
	if err != nil {
		t.Fatal(err)
	}
	b, err := o.Apply(e12req("a", 3, 1))
	if err != nil {
		t.Fatal(err)
	}
	if a.Outcome != "APPLIED" || b.Outcome != "APPLIED" {
		t.Fatalf("%#v %#v", a, b)
	}
	s, e, c, term, _, err := o.Snapshot()
	if err != nil {
		t.Fatal(err)
	}
	if s[0] != 3 || e != 1 || c != 1 || term != 1 {
		t.Fatalf("state=%v effects=%d claims=%d term=%d", s, e, c, term)
	}
}
func TestE2BarrierStopsOldRequest(t *testing.T) {
	o := newE12(t, "E2")
	br, err := o.Barrier(protocol.BarrierRequest{SchemaVersion: 1, DomainID: "d", Generation: 1, Token: "b", CutoffSequence: 5})
	if err != nil {
		t.Fatal(err)
	}
	if br.CutoffSequence < 5 {
		t.Fatal(br)
	}
	rc, err := o.Apply(e12req("old", 3, 4))
	if err != nil {
		t.Fatal(err)
	}
	if rc.Outcome != "STOPPED" || rc.Reason != "SEALED_BY_BARRIER" {
		t.Fatalf("%#v", rc)
	}
}

func TestE2BarrierReplayKeepsOriginalAnchor(t *testing.T) {
	o := newE12(t, "E2")
	first, err := o.Barrier(protocol.BarrierRequest{SchemaVersion: 1, DomainID: "d", Generation: 1, Token: "stable", CutoffSequence: 1})
	if err != nil {
		t.Fatal(err)
	}
	if len(first.State) != 1 || first.State[0] != 0 {
		t.Fatalf("first=%#v", first)
	}
	if _, err = o.Apply(e12req("new", 3, 2)); err != nil {
		t.Fatal(err)
	}
	replay, err := o.Barrier(protocol.BarrierRequest{SchemaVersion: 1, DomainID: "d", Generation: 1, Token: "stable", CutoffSequence: 1})
	if err != nil {
		t.Fatal(err)
	}
	if len(replay.State) != 1 || replay.State[0] != 0 {
		t.Fatalf("barrier replay changed anchor: %#v", replay)
	}
}

func TestE2BarrierResumeDoesNotRecommitDurableCutoff(t *testing.T) {
	o := newE12(t, "E2")
	// Simulate the durable state left by a crash after the owner cutoff commit
	// but before the barrier reply/COMPLETE record.
	tm := time.Now().UTC().Format(time.RFC3339Nano)
	if _, err := o.claims.Exec(`INSERT INTO barriers(domain_id,token,cutoff_sequence,status,state_json,owner_version,created_at,updated_at) VALUES(?,?,?,'INSTALLING','[]',0,?,?)`, "d", "resume", 5, tm, tm); err != nil {
		t.Fatal(err)
	}
	if _, err := o.effects.Exec(`UPDATE domains SET cutoff_sequence=5,owner_version=7 WHERE domain_id='d'`); err != nil {
		t.Fatal(err)
	}
	before, err := o.effects.Query(`SELECT owner_version FROM domains WHERE domain_id='d'`)
	if err != nil {
		t.Fatal(err)
	}
	beforeOV, _ := sqlitecgo.Int64At(before[0], 0)
	rc, err := o.Barrier(protocol.BarrierRequest{SchemaVersion: 1, DomainID: "d", Generation: 1, Token: "resume", CutoffSequence: 5})
	if err != nil {
		t.Fatal(err)
	}
	if rc.CutoffSequence < 5 {
		t.Fatalf("resume cutoff=%d", rc.CutoffSequence)
	}
	after, err := o.effects.Query(`SELECT owner_version FROM domains WHERE domain_id='d'`)
	if err != nil {
		t.Fatal(err)
	}
	afterOV, _ := sqlitecgo.Int64At(after[0], 0)
	if afterOV != beforeOV {
		t.Fatalf("resume recommitted already-durable cutoff: owner_version %d -> %d", beforeOV, afterOV)
	}
	rows, err := o.claims.Query(`SELECT status FROM barriers WHERE domain_id='d' AND token='resume'`)
	if err != nil {
		t.Fatal(err)
	}
	status, _ := sqlitecgo.TextAt(rows[0], 0)
	if status != "COMPLETE" {
		t.Fatalf("barrier status=%q", status)
	}
}

func TestE2PilotForceStopIsTerminalWithoutEffect(t *testing.T) {
	o := newE12(t, "E2")
	r := e12req("reject", 5, 1)
	rc, err := o.ForceStop(r, "PILOT_PREDETERMINED_REJECT")
	if err != nil {
		t.Fatal(err)
	}
	if rc.Outcome != "STOPPED" || rc.Reason != "PILOT_PREDETERMINED_REJECT" {
		t.Fatalf("%#v", rc)
	}
	s, effects, claims, terminal, _, err := o.Snapshot()
	if err != nil {
		t.Fatal(err)
	}
	if s[0] != 0 || effects != 0 || claims != 1 || terminal != 1 {
		t.Fatalf("state=%v effects=%d claims=%d term=%d", s, effects, claims, terminal)
	}
	replay, err := o.Apply(r)
	if err != nil {
		t.Fatal(err)
	}
	if replay.Outcome != "STOPPED" {
		t.Fatalf("replay=%#v", replay)
	}
}
