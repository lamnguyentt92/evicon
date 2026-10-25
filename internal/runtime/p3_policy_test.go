package runtime

import (
	"bytes"
	"encoding/json"
	"evicon/internal/trace"
	"fmt"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"evicon/internal/journal"
	"evicon/internal/probe"
	"evicon/internal/protocol"
)

func setupPolicyTest(t *testing.T, method probe.Method, costs probe.CostModel) (*Authority, *journal.Store, protocol.CommandRequest, func()) {
	t.Helper()
	d := journal.Domain{ID: "atlas-001", Generation: 1, InstalledEpoch: 1, Dimension: 1, ComponentMin: []int64{0}, ComponentMax: []int64{12}, Anchor: []int64{4}, ContractHash: "p3-test"}
	st, err := journal.Open(filepath.Join(t.TempDir(), "authority.db"))
	if err != nil {
		t.Fatal(err)
	}
	if err := st.EnsureDomain(d); err != nil {
		t.Fatal(err)
	}
	mk := func(id string, v int64, seq uint64) protocol.CommandRequest {
		r := protocol.CommandRequest{SchemaVersion: 1, DomainID: d.ID, Generation: 1, CommandID: id, Operation: "ADD", Value: []int64{v}, Epoch: 1, Sequence: seq}
		if err := r.ValidateAndFillHash(1); err != nil {
			t.Fatal(err)
		}
		return r
	}
	a := mk("A", 4, 1)
	b := mk("B", 1, 2)
	c := mk("C", 4, 3)
	seed := func(r protocol.CommandRequest) {
		dom, _ := st.GetDomain(d.ID)
		rec := journal.CommandRecord{DomainID: r.DomainID, CommandID: r.CommandID, Generation: r.Generation, Epoch: r.Epoch, Sequence: r.Sequence, Operation: r.Operation, Value: r.Value, IntentHash: r.IntentHash, Reservation: r.Value, Status: "PENDING", Decision: "ADMITTED", Reason: "seed"}
		if _, e := st.ReserveAndEnqueueAtRevision(rec, "http://unused", runtimeAnalyzer, dom.Revision, nil); e != nil {
			t.Fatal(e)
		}
	}
	seed(a)
	seed(b)
	receipts := map[string]protocol.Receipt{
		"A": {SchemaVersion: 1, DomainID: d.ID, Generation: 1, CommandID: "A", IntentHash: a.IntentHash, Outcome: "STOPPED", OwnerVersion: 1, ReceiptVersion: 1, State: []int64{5}, Reason: "TEST_STOPPED"},
		"B": {SchemaVersion: 1, DomainID: d.ID, Generation: 1, CommandID: "B", IntentHash: b.IntentHash, Outcome: "APPLIED", OwnerVersion: 2, ReceiptVersion: 1, State: []int64{5}, Reason: "TEST_APPLIED"},
	}
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		id := r.URL.Path[len("/v1/receipts/"):]
		rc, ok := receipts[id]
		if !ok {
			http.NotFound(w, r)
			return
		}
		w.Header().Set("content-type", "application/json")
		json.NewEncoder(w).Encode(rc)
	}))
	auth, err := NewAuthority(AuthorityConfig{Store: st, Domain: d, EndpointURL: srv.URL, Profile: "E2", Policy: method, QueryCosts: costs, ProbeBudget: 2, PlannerDepth: 2, PlannerComputeBudget: 100 * time.Millisecond, QueryConcurrency: 2, PlannerCache: true, MaxConfigurations: 256, MaxOutstanding: 4})
	if err != nil {
		t.Fatal(err)
	}
	return auth, st, c, func() { srv.Close(); st.Close() }
}

func invokeCandidate(t *testing.T, a *Authority, c protocol.CommandRequest) (int, protocol.AuthorityResponse) {
	t.Helper()
	b, _ := json.Marshal(c)
	req := httptest.NewRequest(http.MethodPost, "/v1/commands", bytes.NewReader(b))
	rr := httptest.NewRecorder()
	a.Handler().ServeHTTP(rr, req)
	var out protocol.AuthorityResponse
	_ = json.Unmarshal(rr.Body.Bytes(), &out)
	return rr.Code, out
}

func TestP3M1QueryAll(t *testing.T) {
	cst := probe.DefaultCostModel()
	a, st, c, done := setupPolicyTest(t, probe.M1, cst)
	defer done()
	code, out := invokeCandidate(t, a, c)
	if code != 202 || out.Decision != "ADMITTED" {
		t.Fatalf("%d %+v", code, out)
	}
	ps, _ := st.ListProbeAttempts(c.DomainID, c.CommandID, c.IntentHash)
	if len(ps) != 2 {
		t.Fatalf("probes=%+v", ps)
	}
	if ps[0].QueriedCommand != "A" || ps[1].QueriedCommand != "B" {
		t.Fatalf("order=%+v", ps)
	}
}
func TestP3M2Largest(t *testing.T) {
	cst := probe.DefaultCostModel()
	a, st, c, done := setupPolicyTest(t, probe.M2, cst)
	defer done()
	code, _ := invokeCandidate(t, a, c)
	if code != 202 {
		t.Fatalf("code=%d", code)
	}
	ps, _ := st.ListProbeAttempts(c.DomainID, c.CommandID, c.IntentHash)
	if len(ps) != 1 || ps[0].QueriedCommand != "A" {
		t.Fatalf("probes=%+v", ps)
	}
}
func TestP3M3CheapestThenUseful(t *testing.T) {
	cst := probe.DefaultCostModel()
	cst.ByCommand = map[string]int64{"A": 4000, "B": 1000}
	a, st, c, done := setupPolicyTest(t, probe.M3, cst)
	defer done()
	code, _ := invokeCandidate(t, a, c)
	if code != 202 {
		t.Fatalf("code=%d", code)
	}
	ps, _ := st.ListProbeAttempts(c.DomainID, c.CommandID, c.IntentHash)
	if len(ps) != 2 || ps[0].QueriedCommand != "B" || ps[1].QueriedCommand != "A" {
		t.Fatalf("probes=%+v", ps)
	}
}
func TestP3M4DeficitCost(t *testing.T) {
	cst := probe.DefaultCostModel()
	a, st, c, done := setupPolicyTest(t, probe.M4, cst)
	defer done()
	code, _ := invokeCandidate(t, a, c)
	if code != 202 {
		t.Fatalf("code=%d", code)
	}
	ps, _ := st.ListProbeAttempts(c.DomainID, c.CommandID, c.IntentHash)
	if len(ps) != 1 || ps[0].QueriedCommand != "A" {
		t.Fatalf("probes=%+v", ps)
	}
}
func TestP3M5BoundedLookaheadUsesActualEvidence(t *testing.T) {
	cst := probe.DefaultCostModel()
	cst.ByCommand = map[string]int64{"A": 4000, "B": 1000}
	a, st, c, done := setupPolicyTest(t, probe.M5, cst)
	defer done()
	code, _ := invokeCandidate(t, a, c)
	if code != 202 {
		t.Fatalf("code=%d", code)
	}
	ps, _ := st.ListProbeAttempts(c.DomainID, c.CommandID, c.IntentHash)
	if len(ps) != 2 || ps[0].QueriedCommand != "B" || ps[0].Outcome != "APPLIED" || ps[1].QueriedCommand != "A" || ps[1].Outcome != "STOPPED" {
		t.Fatalf("probes=%+v", ps)
	}
	dec, _ := st.LatestDecision(c.DomainID, c.CommandID)
	if dec.Policy != "M5" || dec.ProbeCount != 2 {
		t.Fatalf("decision=%+v", dec)
	}
}
func TestP3ProbeBudgetPersistsAcrossRetry(t *testing.T) {
	cst := probe.DefaultCostModel()
	cst.ByCommand = map[string]int64{"A": 4000, "B": 1000}
	a, st, c, done := setupPolicyTest(t, probe.M3, cst)
	defer done()
	a.cfg.ProbeBudget = 1
	code, out := invokeCandidate(t, a, c)
	if code != 503 || out.Reason != "PROBE_BUDGET_EXHAUSTED" {
		t.Fatalf("first %d %+v", code, out)
	}
	n, _, _ := st.ProbeUsage(c.DomainID, c.CommandID, c.IntentHash)
	if n != 1 {
		t.Fatalf("used=%d", n)
	}
	code, out = invokeCandidate(t, a, c)
	if code != 503 || out.Reason != "PROBE_BUDGET_EXHAUSTED" {
		t.Fatalf("retry %d %+v", code, out)
	}
	n, _, _ = st.ProbeUsage(c.DomainID, c.CommandID, c.IntentHash)
	if n != 1 {
		t.Fatalf("budget reset used=%d", n)
	}
}
func TestP3M0StrongLedgerFixedReconciliation(t *testing.T) {
	cst := probe.DefaultCostModel()
	a, st, c, done := setupPolicyTest(t, probe.M0, cst)
	defer done()
	code, out := invokeCandidate(t, a, c)
	if code != 202 || out.Decision != "ADMITTED" {
		t.Fatalf("%d %+v", code, out)
	}
	ps, _ := st.ListProbeAttempts(c.DomainID, c.CommandID, c.IntentHash)
	if len(ps) != 1 || ps[0].QueriedCommand != "A" {
		t.Fatalf("M0 fixed reconciliation probes=%+v", ps)
	}
	dec, _ := st.LatestDecision(c.DomainID, c.CommandID)
	if dec.Policy != "M0" || dec.ProbeCount != 1 {
		t.Fatalf("decision=%+v", dec)
	}
}

func TestP3M0NoProbeAblation(t *testing.T) {
	cst := probe.DefaultCostModel()
	a, st, c, done := setupPolicyTest(t, probe.M0NoProbe, cst)
	defer done()
	code, out := invokeCandidate(t, a, c)
	if code != 409 || out.Decision != "BLOCK" {
		t.Fatalf("%d %+v", code, out)
	}
	ps, _ := st.ListProbeAttempts(c.DomainID, c.CommandID, c.IntentHash)
	if len(ps) != 0 {
		t.Fatalf("ablation unexpectedly probed: %+v", ps)
	}
}

func TestRevisionChangeAfterAnalysisForcesRecompute(t *testing.T) {
	cst := probe.DefaultCostModel()
	a, st, c, done := setupPolicyTest(t, probe.M0NoProbe, cst)
	defer done()
	called := false
	a.cfg.AfterPolicyEvaluationForTest = func() {
		if called {
			return
		}
		called = true
		cmd, err := st.GetCommand("atlas-001", "A")
		if err != nil {
			t.Fatal(err)
		}
		_, err = st.RecordEvidence(journal.ReceiptRecord{DomainID: cmd.DomainID, CommandID: cmd.CommandID, IntentHash: cmd.IntentHash, Outcome: "STOPPED", OwnerVersion: 9, ReceiptVersion: 9, State: []int64{5}}, "TEST_REVISION_CHANGE", "TEST", false)
		if err != nil {
			t.Fatal(err)
		}
	}
	code, out := invokeCandidate(t, a, c)
	if !called {
		t.Fatal("revision-change hook not called")
	}
	if code != 202 || out.Decision != "ADMITTED" {
		t.Fatalf("%d %+v", code, out)
	}
	// A becomes STOPPED between analysis and commit. Re-evaluation on the new
	// revision makes C safe; using the stale verdict would have blocked it.
	dec, err := st.LatestDecision(c.DomainID, c.CommandID)
	if err != nil {
		t.Fatal(err)
	}
	dom, _ := st.GetDomain(c.DomainID)
	if dec.Revision != dom.Revision {
		t.Fatalf("decision revision=%d domain=%d", dec.Revision, dom.Revision)
	}
}

func TestM0FrozenM2Reconciliation(t *testing.T) {
	a, st, c, done := setupPolicyTest(t, probe.M0, probe.DefaultCostModel())
	defer done()
	a.cfg.LedgerReconcile = probe.M2
	code, _ := invokeCandidate(t, a, c)
	if code != 202 {
		t.Fatal(code)
	}
	ps, e := st.ListProbeAttempts(c.DomainID, c.CommandID, c.IntentHash)
	if e != nil || len(ps) != 1 || ps[0].QueriedCommand != "A" {
		t.Fatal(ps, e)
	}
}

func TestAuthorityAcceptsCapabilityHonestLinuxTCProfile(t *testing.T) {
	dir := t.TempDir()
	st, err := journal.Open(filepath.Join(dir, "authority.db"))
	if err != nil {
		t.Fatal(err)
	}
	defer st.Close()
	d := journal.Domain{ID: "linux-edge-001", Generation: 1, InstalledEpoch: 1, Dimension: 1, ComponentMin: []int64{0}, ComponentMax: []int64{100000}, Anchor: []int64{0}, ContractHash: "linux-tc-016"}
	if _, err := NewAuthority(AuthorityConfig{Store: st, Domain: d, Profile: "LINUX_TC_E2_LIKE", Policy: probe.M5, QueryCosts: probe.DefaultCostModel()}); err != nil {
		t.Fatal(err)
	}
}

func TestLinuxProfileRejectsSETAtAuthorityBoundary(t *testing.T) {
	dir := t.TempDir()
	st, err := journal.Open(filepath.Join(dir, "authority.db"))
	if err != nil {
		t.Fatal(err)
	}
	defer st.Close()
	d := journal.Domain{ID: "linux-edge-001", Generation: 1, InstalledEpoch: 1, Dimension: 1, ComponentMin: []int64{0}, ComponentMax: []int64{100000}, Anchor: []int64{0}, ContractHash: "linux-tc-016"}
	a, err := NewAuthority(AuthorityConfig{Store: st, Domain: d, Profile: "LINUX_TC_E2_LIKE", Policy: probe.M5, QueryCosts: probe.DefaultCostModel()})
	if err != nil {
		t.Fatal(err)
	}
	body := `{"schema_version":1,"domain_id":"linux-edge-001","generation":1,"command_id":"bad-set","operation":"SET","value":[1],"epoch":1,"sequence":1}`
	rr := httptest.NewRecorder()
	req := httptest.NewRequest(http.MethodPost, "/v1/commands", strings.NewReader(body))
	a.Handler().ServeHTTP(rr, req)
	if rr.Code != http.StatusConflict {
		t.Fatalf("status=%d body=%s", rr.Code, rr.Body.String())
	}
	if !strings.Contains(rr.Body.String(), "UNSUPPORTED_OPERATION_FOR_PROFILE") {
		t.Fatal(rr.Body.String())
	}
}

func TestP3M5DefinitiveCounterexampleBlocksWithoutPlanner(t *testing.T) {
	d := journal.Domain{ID: "definitive-001", Generation: 1, InstalledEpoch: 1, Dimension: 1, ComponentMin: []int64{0}, ComponentMax: []int64{100}, Anchor: []int64{0}, ContractHash: "fix8-definitive"}
	st, err := journal.Open(filepath.Join(t.TempDir(), "authority.db"))
	if err != nil {
		t.Fatal(err)
	}
	defer st.Close()
	if err := st.EnsureDomain(d); err != nil {
		t.Fatal(err)
	}

	mk := func(id string, v int64, seq uint64) protocol.CommandRequest {
		r := protocol.CommandRequest{SchemaVersion: 1, DomainID: d.ID, Generation: 1, CommandID: id, Operation: "ADD", Value: []int64{v}, Epoch: 1, Sequence: seq}
		if err := r.ValidateAndFillHash(1); err != nil {
			t.Fatal(err)
		}
		return r
	}
	seedApplied := func(r protocol.CommandRequest, ownerVersion uint64, state int64) {
		dom, _ := st.GetDomain(d.ID)
		rec := journal.CommandRecord{DomainID: r.DomainID, CommandID: r.CommandID, Generation: r.Generation, Epoch: r.Epoch, Sequence: r.Sequence, Operation: r.Operation, Value: r.Value, IntentHash: r.IntentHash, Reservation: r.Value, Status: "PENDING", Decision: "ADMITTED", Reason: "seed"}
		if _, err := st.ReserveAndEnqueueAtRevision(rec, "http://unused", runtimeAnalyzer, dom.Revision, nil); err != nil {
			t.Fatal(err)
		}
		if _, err := st.RecordEvidence(journal.ReceiptRecord{DomainID: r.DomainID, CommandID: r.CommandID, IntentHash: r.IntentHash, Outcome: "APPLIED", OwnerVersion: ownerVersion, ReceiptVersion: 1, State: []int64{state}}, "TEST_APPLIED", "TEST", true); err != nil {
			t.Fatal(err)
		}
	}
	seedApplied(mk("A", 30, 1), 1, 30)
	seedApplied(mk("C", 25, 2), 2, 55)
	seedApplied(mk("D", 15, 3), 3, 70)
	candidate := mk("L-UNSAFE", 40, 4)

	a, err := NewAuthority(AuthorityConfig{Store: st, Domain: d, EndpointURL: "http://unused", Profile: "E2", Policy: probe.M5, QueryCosts: probe.DefaultCostModel(), ProbeBudget: 2, PlannerDepth: 2, PlannerComputeBudget: 100 * time.Millisecond, MaxConfigurations: 256, MaxOutstanding: 8})
	if err != nil {
		t.Fatal(err)
	}
	code, out := invokeCandidate(t, a, candidate)
	if code != 409 || out.Decision != "BLOCK" || out.Status != "NOT_ADMITTED" {
		t.Fatalf("expected definitive BLOCK, got code=%d out=%+v", code, out)
	}
	attempts, err := st.ListProbeAttempts(candidate.DomainID, candidate.CommandID, candidate.IntentHash)
	if err != nil {
		t.Fatal(err)
	}
	if len(attempts) != 0 {
		t.Fatalf("definitive counterexample unexpectedly probed: %+v", attempts)
	}
}

func TestPlannerTraceRecordsActualConfigurationAndExhaustion(t *testing.T) {
	for _, enabled := range []bool{false, true} {
		for _, tiny := range []bool{false, true} {
			t.Run(fmt.Sprintf("cache=%v/tiny=%v", enabled, tiny), func(t *testing.T) {
				a, _, c, done := setupPolicyTest(t, probe.M5, probe.DefaultCostModel())
				defer done()
				cfg := a.cfg
				cfg.PlannerCache = enabled
				if tiny {
					cfg.PlannerComputeBudget = time.Nanosecond
				}
				path := filepath.Join(t.TempDir(), "authority.jsonl")
				cfg.Trace = trace.New(path, "authority", "test", "M5", 1)
				a, err := NewAuthority(cfg)
				if err != nil {
					t.Fatal(err)
				}
				_, response := invokeCandidate(t, a, c)
				raw, err := os.ReadFile(path)
				if err != nil {
					t.Fatal(err)
				}
				calls, configurations := 0, 0
				for _, line := range bytes.Split(bytes.TrimSpace(raw), []byte("\n")) {
					var e trace.Event
					if err = json.Unmarshal(line, &e); err != nil {
						t.Fatal(err)
					}
					if e.Event == "PLANNER_CONFIGURATION" {
						configurations++
						if e.PlannerCacheEnabled == nil || *e.PlannerCacheEnabled != enabled || e.PlannerBudgetNS != cfg.PlannerComputeBudget.Nanoseconds() {
							t.Fatalf("bad config: %+v", e)
						}
					}
					if e.Event != "PLANNER_EVALUATED" {
						continue
					}
					calls++
					if e.PlannerCacheEnabled == nil || *e.PlannerCacheEnabled != enabled || e.PlannerCacheHit == nil || e.PlannerElapsedNS <= 0 || e.PlannerBudgetNS != cfg.PlannerComputeBudget.Nanoseconds() {
						t.Fatalf("bad event: %+v", e)
					}
					if tiny && e.Outcome != "BUDGET_EXHAUSTED" {
						t.Fatalf("bad outcome: %+v", e)
					}
				}
				if calls == 0 || configurations != 1 {
					t.Fatalf("missing trace: calls=%d config=%d", calls, configurations)
				}
				if tiny && response.Decision == "ADMITTED" {
					t.Fatal("over-budget plan admitted")
				}
			})
		}
	}
}
