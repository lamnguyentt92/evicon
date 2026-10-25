package journal

import (
	"errors"
	"path/filepath"
	"strings"
	"testing"

	"evicon/internal/domain"
)

func TestPragmasAndAtomicReserveRollback(t *testing.T) {
	path := filepath.Join(t.TempDir(), "authority.db")
	s, err := Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer s.Close()
	p, err := s.Pragmas()
	if err != nil {
		t.Fatal(err)
	}
	if p["journal_mode"] != "wal" {
		t.Fatalf("journal_mode=%q", p["journal_mode"])
	}
	if p["synchronous"] != "2" {
		t.Fatalf("synchronous=%q", p["synchronous"])
	}
	max := int64(12)
	d := Domain{ID: "d", Generation: 1, InstalledEpoch: 1, Dimension: 1, ComponentMin: []int64{0}, ComponentMax: []int64{12}, SumMax: &max, Anchor: []int64{0}, ContractHash: "h"}
	if err := s.EnsureDomain(d); err != nil {
		t.Fatal(err)
	}
	rec := CommandRecord{DomainID: "d", CommandID: "c1", Generation: 1, Epoch: 1, Sequence: 1, Operation: domain.OpAdd, Value: []int64{3}, IntentHash: "hash1", Reservation: []int64{3}, Status: "PENDING", Decision: "ADMITTED", Reason: "test"}
	want := errors.New("cut")
	if _, err := s.ReserveAndEnqueue(rec, "http://e", "test", func() error { return want }); !errors.Is(err, want) {
		t.Fatalf("got %v", err)
	}
	counts, err := s.Counts()
	if err != nil {
		t.Fatal(err)
	}
	if counts["commands"] != 0 || counts["outbox"] != 0 || counts["decisions"] != 0 {
		t.Fatalf("rollback failed: %#v", counts)
	}
	if _, err := s.ReserveAndEnqueue(rec, "http://e", "test", nil); err != nil {
		t.Fatal(err)
	}
	counts, _ = s.Counts()
	if counts["commands"] != 1 || counts["outbox"] != 1 || counts["decisions"] != 1 {
		t.Fatalf("commit missing: %#v", counts)
	}
}

func TestRecordNonAdmittedDecisionIsDurableWithoutAdvancingRevision(t *testing.T) {
	path := filepath.Join(t.TempDir(), "authority.db")
	s, err := Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer s.Close()
	d := Domain{ID: "audit", Generation: 1, InstalledEpoch: 1, Dimension: 1, ComponentMin: []int64{0}, ComponentMax: []int64{12}, Anchor: []int64{0}, ContractHash: "h"}
	if err = s.EnsureDomain(d); err != nil {
		t.Fatal(err)
	}
	if err = s.RecordDecision("audit", "c-block", "BLOCK", "RELATIONAL_ONLINE_BELIEF", "BOUND", 0); err != nil {
		t.Fatal(err)
	}
	counts, err := s.Counts()
	if err != nil {
		t.Fatal(err)
	}
	if counts["decisions"] != 1 || counts["commands"] != 0 || counts["outbox"] != 0 {
		t.Fatalf("unexpected counts: %#v", counts)
	}
	got, err := s.GetDomain("audit")
	if err != nil {
		t.Fatal(err)
	}
	if got.Revision != 0 {
		t.Fatalf("audit-only decision advanced semantic revision: %d", got.Revision)
	}
}

func TestEvidenceTerminalMonotonicAndAttemptCount(t *testing.T) {
	path := filepath.Join(t.TempDir(), "authority.db")
	s, err := Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer s.Close()
	d := Domain{ID: "d2", Generation: 1, InstalledEpoch: 1, Dimension: 1, ComponentMin: []int64{0}, ComponentMax: []int64{12}, Anchor: []int64{0}, ContractHash: "h"}
	if err = s.EnsureDomain(d); err != nil {
		t.Fatal(err)
	}
	rec := CommandRecord{DomainID: "d2", CommandID: "c", Generation: 1, Epoch: 1, Sequence: 1, Operation: domain.OpAdd, Value: []int64{3}, IntentHash: "h1", Reservation: []int64{3}, Status: "PENDING", Decision: "ADMITTED", Reason: "t"}
	if _, err = s.ReserveAndEnqueue(rec, "http://e", "t", nil); err != nil {
		t.Fatal(err)
	}
	if err = s.BeginDispatchAttempt("d2", "c"); err != nil {
		t.Fatal(err)
	}
	if err = s.BeginDispatchAttempt("d2", "c"); err != nil {
		t.Fatal(err)
	}
	n, err := s.OutboxAttemptCount("d2", "c")
	if err != nil || n != 2 {
		t.Fatalf("attempts=%d err=%v", n, err)
	}
	if _, err = s.RecordEvidence(ReceiptRecord{DomainID: "d2", CommandID: "c", IntentHash: "h1", Outcome: "APPLIED", OwnerVersion: 1, ReceiptVersion: 1, State: []int64{3}}, "ok", "RECEIPT", true); err != nil {
		t.Fatal(err)
	}
	if _, err = s.RecordEvidence(ReceiptRecord{DomainID: "d2", CommandID: "c", IntentHash: "h1", Outcome: "STOPPED", OwnerVersion: 2, ReceiptVersion: 2, State: []int64{3}}, "bad", "RECEIPT", false); err == nil {
		t.Fatal("expected terminal reversal rejection")
	}
}

func TestReserveRejectsStaleRevision(t *testing.T) {
	path := filepath.Join(t.TempDir(), "authority.db")
	s, err := Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer s.Close()
	d := Domain{ID: "d3", Generation: 1, InstalledEpoch: 1, Dimension: 1, ComponentMin: []int64{0}, ComponentMax: []int64{12}, Anchor: []int64{0}, ContractHash: "h"}
	if err = s.EnsureDomain(d); err != nil {
		t.Fatal(err)
	}
	r1 := CommandRecord{DomainID: "d3", CommandID: "a", Generation: 1, Epoch: 1, Sequence: 1, Operation: domain.OpAdd, Value: []int64{1}, IntentHash: "ha", Reservation: []int64{1}, Status: "PENDING", Decision: "ADMITTED", Reason: "t"}
	if _, err = s.ReserveAndEnqueueAtRevision(r1, "http://e", "t", 0, nil); err != nil {
		t.Fatal(err)
	}
	r2 := CommandRecord{DomainID: "d3", CommandID: "b", Generation: 1, Epoch: 1, Sequence: 2, Operation: domain.OpAdd, Value: []int64{1}, IntentHash: "hb", Reservation: []int64{1}, Status: "PENDING", Decision: "ADMITTED", Reason: "t"}
	if _, err = s.ReserveAndEnqueueAtRevision(r2, "http://e", "t", 0, nil); !errors.Is(err, ErrRevisionConflict) {
		t.Fatalf("expected revision conflict, got %v", err)
	}
}

func TestRecordEvidenceRejectsTerminalStateDimensionMismatch(t *testing.T) {
	path := filepath.Join(t.TempDir(), "authority.db")
	s, err := Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer s.Close()
	d := Domain{ID: "dim", Generation: 1, InstalledEpoch: 1, Dimension: 2, ComponentMin: []int64{0, 0}, ComponentMax: []int64{12, 12}, Anchor: []int64{0, 0}, ContractHash: "h"}
	if err = s.EnsureDomain(d); err != nil {
		t.Fatal(err)
	}
	rec := CommandRecord{DomainID: "dim", CommandID: "c", Generation: 1, Epoch: 1, Sequence: 1, Operation: domain.OpAdd, Value: []int64{1, 1}, IntentHash: "h1", Reservation: []int64{1, 1}, Status: "PENDING", Decision: "ADMITTED", Reason: "t"}
	if _, err = s.ReserveAndEnqueue(rec, "http://e", "t", nil); err != nil {
		t.Fatal(err)
	}
	_, err = s.RecordEvidence(ReceiptRecord{DomainID: "dim", CommandID: "c", IntentHash: "h1", Outcome: "APPLIED", OwnerVersion: 1, ReceiptVersion: 1, State: []int64{1}}, "bad-dim", "TEST", false)
	if err == nil {
		t.Fatal("expected terminal evidence dimension rejection")
	}
	got, err := s.GetCommand("dim", "c")
	if err != nil {
		t.Fatal(err)
	}
	if got.Status != "PENDING" {
		t.Fatalf("invalid evidence changed command status to %s", got.Status)
	}
}

func TestEnsureDomainRejectsDurableContractConflict(t *testing.T) {
	path := filepath.Join(t.TempDir(), "authority.db")
	s, err := Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer s.Close()
	sum := int64(20)
	d := Domain{ID: "cfg", Generation: 1, InstalledEpoch: 1, Dimension: 1, ComponentMin: []int64{0}, ComponentMax: []int64{12}, SumMax: &sum, Anchor: []int64{0}, ContractHash: "contract-v1"}
	if err = s.EnsureDomain(d); err != nil {
		t.Fatal(err)
	}
	// Anchor/current state is allowed to progress and is deliberately not part
	// of restart configuration equality.
	same := d
	same.Anchor = []int64{7}
	if err = s.EnsureDomain(same); err != nil {
		t.Fatalf("same contract rejected: %v", err)
	}
	bad := d
	bad.ComponentMax = []int64{5}
	if err = s.EnsureDomain(bad); err == nil || !strings.Contains(err.Error(), "CONFIGURATION_CONFLICT") {
		t.Fatalf("changed bound not rejected: %v", err)
	}
	bad = d
	bad.ContractHash = "contract-v2"
	if err = s.EnsureDomain(bad); err == nil {
		t.Fatal("changed contract hash accepted")
	}
	bad = d
	bad.Dimension = 2
	bad.ComponentMin = []int64{0, 0}
	bad.ComponentMax = []int64{12, 12}
	if err = s.EnsureDomain(bad); err == nil {
		t.Fatal("changed dimension accepted")
	}
}
