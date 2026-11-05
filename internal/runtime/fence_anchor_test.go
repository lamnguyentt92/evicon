package runtime

// Release 086 (verifier finding B2-02): the anchor of a complete fence from evidence (fence_anchor.go).
//
//   TestFenceAnchorDefault086              the default is the evidence anchor; owner-state and unknown values
//   TestFenceAnchorFromEvidence086         an honest owner: the fence anchors the computed state (= the owner's)
//   TestFenceAnchorMismatchAborts086       an owner that reports a wrong state (a reboot reset its level): the fence
//                                          aborts as an evidence conflict and nothing is anchored; with
//                                          -fence-anchor owner-state the wrong state becomes the anchor
//   TestFenceAnchorUndeterminedSET086      a SET domain whose evidence allows two states: no anchor, every answer
//                                          taken in (lifecycle PARTIAL)
//   TestFenceAnchorPropertyE2086           random ADD/SET histories with lost requests, lost replies, late copies and
//                                          fences on the E2 owner: every committed anchor equals the owner's true
//                                          state; an owner that reports a reset state makes fences abort and no
//                                          bound is violated; with owner-state the same histories violate the bound
//   TestFenceAnchorPropertyDevice086       the same with crash orphans, rules R5 and R5' on evicon-device (ADD)

import (
	"context"
	"encoding/json"
	"math/rand"
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
	"evicon/internal/trace"
)

func TestFenceAnchorDefault086(t *testing.T) {
	st, err := journal.Open(filepath.Join(t.TempDir(), "a.db"))
	if err != nil {
		t.Fatal(err)
	}
	defer st.Close()
	d := journal.Domain{ID: "dose-1", Generation: 1, InstalledEpoch: 1, Dimension: 1, Anchor: []int64{0}}
	for _, tc := range []struct{ in, want string }{{"", FenceAnchorEvidence}, {FenceAnchorEvidence, FenceAnchorEvidence}, {FenceAnchorOwnerState, FenceAnchorOwnerState}} {
		a, err := NewAuthority(AuthorityConfig{Store: st, Domain: d, Profile: "E2", FenceAnchor: tc.in})
		if err != nil || a.cfg.FenceAnchor != tc.want {
			t.Fatalf("FenceAnchor %q: got %q, %v (want %q)", tc.in, a.cfg.FenceAnchor, err, tc.want)
		}
	}
	if _, err := NewAuthority(AuthorityConfig{Store: st, Domain: d, Profile: "E2", FenceAnchor: "owner"}); err == nil {
		t.Fatal("an unknown fence anchor must be refused")
	}
}

// anchorHarness is an E2 owner behind HTTP whose fence answers can report a wrong state (lie) and whose true state
// after every effect is recorded (max), so that a bound violation of the owner is seen even when the owner itself
// enforces no bound.
type anchorHarness struct {
	*e2Harness
	mu2      sync.Mutex
	lie      func(state []int64) []int64 // nil: honest
	answers  map[string][]int64          // token -> the owner's true state when it answered the fence
	maxState int64
	held     []protocol.CommandRequest
	rng      *rand.Rand
	lossy    bool
}

func newAnchorHarness(t *testing.T, ownerBound domain.HardBound, initial []int64) *anchorHarness {
	t.Helper()
	dir := t.TempDir()
	o, err := adapter.OpenE12(adapter.E12Config{Profile: "E2", ClaimDBPath: filepath.Join(dir, "c.db"), EffectsDBPath: filepath.Join(dir, "e.db"),
		DomainID: "dose-1", Generation: 1, InstalledEpoch: 1, InitialState: initial, Bound: ownerBound})
	if err != nil {
		t.Fatal(err)
	}
	h := &anchorHarness{e2Harness: &e2Harness{owner: o, dropRequest: map[string]bool{}, dropReply: map[string]bool{}}, answers: map[string][]int64{}}
	mux := http.NewServeMux()
	mux.HandleFunc("/v1/effects", func(w http.ResponseWriter, r *http.Request) {
		var req protocol.CommandRequest
		_ = json.NewDecoder(r.Body).Decode(&req)
		h.mu.Lock()
		lostReq, lostRep := h.dropRequest[req.CommandID], h.dropReply[req.CommandID]
		h.mu.Unlock()
		fate := 100
		if h.lossy {
			h.mu2.Lock()
			fate = h.rng.Intn(100)
			h.mu2.Unlock()
		}
		switch {
		case lostReq || fate < 12: // lost on the way
			http.Error(w, "lost", 503)
			return
		case fate < 22: // held, delivered late
			h.mu2.Lock()
			h.held = append(h.held, req)
			h.mu2.Unlock()
			http.Error(w, "held", 503)
			return
		}
		rc, err := o.Apply(req)
		h.observe()
		if err != nil {
			http.Error(w, err.Error(), 400)
			return
		}
		if lostRep || fate < 34 {
			http.Error(w, "reply lost", 503)
			return
		}
		writeJSON(w, 200, rc)
	})
	mux.HandleFunc("/v1/receipts/", func(w http.ResponseWriter, r *http.Request) {
		rc, err := o.GetReceipt("dose-1", strings.TrimPrefix(r.URL.Path, "/v1/receipts/"))
		if err != nil {
			http.NotFound(w, r)
			return
		}
		writeJSON(w, 200, rc)
	})
	mux.HandleFunc("/v1/stop", func(w http.ResponseWriter, r *http.Request) {
		writeJSON(w, 409, map[string]any{"error": "STOP_UNSUPPORTED"})
	})
	mux.HandleFunc("/v1/barriers", func(w http.ResponseWriter, r *http.Request) {
		var br protocol.BarrierRequest
		_ = json.NewDecoder(r.Body).Decode(&br)
		rc, err := o.Barrier(br)
		if err != nil {
			http.Error(w, err.Error(), 400)
			return
		}
		h.mu2.Lock()
		h.answers[br.Token] = append([]int64(nil), rc.State...)
		if h.lie != nil {
			rc.State = h.lie(rc.State)
		}
		h.mu2.Unlock()
		writeJSON(w, 200, rc)
	})
	h.srv = httptest.NewServer(mux)
	t.Cleanup(func() { h.srv.Close(); _ = o.Close() })
	return h
}

func (h *anchorHarness) observe() {
	s, _, _, _, _, err := h.owner.Snapshot()
	if err != nil {
		return
	}
	var sum int64
	for _, v := range s {
		sum += v
	}
	h.mu2.Lock()
	if sum > h.maxState {
		h.maxState = sum
	}
	h.mu2.Unlock()
}

func (h *anchorHarness) deliverHeld(n int) {
	h.mu2.Lock()
	if n > len(h.held) {
		n = len(h.held)
	}
	batch := append([]protocol.CommandRequest(nil), h.held[:n]...)
	h.held = h.held[n:]
	h.mu2.Unlock()
	for _, req := range batch {
		_, _ = h.owner.Apply(req)
		h.observe()
	}
}

func anchorAuthority(t *testing.T, h *anchorHarness, bound int64, mode string, tw *trace.Writer, maxOut int, pol resolve.Policy) (*Authority, *journal.Store, *outbox.Dispatcher) {
	t.Helper()
	d := journal.Domain{ID: "dose-1", Generation: 1, InstalledEpoch: 1, Dimension: 1, ComponentMin: []int64{0}, ComponentMax: []int64{bound}, Anchor: []int64{0}, ContractHash: "anchor-086"}
	st, err := journal.Open(filepath.Join(t.TempDir(), "authority.db"))
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { st.Close() })
	declared, err := contract.EvidenceContractForProfile("E2")
	if err != nil {
		t.Fatal(err)
	}
	ec, err := contract.RuntimeContract("runtime-E2", declared, adapter.P2E2Profile())
	if err != nil {
		t.Fatal(err)
	}
	disp := outbox.New(st, time.Hour, nil)
	a, err := NewAuthority(AuthorityConfig{Store: st, Domain: d, EndpointURL: h.srv.URL, Profile: "E2", Policy: probe.EVICON, Dispatcher: disp, Trace: tw,
		ProbeBudget: 4, MaxOutstanding: maxOut, MaxConfigurations: 256, PlannerComputeBudget: 50 * time.Millisecond, FenceAnchor: mode,
		EvidenceContract: ec, ResolvePolicy: pol, AmbiguityAfter: time.Millisecond, ResolveCosts: resolve.Costs{HoldPerUnitPerSec: 1, QueryCost: 0.001, StopCost: 0.001, FenceCost: 0.001}})
	if err != nil {
		t.Fatal(err)
	}
	return a, st, disp
}

func setCmd(t *testing.T, id string, v int64, seq uint64) protocol.CommandRequest {
	r := protocol.CommandRequest{SchemaVersion: 1, DomainID: "dose-1", Generation: 1, CommandID: id, Operation: domain.OpSet, Value: []int64{v}, Epoch: 1, Sequence: seq}
	if err := r.ValidateAndFillHash(1); err != nil {
		t.Fatal(err)
	}
	return r
}

func traceEvents(t *testing.T, path string) []trace.Event {
	t.Helper()
	raw, err := readFileString(path)
	if err != nil {
		t.Fatal(err)
	}
	var out []trace.Event
	for _, l := range strings.Split(raw, "\n") {
		if strings.TrimSpace(l) == "" {
			continue
		}
		var e trace.Event
		if json.Unmarshal([]byte(l), &e) == nil {
			out = append(out, e)
		}
	}
	return out
}

func readFileString(p string) (string, error) {
	b, err := os.ReadFile(p)
	if os.IsNotExist(err) {
		return "", nil
	}
	return string(b), err
}

func openTrace(t *testing.T) (*trace.Writer, string) {
	t.Helper()
	p := filepath.Join(t.TempDir(), "authority.jsonl")
	return trace.New(p, "authority-test", "anchor-086", "EVICON", 0), p
}

// A lost request A blocks the candidate C; the fence lists A STOPPED and the earlier doses APPLIED.
func fenceScenario(t *testing.T, h *anchorHarness, a *Authority, disp *outbox.Dispatcher) (int, protocol.AuthorityResponse) {
	t.Helper()
	for i, v := range []int64{3, 4} {
		id := []string{"d1", "d2"}[i]
		invokeCandidate(t, a, dose(t, id, v, uint64(i+1)))
		_ = disp.DispatchOnce(context.Background())
	}
	h.mu.Lock()
	h.dropRequest["A"] = true
	h.mu.Unlock()
	invokeCandidate(t, a, dose(t, "A", 4, 3))
	_ = disp.DispatchOnce(context.Background())
	return invokeCandidate(t, a, dose(t, "C", 5, 4))
}

func TestFenceAnchorFromEvidence086(t *testing.T) {
	h := newAnchorHarness(t, domain.HardBound{}, []int64{0})
	tw, tp := openTrace(t)
	a, st, disp := anchorAuthority(t, h, 12, "", tw, 4, resolve.Policy{Mode: resolve.ModeImmediate})
	code, out := fenceScenario(t, h, a, disp)
	if code != 202 {
		t.Fatalf("C must be admitted after the fence: %d %+v", code, out)
	}
	dom, _ := st.GetDomain("dose-1")
	s, _ := ownerState(t, h.e2Harness)
	if dom.CutoffSequence < 3 || len(dom.Anchor) != 1 {
		t.Fatalf("the fence must anchor at or above A: %+v", dom)
	}
	var check *trace.Event
	for _, e := range traceEvents(t, tp) {
		if e.Event == "FENCE_ANCHOR_CHECK" {
			ev := e
			check = &ev
		}
	}
	if check == nil || check.Outcome != "MATCH" || !strings.Contains(check.Reason, "mode=evidence") || !strings.Contains(check.Reason, "computed=[7]") {
		t.Fatalf("expected FENCE_ANCHOR_CHECK MATCH computed=[7]: %+v", check)
	}
	if s < 7 {
		t.Fatalf("owner state %d", s)
	}
}

func TestFenceAnchorMismatchAborts086(t *testing.T) {
	for _, mode := range []string{FenceAnchorEvidence, FenceAnchorOwnerState} {
		t.Run(mode, func(t *testing.T) {
			h := newAnchorHarness(t, domain.HardBound{}, []int64{0})
			h.lie = func(s []int64) []int64 { return []int64{0} } // the level was reset by a reboot
			tw, tp := openTrace(t)
			a, st, disp := anchorAuthority(t, h, 12, mode, tw, 4, resolve.Policy{Mode: resolve.ModeImmediate})
			code, out := fenceScenario(t, h, a, disp)
			dom, _ := st.GetDomain("dose-1")
			evs := traceEvents(t, tp)
			has := func(name, sub string) bool {
				for _, e := range evs {
					if e.Event == name && strings.Contains(e.Reason+e.Outcome, sub) {
						return true
					}
				}
				return false
			}
			if mode == FenceAnchorEvidence {
				if code == 202 {
					t.Fatalf("C must stay blocked: the fence cannot complete: %+v", out)
				}
				if dom.CutoffSequence != 0 && dom.Anchor[0] == 0 {
					t.Fatalf("a reported state must never become the anchor: %+v", dom)
				}
				if !has("FENCE_ANCHOR_CHECK", "MISMATCH") || !has("EVIDENCE_CONFLICT", "FENCE_ANCHOR_MISMATCH") || !has("BARRIER_ABORTED", "EVIDENCE_CONFLICT") {
					t.Fatalf("expected MISMATCH, EVIDENCE_CONFLICT and BARRIER_ABORTED in the trace")
				}
				if has("BARRIER_ANCHOR_COMMITTED", "") {
					t.Fatal("no anchor may be committed")
				}
				c, _ := st.GetCommand("dose-1", "A")
				if c.Status == "STOPPED" {
					t.Fatal("an aborted fence seals nothing")
				}
				return
			}
			// owner-state (releases 061-085): the reported [0] becomes the anchor and C is admitted
			if code != 202 || dom.Anchor[0] != 0 || dom.CutoffSequence < 3 {
				t.Fatalf("owner-state must adopt the reported state: code %d %+v %+v", code, out, dom)
			}
			if !has("FENCE_ANCHOR_CHECK", "MISMATCH_OWNER_STATE_ADOPTED") {
				t.Fatal("the trace must record the adopted mismatch")
			}
		})
	}
}

func TestFenceAnchorUndeterminedSET086(t *testing.T) {
	h := newAnchorHarness(t, domain.HardBound{}, []int64{0})
	tw, tp := openTrace(t)
	a, st, disp := anchorAuthority(t, h, 12, "", tw, 4, resolve.Policy{Mode: resolve.ModeImmediate})
	// SET(5) seq 1 and ADD(2) seq 2 both apply, the ADD's reply is lost (the evidence cannot order them: 5+2 or 5 would
	// both be consistent if the ADD had come first), then a lost request blocks a candidate and a fence is needed
	h.mu.Lock()
	h.dropReply["s1"], h.dropReply["a2"], h.dropRequest["A"] = true, true, true
	h.mu.Unlock()
	invokeCandidate(t, a, setCmd(t, "s1", 5, 1))
	_ = disp.DispatchOnce(context.Background())
	invokeCandidate(t, a, dose(t, "a2", 2, 2))
	_ = disp.DispatchOnce(context.Background())
	invokeCandidate(t, a, dose(t, "A", 1, 3))
	_ = disp.DispatchOnce(context.Background())
	code, _ := invokeCandidate(t, a, dose(t, "C", 5, 4)) // blocked: worst 7 + 1 (A) + 5 > 12
	if code != 202 {
		t.Fatalf("C must be admitted after the unanchored fence (A sealed: worst state 7 + 5 <= 12): %d", code)
	}
	partial, aborted, err := st.BarrierStatusCounts("dose-1")
	if err != nil || partial != 1 || aborted != 0 {
		t.Fatalf("expected one PARTIAL (unanchored) fence: partial %d aborted %d %v", partial, aborted, err)
	}
	dom, _ := st.GetDomain("dose-1")
	if dom.CutoffSequence != 0 {
		t.Fatalf("nothing may be anchored: %+v", dom)
	}
	for _, id := range []string{"s1", "a2", "A"} {
		c, _ := st.GetCommand("dose-1", id)
		if c.Status != map[string]string{"s1": "APPLIED", "a2": "APPLIED", "A": "STOPPED"}[id] {
			t.Fatalf("%s: %s", id, c.Status)
		}
	}
	found := false
	for _, e := range traceEvents(t, tp) {
		if e.Event == "FENCE_ANCHOR_CHECK" && e.Outcome == "UNDETERMINED" && strings.Contains(e.Reason, "computed=[5]|[7]") {
			found = true
		}
	}
	if !found {
		t.Fatal("expected FENCE_ANCHOR_CHECK UNDETERMINED computed=[5]|[7]")
	}
}

type anchorOutcome struct {
	executions, fences, complete, partial, aborted, mismatched int
	anchorWrong, violations                                    int
}

// runAnchorExecution plays one random ADD/SET execution on the E2 owner (which enforces no bound itself) and returns
// what happened. lie: from a random step on, the owner's fence answers report the state minus the state at that
// step (a level counter restarted at 0 by a reboot).
func runAnchorExecution(t *testing.T, seed int64, mode string, lie bool) anchorOutcome {
	t.Helper()
	rng := rand.New(rand.NewSource(seed))
	bound := int64(10 + rng.Intn(10))
	h := newAnchorHarness(t, domain.HardBound{}, []int64{0})
	h.rng, h.lossy = rng, true
	pol := resolve.Policy{Mode: resolve.ModeImmediate, QueryFirst: rng.Intn(2) == 0}
	a, st, disp := anchorAuthority(t, h, bound, mode, nil, 2+rng.Intn(4), pol)
	rebootAt := -1
	if lie {
		rebootAt = rng.Intn(8)
	}
	var seq uint64
	steps := 18 + rng.Intn(14)
	for i := 0; i < steps; i++ {
		if i == rebootAt {
			s, _, _, _, _, _ := h.owner.Snapshot()
			shift := s[0]
			h.mu2.Lock()
			h.lie = func(x []int64) []int64 {
				v := x[0] - shift
				if v < 0 {
					v = 0
				}
				return []int64{v}
			}
			h.mu2.Unlock()
		}
		switch k := rng.Intn(10); {
		case k < 6:
			seq++
			id := "c" + itoaSeq(seq)
			var req protocol.CommandRequest
			if rng.Intn(5) == 0 {
				req = setCmd(t, id, int64(rng.Intn(int(bound)/2+1)), seq)
			} else {
				req = dose(t, id, int64(1+rng.Intn(4)), seq)
			}
			invokeCandidate(t, a, req)
			_ = disp.DispatchOnce(context.Background())
		case k < 8:
			if err := a.SweepOnce(); err != nil {
				t.Fatal(err)
			}
		default:
			h.deliverHeld(1 + rng.Intn(2))
		}
	}
	h.deliverHeld(1 << 20)
	for i := 0; i < 3; i++ {
		_ = disp.DispatchOnce(context.Background())
		_ = a.SweepOnce()
	}
	var o anchorOutcome
	o.executions = 1
	if h.maxState > bound {
		o.violations = 1
	}
	rows, err := lifecycles(st)
	if err != nil {
		t.Fatal(err)
	}
	for _, r := range rows {
		o.fences++
		switch r.Status {
		case "COMPLETE":
			o.complete++
			h.mu2.Lock()
			truth, ok := h.answers[r.Token]
			h.mu2.Unlock()
			if ok && !sameState(domain.State(truth), domain.State(r.Anchor)) {
				o.anchorWrong++
			}
		case "PARTIAL":
			o.partial++
		case "ABORTED":
			o.aborted++
		}
	}
	return o
}

func lifecycles(st *journal.Store) ([]journal.BarrierLifecycle, error) {
	toks, err := st.BarrierTokens("dose-1")
	if err != nil {
		return nil, err
	}
	var out []journal.BarrierLifecycle
	for _, tok := range toks {
		b, err := st.GetBarrierLifecycle("dose-1", 1, tok)
		if err != nil {
			return nil, err
		}
		out = append(out, b)
	}
	return out, nil
}

func TestFenceAnchorPropertyE2086(t *testing.T) {
	n := 80
	if testing.Short() {
		n = 20
	}
	for _, tc := range []struct {
		name string
		mode string
		lie  bool
	}{
		{"honest owner, evidence anchor", FenceAnchorEvidence, false},
		{"reset level, evidence anchor", FenceAnchorEvidence, true},
		{"negative control: reset level, owner-state anchor", FenceAnchorOwnerState, true},
	} {
		t.Run(tc.name, func(t *testing.T) {
			var tot anchorOutcome
			for i := 0; i < n; i++ {
				o := runAnchorExecution(t, int64(8600+i), tc.mode, tc.lie)
				if tc.mode == FenceAnchorEvidence && (o.violations > 0 || o.anchorWrong > 0) {
					t.Fatalf("seed %d: %s: %+v", 8600+i, tc.name, o)
				}
				tot.executions += o.executions
				tot.fences += o.fences
				tot.complete += o.complete
				tot.partial += o.partial
				tot.aborted += o.aborted
				tot.anchorWrong += o.anchorWrong
				tot.violations += o.violations
			}
			t.Logf("%s: %d executions, %d fences (%d anchored, %d unanchored/PARTIAL, %d aborted), anchors different from the owner's true state %d, executions with a bound violation %d",
				tc.name, tot.executions, tot.fences, tot.complete, tot.partial, tot.aborted, tot.anchorWrong, tot.violations)
			switch {
			case tot.complete == 0:
				t.Fatal("the executions must contain completed fences")
			case !tc.lie && tot.partial == 0:
				t.Fatal("the SET histories must contain fences whose evidence leaves the state open (unanchored)")
			case tc.lie && tc.mode == FenceAnchorEvidence && tot.aborted == 0:
				t.Fatal("a reset level must make fences abort")
			case tc.mode == FenceAnchorOwnerState && tot.violations == 0:
				t.Fatal("negative control: the owner-state anchor must violate the bound in some execution")
			}
		})
	}
}
