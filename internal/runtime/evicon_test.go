package runtime

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"testing"
	"time"

	"evicon/internal/adapter"
	"evicon/internal/contract"
	"evicon/internal/domain"
	"evicon/internal/journal"
	"evicon/internal/outbox"
	"evicon/internal/probe"
	"evicon/internal/protocol"
	"evicon/internal/resolve"
)

// e2Harness runs a real E2 owner behind HTTP with the 061 routes. Requests in
// dropRequest never reach the owner (lost on the way); requests in dropReply
// take effect but their reply is lost.
type e2Harness struct {
	owner       *adapter.E12Owner
	srv         *httptest.Server
	mu          sync.Mutex
	dropRequest map[string]bool
	dropReply   map[string]bool
	stops       int
	barriers    int
	failBarrier int // number of upcoming barrier calls answered 503 (owner unreachable)
}

func newE2Harness(t *testing.T, profile string, withStop bool) *e2Harness {
	t.Helper()
	dir := t.TempDir()
	o, err := adapter.OpenE12(adapter.E12Config{Profile: profile, ClaimDBPath: filepath.Join(dir, "c.db"), EffectsDBPath: filepath.Join(dir, "e.db"),
		DomainID: "dose-1", Generation: 1, InstalledEpoch: 1, InitialState: []int64{0}, Bound: domain.HardBound{ComponentMin: []int64{0}, ComponentMax: []int64{12}}})
	if err != nil {
		t.Fatal(err)
	}
	h := &e2Harness{owner: o, dropRequest: map[string]bool{}, dropReply: map[string]bool{}}
	mux := http.NewServeMux()
	mux.HandleFunc("/v1/effects", func(w http.ResponseWriter, r *http.Request) {
		var req protocol.CommandRequest
		_ = json.NewDecoder(r.Body).Decode(&req)
		h.mu.Lock()
		lostReq, lostRep := h.dropRequest[req.CommandID], h.dropReply[req.CommandID]
		h.mu.Unlock()
		if lostReq {
			http.Error(w, "lost", 503)
			return
		}
		rc, err := o.Apply(req)
		if err != nil {
			http.Error(w, err.Error(), 400)
			return
		}
		if lostRep {
			http.Error(w, "reply lost", 503)
			return
		}
		writeJSON(w, 200, rc)
	})
	mux.HandleFunc("/v1/receipts/", func(w http.ResponseWriter, r *http.Request) {
		rc, err := o.GetReceipt("dose-1", strings.TrimPrefix(r.URL.Path, "/v1/receipts/"))
		if errors.Is(err, os.ErrNotExist) {
			http.NotFound(w, r)
			return
		}
		if err != nil {
			http.Error(w, err.Error(), 500)
			return
		}
		writeJSON(w, 200, rc)
	})
	mux.HandleFunc("/v1/stop", func(w http.ResponseWriter, r *http.Request) {
		h.mu.Lock()
		h.stops++
		h.mu.Unlock()
		if !withStop {
			writeJSON(w, 409, map[string]any{"error": "STOP_UNSUPPORTED"})
			return
		}
		var sr protocol.StopRequest
		_ = json.NewDecoder(r.Body).Decode(&sr)
		rc, err := o.Stop(sr)
		if err != nil {
			http.Error(w, err.Error(), 400)
			return
		}
		writeJSON(w, 200, rc)
	})
	mux.HandleFunc("/v1/barriers", func(w http.ResponseWriter, r *http.Request) {
		h.mu.Lock()
		h.barriers++
		fail := h.failBarrier > 0
		if fail {
			h.failBarrier--
		}
		h.mu.Unlock()
		if fail {
			http.Error(w, "owner unreachable", 503)
			return
		}
		var br protocol.BarrierRequest
		_ = json.NewDecoder(r.Body).Decode(&br)
		rc, err := o.Barrier(br)
		if err != nil {
			http.Error(w, err.Error(), 400)
			return
		}
		writeJSON(w, 200, rc)
	})
	h.srv = httptest.NewServer(mux)
	t.Cleanup(func() { h.srv.Close(); _ = o.Close() })
	return h
}

func eviconAuthority(t *testing.T, h *e2Harness, profile string, ap contract.AdapterProfile, pol resolve.Policy) (*Authority, *journal.Store, *outbox.Dispatcher) {
	t.Helper()
	d := journal.Domain{ID: "dose-1", Generation: 1, InstalledEpoch: 1, Dimension: 1, ComponentMin: []int64{0}, ComponentMax: []int64{12}, Anchor: []int64{0}, ContractHash: "evicon-test"}
	st, err := journal.Open(filepath.Join(t.TempDir(), "authority.db"))
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { st.Close() })
	declared, err := contract.EvidenceContractForProfile(profile)
	if err != nil {
		t.Fatal(err)
	}
	ec, err := contract.RuntimeContract("runtime-"+profile, declared, ap)
	if err != nil {
		t.Fatal(err)
	}
	disp := outbox.New(st, time.Hour, nil)
	a, err := NewAuthority(AuthorityConfig{Store: st, Domain: d, EndpointURL: h.srv.URL, Profile: profile, Policy: probe.EVICON, Dispatcher: disp,
		ProbeBudget: 4, MaxOutstanding: 4, MaxConfigurations: 256, PlannerComputeBudget: 50 * time.Millisecond,
		EvidenceContract: ec, ResolvePolicy: pol, AmbiguityAfter: time.Millisecond, ResolveCosts: resolve.Costs{HoldPerUnitPerSec: 1, StopCost: 0.001, FenceCost: 0.001}})
	if err != nil {
		t.Fatal(err)
	}
	return a, st, disp
}

func dose(t *testing.T, id string, v int64, seq uint64) protocol.CommandRequest {
	r := protocol.CommandRequest{SchemaVersion: 1, DomainID: "dose-1", Generation: 1, CommandID: id, Operation: domain.OpAdd, Value: []int64{v}, Epoch: 1, Sequence: seq}
	if err := r.ValidateAndFillHash(1); err != nil {
		t.Fatal(err)
	}
	return r
}

func ownerState(t *testing.T, h *e2Harness) (int64, int64) {
	s, effects, _, _, _, err := h.owner.Snapshot()
	if err != nil {
		t.Fatal(err)
	}
	return s[0], effects
}

// F25: a lost request blocks a candidate; EVICON stops the ambiguous command
// (tombstone), admits the candidate, and the delayed original can never apply.
func TestF25EviconStopUnblocksCandidate(t *testing.T) {
	h := newE2Harness(t, "E2", true)
	a, st, disp := eviconAuthority(t, h, "E2", adapter.E2StopProfile(), resolve.Policy{Mode: resolve.ModeImmediate})
	A := dose(t, "A", 8, 1)
	h.dropRequest["A"] = true
	if code, out := invokeCandidate(t, a, A); code != 202 {
		t.Fatalf("A admit: %d %+v", code, out)
	}
	_ = disp.DispatchOnce(context.Background()) // request lost: A stays PENDING
	C := dose(t, "C", 8, 2)
	code, out := invokeCandidate(t, a, C)
	if code != 202 || out.Decision != "ADMITTED" {
		t.Fatalf("C must be admitted after the stop: %d %+v", code, out)
	}
	ra, _ := st.GetCommand("dose-1", "A")
	if ra.Status != "STOPPED" {
		t.Fatalf("A must be STOPPED by owner evidence, got %s", ra.Status)
	}
	if h.stops != 1 || h.barriers != 0 {
		t.Fatalf("expected one stop and no barrier, got stops=%d barriers=%d", h.stops, h.barriers)
	}
	// The delayed original finally reaches the owner: it must not apply.
	late, err := h.owner.Apply(A)
	if err != nil || late.Outcome != "STOPPED" {
		t.Fatalf("late A must be STOPPED: %+v %v", late, err)
	}
	_ = disp.DispatchOnce(context.Background()) // dispatch C
	state, effects := ownerState(t, h)
	if state != 8 || effects != 1 {
		t.Fatalf("owner state=%d effects=%d, want 8/1", state, effects)
	}
}

// F25b (T3 in the runtime): the ambiguous predecessor actually applied (reply
// lost). The stop returns APPLIED and the candidate correctly stays blocked.
func TestF25bStopRevealsAppliedCandidateStaysBlocked(t *testing.T) {
	h := newE2Harness(t, "E2", true)
	a, st, disp := eviconAuthority(t, h, "E2", adapter.E2StopProfile(), resolve.Policy{Mode: resolve.ModeImmediate})
	A := dose(t, "A", 8, 1)
	h.dropReply["A"] = true
	invokeCandidate(t, a, A)
	_ = disp.DispatchOnce(context.Background())
	code, out := invokeCandidate(t, a, dose(t, "C", 8, 2))
	if code == 202 {
		t.Fatalf("C must not be admitted: %+v", out)
	}
	ra, _ := st.GetCommand("dose-1", "A")
	if ra.Status != "APPLIED" {
		t.Fatalf("stop must reveal APPLIED, got %s", ra.Status)
	}
	if state, effects := ownerState(t, h); state != 8 || effects != 1 {
		t.Fatalf("owner state=%d effects=%d", state, effects)
	}
}

// F26: legacy E2 (no Q_S) resolves through the owner-certified fence.
func TestF26EviconFenceOnLegacyE2(t *testing.T) {
	h := newE2Harness(t, "E2", false)
	a, st, disp := eviconAuthority(t, h, "E2", adapter.P2E2Profile(), resolve.Policy{Mode: resolve.ModeImmediate})
	A := dose(t, "A", 8, 1)
	h.dropRequest["A"] = true
	invokeCandidate(t, a, A)
	_ = disp.DispatchOnce(context.Background())
	code, out := invokeCandidate(t, a, dose(t, "C", 8, 2))
	if code != 202 {
		t.Fatalf("C must be admitted after the fence: %d %+v", code, out)
	}
	if h.barriers != 1 || h.stops != 0 {
		t.Fatalf("expected one barrier and no stop, got barriers=%d stops=%d", h.barriers, h.stops)
	}
	dom, _ := st.GetDomain("dose-1")
	if dom.CutoffSequence < 1 {
		t.Fatalf("authority anchor must cover A, cutoff=%d", dom.CutoffSequence)
	}
	late, err := h.owner.Apply(A)
	if err != nil || late.Outcome != "STOPPED" || late.Reason != "SEALED_BY_BARRIER" {
		t.Fatalf("late A must be sealed: %+v %v", late, err)
	}
}

// F27: the outbox T4 guard forbids same-identity re-dispatch outside a finite
// dedup horizon, and allows it for unbounded horizons.
func TestF27OutboxRetryGuard(t *testing.T) {
	st, err := journal.Open(filepath.Join(t.TempDir(), "a.db"))
	if err != nil {
		t.Fatal(err)
	}
	defer st.Close()
	d := journal.Domain{ID: "dose-1", Generation: 1, InstalledEpoch: 1, Dimension: 1, ComponentMax: []int64{12}, Anchor: []int64{0}, ContractHash: "t"}
	if err := st.EnsureDomain(d); err != nil {
		t.Fatal(err)
	}
	r := dose(t, "A", 1, 1)
	rec := journal.CommandRecord{DomainID: "dose-1", CommandID: "A", Generation: 1, Epoch: 1, Sequence: 1, Operation: domain.OpAdd, Value: r.Value, IntentHash: r.IntentHash, Reservation: r.Value, Status: "PENDING", Decision: "ADMITTED", Reason: "t"}
	if _, err := st.ReserveAndEnqueueAtRevision(rec, "http://unused", runtimeAnalyzer, 0, nil); err != nil {
		t.Fatal(err)
	}
	finite := contract.EvidenceContract{SchemaVersion: 1, ID: "k1", Source: contract.SourceRuntimeVerified, Capabilities: []contract.EvidenceCapability{contract.CapDedup}, DedupHorizonMS: 45000}
	guard := RetryAllowedFunc(st, finite, 50*time.Second) // delay bound exceeds the horizon
	if !guard("dose-1", "A", 0) {
		t.Fatal("first dispatch is always allowed")
	}
	if guard("dose-1", "A", 1) {
		t.Fatal("re-dispatch must be suppressed when min(h)-delta has passed")
	}
	unb := contract.EvidenceContract{SchemaVersion: 1, ID: "e2", Source: contract.SourceRuntimeVerified, Capabilities: []contract.EvidenceCapability{contract.CapDedup}, DedupHorizonMS: contract.UnboundedHorizonMS}
	if !RetryAllowedFunc(st, unb, time.Second)("dose-1", "A", 3) {
		t.Fatal("unbounded dedup must allow retries")
	}
	if RetryAllowedFunc(st, finite, -1)("dose-1", "A", 1) {
		t.Fatal("unbounded delay with finite horizon must forbid retries")
	}
}

// F28: on E3 (atomic receipts, no backward evidence) EVICON cannot resolve a
// lost request: it escalates and never fabricates STOPPED.
func TestF28EviconEscalatesWithoutBackwardEvidence(t *testing.T) {
	h := newE2Harness(t, "E2", false) // owner reachable; contract decides
	ap := adapter.P1E3Profile()
	a, st, disp := eviconAuthority(t, h, "E3", ap, resolve.Policy{Mode: resolve.ModeImmediate})
	A := dose(t, "A", 8, 1)
	h.dropRequest["A"] = true
	invokeCandidate(t, a, A)
	_ = disp.DispatchOnce(context.Background())
	code, out := invokeCandidate(t, a, dose(t, "C", 8, 2))
	if code == 202 || out.Reason != "EVICON_NO_BACKWARD_EVIDENCE" {
		t.Fatalf("expected DEFER EVICON_NO_BACKWARD_EVIDENCE, got %d %+v", code, out)
	}
	if h.stops != 0 || h.barriers != 0 {
		t.Fatal("no stop or barrier may be issued without the capability")
	}
	ra, _ := st.GetCommand("dose-1", "A")
	if ra.Status != "PENDING" {
		t.Fatalf("A must remain ambiguous, got %s", ra.Status)
	}
}

func TestEviconRequiresVerifiedContract(t *testing.T) {
	st, err := journal.Open(filepath.Join(t.TempDir(), "a.db"))
	if err != nil {
		t.Fatal(err)
	}
	defer st.Close()
	d := journal.Domain{ID: "x", Generation: 1, InstalledEpoch: 1, Dimension: 1, ComponentMax: []int64{1}, Anchor: []int64{0}, ContractHash: "t"}
	if _, err := NewAuthority(AuthorityConfig{Store: st, Domain: d, Profile: "E2", Policy: probe.EVICON}); err == nil {
		t.Fatal("EVICON without contract must fail")
	}
	hyp := contract.EvidenceContract{SchemaVersion: 1, ID: "h", Source: contract.SourceHypothesis}
	if _, err := NewAuthority(AuthorityConfig{Store: st, Domain: d, Profile: "E2", Policy: probe.EVICON, EvidenceContract: hyp}); err == nil {
		t.Fatal("EVICON with a hypothesis contract must fail")
	}
}

// F31 (review RV01): a full outstanding budget must activate the resolver.
// Any terminal outcome frees a pending slot; the guard still decides.
func TestF31PendingBoundActivatesResolver(t *testing.T) {
	h := newE2Harness(t, "E2", true)
	a, st, disp := eviconAuthority(t, h, "E2", adapter.E2StopProfile(), resolve.Policy{Mode: resolve.ModeImmediate})
	a.cfg.MaxOutstanding = 1
	A := dose(t, "A", 2, 1)
	h.dropRequest["A"] = true
	if code, out := invokeCandidate(t, a, A); code != 202 {
		t.Fatalf("A: %d %+v", code, out)
	}
	_ = disp.DispatchOnce(context.Background())
	code, out := invokeCandidate(t, a, dose(t, "C", 2, 2))
	if code != 202 || out.Decision != "ADMITTED" {
		t.Fatalf("C must be admitted after the resolver frees the pending slot: %d %+v", code, out)
	}
	if ra, _ := st.GetCommand("dose-1", "A"); ra.Status != "STOPPED" {
		t.Fatalf("A must be STOPPED by owner evidence, got %s", ra.Status)
	}
	if h.stops != 1 {
		t.Fatalf("expected one stop, got %d", h.stops)
	}
}

// F32 (review RV01): the background sweep resolves an aged ambiguous command
// with no candidate at all, and never changes an APPLIED one.
func TestF32SweepResolvesWithoutCandidate(t *testing.T) {
	h := newE2Harness(t, "E2", true)
	a, st, disp := eviconAuthority(t, h, "E2", adapter.E2StopProfile(), resolve.Policy{Mode: resolve.ModeImmediate})
	h.dropRequest["A"] = true
	h.dropReply["B"] = true
	invokeCandidate(t, a, dose(t, "A", 2, 1))
	invokeCandidate(t, a, dose(t, "B", 3, 2))
	_ = disp.DispatchOnce(context.Background())
	_ = disp.DispatchOnce(context.Background())
	time.Sleep(5 * time.Millisecond)
	for i := 0; i < 3; i++ {
		if err := a.SweepOnce(); err != nil {
			t.Fatal(err)
		}
	}
	ra, _ := st.GetCommand("dose-1", "A")
	rb, _ := st.GetCommand("dose-1", "B")
	if ra.Status != "STOPPED" || rb.Status != "APPLIED" {
		t.Fatalf("sweep must stop A and reveal B applied: A=%s B=%s", ra.Status, rb.Status)
	}
	if state, effects := ownerState(t, h); state != 3 || effects != 1 {
		t.Fatalf("owner state=%d effects=%d", state, effects)
	}
}

// F33 (release 062, same-capability baseline): with the SWEEP_ONLY trigger a
// blocked candidate does not resolve anything; the periodic sweep does, with
// the same contract and actions as EVICON.
func TestF33SweepOnlyTriggerIsPeriodicBaseline(t *testing.T) {
	h := newE2Harness(t, "E2", true)
	a, st, disp := eviconAuthority(t, h, "E2", adapter.E2StopProfile(), resolve.Policy{Mode: resolve.ModeImmediate})
	a.cfg.ResolveTrigger = TriggerSweepOnly
	h.dropRequest["A"] = true
	invokeCandidate(t, a, dose(t, "A", 8, 1))
	_ = disp.DispatchOnce(context.Background())
	if _, out := invokeCandidate(t, a, dose(t, "C", 8, 2)); out.Decision == "ADMITTED" {
		t.Fatalf("SWEEP_ONLY must not resolve on the candidate path: %+v", out)
	}
	if h.stops != 0 {
		t.Fatalf("no stop expected before the sweep, got %d", h.stops)
	}
	time.Sleep(2 * time.Millisecond)
	if err := a.SweepOnce(); err != nil {
		t.Fatal(err)
	}
	if ra, _ := st.GetCommand("dose-1", "A"); ra.Status != "STOPPED" || h.stops != 1 {
		t.Fatalf("sweep must stop A once: status=%s stops=%d", ra.Status, h.stops)
	}
	if _, out := invokeCandidate(t, a, dose(t, "C2", 8, 3)); out.Decision != "ADMITTED" {
		t.Fatalf("C2 must be admitted after the sweep: %+v", out)
	}
}

// F34: an UNCERTAIN stop reply is not re-issued in a tight loop; the resolver
// waits AmbiguityAfter before stopping the same identity again.
func TestF34UncertainStopIsNotRetriedImmediately(t *testing.T) {
	h := newE2Harness(t, "E2", true)
	a, _, disp := eviconAuthority(t, h, "E2", adapter.E2StopProfile(), resolve.Policy{Mode: resolve.ModeImmediate})
	a.cfg.AmbiguityAfter = time.Hour
	h.dropRequest["A"] = true
	invokeCandidate(t, a, dose(t, "A", 8, 1))
	_ = disp.DispatchOnce(context.Background())
	a.stoppedAt = map[string]time.Time{"A": time.Now()}
	if _, out := invokeCandidate(t, a, dose(t, "C", 8, 2)); out.Decision == "ADMITTED" {
		t.Fatalf("candidate must stay blocked while the stop is outstanding: %+v", out)
	}
	if h.stops != 0 {
		t.Fatalf("recently stopped identity re-stopped: %d", h.stops)
	}
}

// F35 (release 062): a fence whose owner call fails leaves a pending barrier;
// the next resolution retries THAT barrier (same token and cutoff) and the
// domain resumes, instead of staying paused behind BARRIER_IN_PROGRESS.
func TestF35FailedFenceIsRetriedWithItsToken(t *testing.T) {
	h := newE2Harness(t, "E2", false)
	a, st, disp := eviconAuthority(t, h, "E2", adapter.P2E2Profile(), resolve.Policy{Mode: resolve.ModeImmediate})
	h.dropRequest["A"] = true
	invokeCandidate(t, a, dose(t, "A", 8, 1))
	_ = disp.DispatchOnce(context.Background())
	h.failBarrier = 1
	if _, out := invokeCandidate(t, a, dose(t, "C", 8, 2)); out.Decision == "ADMITTED" {
		t.Fatalf("C cannot be admitted while the fence failed: %+v", out)
	}
	pb, ok, err := st.PendingBarrier("dose-1")
	if err != nil || !ok {
		t.Fatalf("expected a pending barrier after the failed owner call: %v %v", ok, err)
	}
	time.Sleep(2 * time.Millisecond)
	if err := a.SweepOnce(); err != nil {
		t.Fatal(err)
	}
	if _, still, _ := st.PendingBarrier("dose-1"); still {
		t.Fatal("the pending barrier must complete on retry")
	}
	lc, err := st.GetBarrierLifecycle("dose-1", 1, pb.Token)
	if err != nil || lc.Status != "COMPLETE" {
		t.Fatalf("barrier %s: %+v %v", pb.Token, lc, err)
	}
	if ra, _ := st.GetCommand("dose-1", "A"); ra.Status != "STOPPED" {
		t.Fatalf("A must be sealed STOPPED by the completed fence, got %s", ra.Status)
	}
	if _, out := invokeCandidate(t, a, dose(t, "C2", 8, 3)); out.Decision != "ADMITTED" {
		t.Fatalf("admission must resume: %+v", out)
	}
}

// F36 (release 066): a fence aborted because the owner could not certify an identity
// (a crash orphan) must not be retried under the same token. Before 066 every later
// attempt at the same revision was refused locally (409 BARRIER_ABORTED) without
// contacting the owner, and the domain never fenced again (065 testbed run).
func TestF36AbortedFenceGetsAFreshToken(t *testing.T) {
	h := newE2Harness(t, "E2", false)
	a, st, disp := eviconAuthority(t, h, "E2", adapter.P2E2Profile(), resolve.Policy{Mode: resolve.ModeImmediate})
	h.dropRequest["A"] = true
	invokeCandidate(t, a, dose(t, "A", 8, 1))
	_ = disp.DispatchOnce(context.Background())
	d, err := st.GetDomain("dose-1")
	if err != nil {
		t.Fatal(err)
	}
	cut, err := st.MaxAdmittedSequence("dose-1")
	if err != nil {
		t.Fatal(err)
	}
	tok := fmt.Sprintf("evicon-%s--r%d", strings.ToLower(modeSweep), d.Revision)
	if err := st.BeginBarrier("dose-1", tok, 1, cut, d.Revision); err != nil {
		t.Fatal(err)
	}
	if err := st.AbortBarrier("dose-1", 1, tok, "OWNER_UNCERTAIN"); err != nil {
		t.Fatal(err)
	}
	time.Sleep(2 * time.Millisecond)
	if err := a.SweepOnce(); err != nil {
		t.Fatal(err)
	}
	if h.barriers != 1 {
		t.Fatalf("the owner must be asked again after an aborted barrier, got %d barrier calls", h.barriers)
	}
	if lc, err := st.GetBarrierLifecycle("dose-1", 1, tok+"-a1"); err != nil || lc.Status != "COMPLETE" {
		t.Fatalf("fresh token %s-a1: %+v %v", tok, lc, err)
	}
	if ra, _ := st.GetCommand("dose-1", "A"); ra.Status != "STOPPED" {
		t.Fatalf("A must be sealed STOPPED by the completed fence, got %s", ra.Status)
	}
}

// F42 (release 072): when the window of unresolved commands (MaxOutstanding) is
// what defers a candidate, the EVICON DEFER reason keeps that cause first, so a
// window-bound wait is told apart from a capacity-bound one in traces and counts.
func TestF42WindowBoundDeferKeepsItsCause(t *testing.T) {
	h := newE2Harness(t, "E2", false)
	ap := adapter.P1E3Profile()
	a, _, disp := eviconAuthority(t, h, "E3", ap, resolve.Policy{Mode: resolve.ModeImmediate})
	for i, id := range []string{"A1", "A2", "A3", "A4"} { // MaxOutstanding is 4; all four requests are lost
		h.dropRequest[id] = true
		if code, out := invokeCandidate(t, a, dose(t, id, 1, uint64(i+1))); code != 202 {
			t.Fatalf("%s: want admission, got %d %+v", id, code, out)
		}
	}
	_ = disp.DispatchOnce(context.Background())
	code, out := invokeCandidate(t, a, dose(t, "C", 1, 5))
	if code == 202 || !strings.HasPrefix(out.Reason, "OUTSTANDING_BUDGET_EXHAUSTED;EVICON_") {
		t.Fatalf("want DEFER OUTSTANDING_BUDGET_EXHAUSTED;EVICON_..., got %d %+v", code, out)
	}
}
