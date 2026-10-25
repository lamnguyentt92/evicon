package runtime

import (
	"path/filepath"
	"strings"
	"testing"
	"time"

	"evicon/internal/journal"
	"evicon/internal/probe"
	"evicon/internal/protocol"
)

// All prior model outcomes are deliberately WRONG about actual B outcome.
// The authority still must use only the actual validated receipt and guard.
func TestM5V2PredictionCannotAdmitWithoutRealEvidence(t *testing.T) {
	d := journal.Domain{ID: "safe-model", Generation: 1, InstalledEpoch: 1, Dimension: 1, ComponentMin: []int64{0}, ComponentMax: []int64{12}, Anchor: []int64{4}, ContractHash: "test"}
	store, err := journal.Open(filepath.Join(t.TempDir(), "journal.db"))
	if err != nil {
		t.Fatal(err)
	}
	defer store.Close()
	if err = store.EnsureDomain(d); err != nil {
		t.Fatal(err)
	}
	mk := func(id string, v int64, seq uint64) protocol.CommandRequest {
		r := protocol.CommandRequest{SchemaVersion: 1, DomainID: d.ID, Generation: 1, CommandID: id, Operation: "ADD", Value: []int64{v}, Epoch: 1, Sequence: seq}
		if e := r.ValidateAndFillHash(1); e != nil {
			t.Fatal(e)
		}
		return r
	}
	a := mk("A", 4, 1)
	b := mk("B", 1, 2)
	candidate := mk("C", 4, 3)
	for _, r := range []protocol.CommandRequest{a, b} {
		dom, _ := store.GetDomain(d.ID)
		rec := journal.CommandRecord{DomainID: r.DomainID, CommandID: r.CommandID, Generation: 1, Epoch: 1, Sequence: r.Sequence, Operation: r.Operation, Value: r.Value, IntentHash: r.IntentHash, Reservation: r.Value, Status: "PENDING", Decision: "ADMITTED"}
		if _, e := store.ReserveAndEnqueueAtRevision(rec, "http://unused", runtimeAnalyzer, dom.Revision, nil); e != nil {
			t.Fatal(e)
		}
	}
	model := probe.OutcomeModel{Hash: strings.Repeat("a", 64), Spec: probe.OutcomeFile{SchemaVersion: 1, Profile: "E2", BucketThreshold: 2, DeadlineUS: 500000, CostPenaltyPerUS: 0, Rows: map[string]probe.OutcomePrior{
		"ADD:SMALL": {Applied: 0, Stopped: 1, LatencyUS: 100, Samples: 20},
		"ADD:LARGE": {Applied: 0, Stopped: 1, LatencyUS: 100, Samples: 20},
	}}}
	// No reachable endpoint; the query fails. A prediction of STOPPED=100%
	// still cannot narrow the belief or admit an unsafe candidate.
	auth, err := NewAuthority(AuthorityConfig{Store: store, Domain: d, Profile: "E2", EndpointURL: "http://127.0.0.1:1", Policy: probe.M5V2, QueryCosts: probe.DefaultCostModel(), OutcomeModel: model, ProbeBudget: 2, PlannerDepth: 2, PlannerComputeBudget: 100 * time.Millisecond, MaxConfigurations: 256, MaxOutstanding: 4})
	if err != nil {
		t.Fatal(err)
	}
	result, _, err := auth.evaluateWithPolicy(d, candidate)
	if err != nil {
		t.Fatal(err)
	}
	if result.Verdict == "SAFE" {
		t.Fatal("unsafe admission based on hypothetical STOPPED")
	}
	for _, c := range []protocol.CommandRequest{a, b} {
		commands, e := store.ListCommandsForBelief(d.ID, 0)
		if e != nil {
			t.Fatal(e)
		}
		for _, cmd := range commands {
			if cmd.CommandID == c.CommandID && cmd.Status == "STOPPED" {
				t.Fatal("hypothetical STOPPED was durably recorded")
			}
		}
	}
}
func TestM4PRejectsMissingModel(t *testing.T) {
	st, err := journal.Open(filepath.Join(t.TempDir(), "a.db"))
	if err != nil {
		t.Fatal(err)
	}
	defer st.Close()
	d := journal.Domain{ID: "d", Generation: 1, InstalledEpoch: 1, Dimension: 1, ComponentMax: []int64{12}, Anchor: []int64{0}, ContractHash: "t"}
	if _, err = NewAuthority(AuthorityConfig{Store: st, Domain: d, Policy: probe.M4P, Profile: "E2"}); err == nil {
		t.Fatal("accepted policy without frozen model")
	}
}
