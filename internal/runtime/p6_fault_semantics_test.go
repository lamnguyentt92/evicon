package runtime

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"

	"evicon/internal/journal"
	"evicon/internal/probe"
	"evicon/internal/protocol"
)

// F13: a terminal-looking receipt from an old generation must not narrow the
// current belief. The probe is still charged, but the command remains PENDING.
func TestP6F13RejectsOldGenerationReceipt(t *testing.T) {
	a, st, c, done := setupPolicyTest(t, probe.M2, probe.DefaultCostModel())
	defer done()
	old := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		cmd, err := st.GetCommand("atlas-001", "A")
		if err != nil {
			t.Fatal(err)
		}
		rc := protocol.Receipt{SchemaVersion: 1, DomainID: "atlas-001", Generation: 0, CommandID: "A", IntentHash: cmd.IntentHash, Outcome: "STOPPED", OwnerVersion: 7, ReceiptVersion: 7, State: []int64{4}, Reason: "F13_OLD_GENERATION"}
		w.Header().Set("content-type", "application/json")
		_ = json.NewEncoder(w).Encode(rc)
	}))
	defer old.Close()
	a.cfg.EndpointURL = old.URL
	a.cfg.ProbeBudget = 1
	code, out := invokeCandidate(t, a, c)
	if code != 503 || out.Decision != "DEFER" || out.Reason != "PROBE_BUDGET_EXHAUSTED" {
		t.Fatalf("unexpected response %d %+v", code, out)
	}
	cmd, _ := st.GetCommand("atlas-001", "A")
	if cmd.Status != "PENDING" {
		t.Fatalf("stale-generation evidence narrowed belief: status=%s", cmd.Status)
	}
	ps, _ := st.ListProbeAttempts(c.DomainID, c.CommandID, c.IntentHash)
	if len(ps) != 1 || ps[0].Outcome != "INVALID_EVIDENCE" {
		t.Fatalf("probe audit=%+v", ps)
	}
}

// F14: if the durable revision changes after analysis, the stale analysis is
// discarded and the candidate is recomputed before reservation/commit.
func TestP6F14RevisionChangeForcesRecomputeBeforeReserve(t *testing.T) {
	a, st, c, done := setupPolicyTest(t, probe.M0NoProbe, probe.DefaultCostModel())
	defer done()
	changed := false
	a.cfg.AfterPolicyEvaluationForTest = func() {
		if changed {
			return
		}
		changed = true
		cmd, err := st.GetCommand("atlas-001", "A")
		if err != nil {
			t.Fatal(err)
		}
		if _, err = st.RecordEvidence(journal.ReceiptRecord{DomainID: cmd.DomainID, CommandID: cmd.CommandID, IntentHash: cmd.IntentHash, Outcome: "STOPPED", OwnerVersion: 14, ReceiptVersion: 14, State: []int64{5}}, "F14_REVISION_CHANGE", "TEST", false); err != nil {
			t.Fatal(err)
		}
	}
	code, out := invokeCandidate(t, a, c)
	if !changed || code != 202 || out.Decision != "ADMITTED" {
		t.Fatalf("changed=%v code=%d out=%+v", changed, code, out)
	}
	dec, err := st.LatestDecision(c.DomainID, c.CommandID)
	if err != nil {
		t.Fatal(err)
	}
	dom, _ := st.GetDomain(c.DomainID)
	if dec.Revision != dom.Revision {
		t.Fatalf("stale commit evidence: decision=%+v domain=%+v", dec, dom)
	}
}

// F19: a query timeout consumes the probe budget but is UNCERTAIN; it must not
// be converted into STOPPED or release the unresolved obligation.
func TestP6F19TimeoutDoesNotBecomeStopped(t *testing.T) {
	a, st, c, done := setupPolicyTest(t, probe.M2, probe.DefaultCostModel())
	defer done()
	slow := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { time.Sleep(120 * time.Millisecond); w.WriteHeader(500) }))
	defer slow.Close()
	a.cfg.EndpointURL = slow.URL
	a.cfg.ProbeBudget = 1
	a.hc.Timeout = 20 * time.Millisecond
	code, out := invokeCandidate(t, a, c)
	if code != 503 || out.Decision != "DEFER" || out.Reason != "PROBE_BUDGET_EXHAUSTED" {
		t.Fatalf("unexpected %d %+v", code, out)
	}
	cmd, _ := st.GetCommand("atlas-001", "A")
	if cmd.Status != "PENDING" {
		t.Fatalf("timeout changed terminal status: %s", cmd.Status)
	}
	ps, _ := st.ListProbeAttempts(c.DomainID, c.CommandID, c.IntentHash)
	if len(ps) != 1 || ps[0].Outcome != "UNCERTAIN" {
		t.Fatalf("probe audit=%+v", ps)
	}
}

// F20: exhausting the configuration budget is conservative. No world may be
// truncated to manufacture an admission; existing obligations remain durable.
// Release 072: an ADD-only history is decided on its product-form belief,
// which needs no configuration budget, so the decision stays exact (here a
// BLOCK). A history with an unresolved SET is still enumerated and still
// DEFERs when the budget is exhausted.
func TestP6F20ConfigurationBudgetDefersAndPreservesObligations(t *testing.T) {
	a, st, c, done := setupPolicyTest(t, probe.M0NoProbe, probe.DefaultCostModel())
	defer done()
	a.cfg.MaxConfigurations = 1
	code, out := invokeCandidate(t, a, c)
	if code != 409 || out.Decision != "BLOCK" || out.Reason != "RELATIONAL_ENVELOPE_COUNTEREXAMPLE" {
		t.Fatalf("ADD-only history: want exact BLOCK without a budget, got %d %+v", code, out)
	}
	if _, err := st.GetCommand(c.DomainID, c.CommandID); err == nil {
		t.Fatal("candidate was reserved despite a counterexample")
	}
	// an unresolved SET makes the belief enumerated again: budget 1 is exhausted
	dom, _ := st.GetDomain(c.DomainID)
	set := protocol.CommandRequest{SchemaVersion: 1, DomainID: c.DomainID, Generation: 1, CommandID: "S", Operation: "SET", Value: []int64{2}, Epoch: 1, Sequence: 10}
	if err := set.ValidateAndFillHash(1); err != nil {
		t.Fatal(err)
	}
	rec := journal.CommandRecord{DomainID: set.DomainID, CommandID: set.CommandID, Generation: 1, Epoch: 1, Sequence: set.Sequence, Operation: set.Operation, Value: set.Value, IntentHash: set.IntentHash, Reservation: set.Value, Status: "PENDING", Decision: "ADMITTED", Reason: "seed"}
	if _, err := st.ReserveAndEnqueueAtRevision(rec, "http://unused", runtimeAnalyzer, dom.Revision, nil); err != nil {
		t.Fatal(err)
	}
	c2 := protocol.CommandRequest{SchemaVersion: 1, DomainID: c.DomainID, Generation: 1, CommandID: "C2", Operation: "ADD", Value: []int64{1}, Epoch: 1, Sequence: 11}
	if err := c2.ValidateAndFillHash(1); err != nil {
		t.Fatal(err)
	}
	a.cfg.MaxOutstanding = 16
	code, out = invokeCandidate(t, a, c2)
	if code != 503 || out.Decision != "DEFER" || out.Reason != "CONFIGURATION_BUDGET_EXHAUSTED" {
		t.Fatalf("SET history: want DEFER on the exhausted budget, got %d %+v", code, out)
	}
	if _, err := st.GetCommand(c2.DomainID, c2.CommandID); err == nil {
		t.Fatal("candidate was reserved despite configuration exhaustion")
	}
	for _, id := range []string{"A", "B", "S"} {
		cmd, err := st.GetCommand(c.DomainID, id)
		if err != nil || cmd.Status != "PENDING" {
			t.Fatalf("obligation %s lost/changed: %+v err=%v", id, cmd, err)
		}
	}
}
