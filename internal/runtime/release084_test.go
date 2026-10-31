package runtime

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"sync/atomic"
	"testing"
	"time"

	"evicon/internal/contract"
	"evicon/internal/device"
	"evicon/internal/journal"
	"evicon/internal/outbox"
	"evicon/internal/plant"
	"evicon/internal/probe"
	"evicon/internal/protocol"
	"evicon/internal/resolve"
)

// orphanOn writes a crash orphan into a device journal before the device opens: a claim of id without a
// receipt (the crash fell between the effect and the receipt), as test F36 does.
func orphanOn(t *testing.T, c *device.Config, req protocol.CommandRequest) {
	t.Helper()
	rec := map[string]any{"k": "REC", "r": map[string]any{"id": req.CommandID, "hash": req.IntentHash, "seq": req.Sequence, "value": req.Value[0], "status": "CLAIMED", "at_ms": time.Now().UnixMilli()}}
	raw, _ := json.Marshal(rec)
	if err := os.MkdirAll(c.StateDir, 0o755); err != nil {
		t.Fatal(err)
	}
	f, err := os.OpenFile(filepath.Join(c.StateDir, c.DeviceID+".journal"), os.O_CREATE|os.O_APPEND|os.O_WRONLY, 0o644)
	if err != nil {
		t.Fatal(err)
	}
	defer f.Close()
	if _, err := f.Write(append(raw, '\n')); err != nil {
		t.Fatal(err)
	}
}

// barrierCounter puts a counting proxy in front of the rig: every POST /v1/barriers is counted.
func barrierCounter(t *testing.T, r *deviceRig) (*int64, string) {
	t.Helper()
	var n int64
	target := r.srv.URL
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, req *http.Request) {
		if req.URL.Path == "/v1/barriers" {
			atomic.AddInt64(&n, 1)
		}
		out, err := http.NewRequest(req.Method, target+req.URL.RequestURI(), req.Body)
		if err != nil {
			http.Error(w, err.Error(), 500)
			return
		}
		out.Header = req.Header.Clone()
		resp, err := http.DefaultClient.Do(out)
		if err != nil {
			http.Error(w, err.Error(), 502)
			return
		}
		defer resp.Body.Close()
		for k, v := range resp.Header {
			w.Header()[k] = v
		}
		w.WriteHeader(resp.StatusCode)
		buf := make([]byte, 32<<10)
		for {
			m, er := resp.Body.Read(buf)
			if m > 0 {
				_, _ = w.Write(buf[:m])
			}
			if er != nil {
				break
			}
		}
	}))
	t.Cleanup(srv.Close)
	return &n, srv.URL
}

// The reviewer's example (M3), on the emulated owner with a fence only (K0F): A (2) is a crash orphan
// whose effect happened, C (2) took effect with its reply lost, B (4) was lost on the way. A candidate D
// (8) does not fit while A, B, C are unresolved (2+4+2+8 = 16 > 12). The fence's answer is complete: A
// UNCERTAIN, C APPLIED, B unlisted.
func f52Scenario(t *testing.T, intake string) (*Authority, *journal.Store, *deviceRig, protocol.AuthorityResponse) {
	t.Helper()
	A, B, C, D := dose(t, "A", 2, 1), dose(t, "B", 4, 2), dose(t, "C", 2, 3), dose(t, "D", 8, 4)
	r := newDeviceRig(t, func(c *device.Config) { c.Fence = true; orphanOn(t, c, A) })
	if res := r.plant.Apply(plant.ApplyRequest{Device: "dose-1", Cmd: "A", Value: 2}); !res.OK { // the orphan's effect happened
		t.Fatalf("plant: %+v", res)
	}
	a, st, disp := rig083(t, r, probe.EVICON, 12, 8, func(c *AuthorityConfig) {
		c.FenceIntake = intake
		c.ResolvePolicy = resolve.Policy{Mode: resolve.ModeImmediate}
		c.ResolveCosts = resolve.Costs{HoldPerUnitPerSec: 1, StopCost: 0.001, FenceCost: 0.001}
	})
	for _, x := range []protocol.CommandRequest{A, B, C} {
		r.drop[x.CommandID] = true
		if code, out := invokeCandidate(t, a, x); code != 202 {
			t.Fatalf("%s: %d %+v", x.CommandID, code, out)
		}
	}
	_ = disp.DispatchOnce(context.Background())
	if rc, err := r.dev.Apply(C); err != nil || rc.Outcome != "APPLIED" { // C took effect; its reply was lost
		t.Fatalf("C at the owner: %+v %v", rc, err)
	}
	time.Sleep(5 * time.Millisecond)
	_, out := invokeCandidate(t, a, D)
	return a, st, r, out
}

// F52 (release 084, rule R5', PREREG_extra_084.md (a)): per-identity fence intake. Under R5 (the default)
// the fence aborts and nothing is resolved (F36); under R5' the authority takes in C's APPLIED answer,
// seals the unlisted B as STOPPED, keeps the orphan A pending and owner-uncertain, admits D (A + C + D =
// 12), and the plant (independent truth) never exceeds the bound. A late copy of B never takes effect.
func TestF52PerIdentityFenceIntake(t *testing.T) {
	t.Run("all-or-nothing (R5, default)", func(t *testing.T) {
		a, st, _, out := f52Scenario(t, "")
		if out.Decision == "ADMITTED" {
			t.Fatalf("under R5 the fence aborts, D must not be admitted: %+v", out)
		}
		for id, want := range map[string]string{"A": "PENDING", "B": "PENDING", "C": "PENDING"} {
			if c, _ := st.GetCommand("dose-1", id); c.Status != want {
				t.Fatalf("R5: %s is %s, want %s", id, c.Status, want)
			}
		}
		if !a.isOwnerUncertain("A") || a.isFenceUncertain("A") {
			t.Fatal("R5: A is owner-uncertain (30 s back-off), never fence-uncertain")
		}
	})
	t.Run("per-identity (R5')", func(t *testing.T) {
		a, st, r, out := f52Scenario(t, FenceIntakePerIdentity)
		if out.Decision != "ADMITTED" {
			t.Fatalf("under R5' D fits once B is STOPPED and C APPLIED: %+v", out)
		}
		for id, want := range map[string]string{"A": "PENDING", "B": "STOPPED", "C": "APPLIED", "D": "PENDING"} {
			if c, _ := st.GetCommand("dose-1", id); c.Status != want {
				t.Fatalf("R5': %s is %s, want %s", id, c.Status, want)
			}
		}
		if !a.isOwnerUncertain("A") || !a.isFenceUncertain("A") {
			t.Fatal("R5': the orphan stays pending, owner-uncertain and out of the targets")
		}
		dom, _ := st.GetDomain("dose-1")
		if dom.CutoffSequence != 0 {
			t.Fatalf("R5' anchors nothing (A is unresolved at or below the cutoff): cutoff %d", dom.CutoffSequence)
		}
		if _, pending, _ := st.PendingBarrier("dose-1"); pending {
			t.Fatal("R5': the domain is un-paused as on completion")
		}
		if late, err := r.dev.Apply(dose(t, "B", 4, 2)); err != nil || late.Outcome != "STOPPED" { // a late copy of B
			t.Fatalf("late B: %+v %v", late, err)
		}
		r.drop["D"] = false
		_, _, disp := a, st, a.cfg.Dispatcher
		_ = disp.DispatchOnce(context.Background())
		s := r.plant.Summary()
		if s.Devices[0].MaxLevel > 12 || s.Devices[0].Level != 12 {
			t.Fatalf("plant truth (A 2 + C 2 + D 8 = 12): %+v", s)
		}
		if c, _ := st.GetCommand("dose-1", "B"); c.Status != "STOPPED" {
			t.Fatal("B")
		}
	})
}

// F53 (release 084, rule R5'): the resolver does not loop and EVICON_FENCE_BLOCKED_BY_OWNER_UNCERTAIN does
// not block per-identity intake. After A is listed UNCERTAIN, (i) sweeps send no fence while A is the only
// unresolved identity (a fence would cover only owner-uncertain identities) and leave A alone even after
// the 30 s owner-uncertain back-off would have expired; (ii) a new lost command E with a blocked candidate
// is fenced under R5' (E sealed STOPPED, A still pending), while under R5 the resolver refuses to fence
// (EVICON_FENCE_BLOCKED_BY_OWNER_UNCERTAIN on a contract without Q_S).
func TestF53PerIdentityIntakeDoesNotLoopOrBlock(t *testing.T) {
	for _, intake := range []string{FenceIntakeAllOrNothing, FenceIntakePerIdentity} {
		t.Run(intake, func(t *testing.T) {
			A, E := dose(t, "A", 2, 1), dose(t, "E", 4, 2)
			r := newDeviceRig(t, func(c *device.Config) { c.Fence = true; orphanOn(t, c, A) })
			n, url := barrierCounter(t, r)
			a, st, disp := rig083(t, r, probe.EVICON, 8, 8, func(c *AuthorityConfig) {
				c.FenceIntake = intake
				c.EndpointURL = url
				c.ResolvePolicy = resolve.Policy{Mode: resolve.ModeImmediate}
				c.ResolveCosts = resolve.Costs{HoldPerUnitPerSec: 1, StopCost: 0.001, FenceCost: 0.001}
			})
			r.drop["A"] = true
			invokeCandidate(t, a, A)
			_ = disp.DispatchOnce(context.Background())
			time.Sleep(5 * time.Millisecond)
			if err := a.SweepOnce(); err != nil { // first fence: A listed UNCERTAIN
				t.Fatal(err)
			}
			if got := atomic.LoadInt64(n); got != 1 {
				t.Fatalf("one fence expected, got %d", got)
			}
			if intake == FenceIntakePerIdentity {
				// pretend the 30 s back-off is over: A must stay out of the targets under R5'
				a.queriedMu.Lock()
				a.ownerUncertain["A"] = time.Now().Add(-2 * OwnerUncertainRetry)
				a.queriedMu.Unlock()
				rev0, _ := st.GetDomain("dose-1")
				for i := 0; i < 5; i++ {
					if err := a.SweepOnce(); err != nil {
						t.Fatal(err)
					}
				}
				rev1, _ := st.GetDomain("dose-1")
				if got := atomic.LoadInt64(n); got != 1 || rev1.Revision != rev0.Revision {
					t.Fatalf("no fence may be sent while only the orphan is unresolved: fences %d, revision %d -> %d", got, rev0.Revision, rev1.Revision)
				}
			}
			// a new lost command E and a candidate F that does not fit (A 2 + E 4 + F 4 = 10 > 8)
			r.drop["E"] = true
			invokeCandidate(t, a, E)
			_ = disp.DispatchOnce(context.Background())
			time.Sleep(5 * time.Millisecond)
			_, out := invokeCandidate(t, a, dose(t, "F", 4, 3))
			e, _ := st.GetCommand("dose-1", "E")
			ac, _ := st.GetCommand("dose-1", "A")
			if ac.Status != "PENDING" {
				t.Fatalf("the orphan A is never resolved by a fence: %s", ac.Status)
			}
			if intake == FenceIntakePerIdentity {
				if out.Decision != "ADMITTED" || e.Status != "STOPPED" || atomic.LoadInt64(n) != 2 {
					t.Fatalf("R5' fences E despite the owner-uncertain A: decision %+v, E %s, fences %d", out, e.Status, atomic.LoadInt64(n))
				}
			} else {
				if out.Decision == "ADMITTED" || e.Status != "PENDING" || !strings.Contains(out.Reason, "EVICON_FENCE_BLOCKED_BY_OWNER_UNCERTAIN") {
					t.Fatalf("R5 refuses to fence while A is owner-uncertain: decision %+v, E %s", out, e.Status)
				}
			}
			if s := r.plant.Summary(); len(s.Devices) > 0 && s.Devices[0].MaxLevel > 8 {
				t.Fatalf("plant truth above the bound: %+v", s)
			}
		})
	}
}

// fakeFenceOwner answers POST /v1/barriers with a crafted complete answer (and 404 to anything else).
func fakeFenceOwner(t *testing.T, answer func(protocol.BarrierRequest) protocol.BarrierReceipt) string {
	t.Helper()
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, req *http.Request) {
		if req.URL.Path != "/v1/barriers" {
			http.Error(w, "not found", 404)
			return
		}
		var br protocol.BarrierRequest
		_ = json.NewDecoder(req.Body).Decode(&br)
		w.Header().Set("content-type", "application/json")
		_ = json.NewEncoder(w).Encode(answer(br))
	}))
	t.Cleanup(srv.Close)
	return srv.URL
}

// F53b (release 084, rule R5'): evidence conflicts and invalid receipts abort under per-identity intake
// exactly as under R5, and a token that completed with per-identity intake keeps its meaning (a retry
// answers BARRIER_PARTIAL; the resolver uses a fresh token).
func TestF53bPerIdentityIntakeStillAbortsOnConflictsAndInvalidReceipts(t *testing.T) {
	A, B, X := dose(t, "A", 1, 1), dose(t, "B", 1, 2), dose(t, "X", 1, 3)
	for _, tc := range []struct {
		name  string
		setup func(st *journal.Store)
		recs  func() []protocol.Receipt
		want  string
	}{
		{"conflict", func(st *journal.Store) {
			if _, err := st.RecordEvidence(journal.ReceiptRecord{DomainID: "dose-1", CommandID: "X", IntentHash: X.IntentHash, Outcome: "STOPPED", OwnerVersion: 1, ReceiptVersion: 1, State: []int64{0}}, "TEST", "TEST", true); err != nil {
				t.Fatal(err)
			}
		}, func() []protocol.Receipt {
			return []protocol.Receipt{
				{SchemaVersion: 1, DomainID: "dose-1", Generation: 1, CommandID: "A", IntentHash: A.IntentHash, Outcome: "UNCERTAIN"},
				{SchemaVersion: 1, DomainID: "dose-1", Generation: 1, CommandID: "X", IntentHash: X.IntentHash, Outcome: "APPLIED", OwnerVersion: 5, ReceiptVersion: 5, State: []int64{1}}}
		}, "EVIDENCE_CONFLICT"},
		{"invalid receipt", nil, func() []protocol.Receipt {
			return []protocol.Receipt{
				{SchemaVersion: 1, DomainID: "dose-1", Generation: 1, CommandID: "A", IntentHash: A.IntentHash, Outcome: "UNCERTAIN"},
				{SchemaVersion: 1, DomainID: "dose-1", Generation: 1, CommandID: "B", IntentHash: "not-the-intent", Outcome: "APPLIED", OwnerVersion: 5, ReceiptVersion: 5, State: []int64{1}}}
		}, "INVALID_BARRIER_RECEIPT"},
		{"partial", nil, func() []protocol.Receipt {
			return []protocol.Receipt{{SchemaVersion: 1, DomainID: "dose-1", Generation: 1, CommandID: "A", IntentHash: A.IntentHash, Outcome: "UNCERTAIN"}}
		}, ""},
	} {
		t.Run(tc.name, func(t *testing.T) {
			url := fakeFenceOwner(t, func(br protocol.BarrierRequest) protocol.BarrierReceipt {
				return protocol.BarrierReceipt{SchemaVersion: 1, DomainID: "dose-1", Generation: 1, Token: br.Token, CutoffSequence: br.CutoffSequence,
					OwnerVersion: 9, State: []int64{1}, Receipts: tc.recs()}
			})
			st, err := journal.Open(filepath.Join(t.TempDir(), "a.db"))
			if err != nil {
				t.Fatal(err)
			}
			defer st.Close()
			d := journal.Domain{ID: "dose-1", Generation: 1, InstalledEpoch: 1, Dimension: 1, ComponentMin: []int64{0}, ComponentMax: []int64{10}, Anchor: []int64{0}, ContractHash: "t"}
			a, err := NewAuthority(AuthorityConfig{Store: st, Domain: d, EndpointURL: url, Profile: "DEVICE", Policy: probe.EVICON, Dispatcher: outbox.New(st, time.Hour, nil),
				ProbeBudget: 4, MaxOutstanding: 8, EvidenceContract: fenceOnlyContract(), FenceIntake: FenceIntakePerIdentity})
			if err != nil {
				t.Fatal(err)
			}
			for _, x := range []protocol.CommandRequest{A, B, X} {
				if code, out := invokeCandidate(t, a, x); code != 202 {
					t.Fatalf("%s: %d %+v", x.CommandID, code, out)
				}
			}
			if tc.setup != nil {
				tc.setup(st)
			}
			a.mu.Lock()
			status, out := a.runBarrierLocked(protocol.BarrierRequest{SchemaVersion: 1, DomainID: "dose-1", Generation: 1, Token: "t1"})
			a.mu.Unlock()
			raw, _ := json.Marshal(out)
			if tc.want != "" {
				if status != 409 || !strings.Contains(string(raw), tc.want) {
					t.Fatalf("want 409 %s, got %d %s", tc.want, status, raw)
				}
				lc, _ := st.GetBarrierLifecycle("dose-1", 1, "t1")
				if lc.Status != "ABORTED" {
					t.Fatalf("the fence must be aborted, got %s", lc.Status)
				}
				for _, id := range []string{"A", "B"} {
					if c, _ := st.GetCommand("dose-1", id); c.Status != "PENDING" {
						t.Fatalf("an aborted fence resolves nothing: %s %s", id, c.Status)
					}
				}
				return
			}
			if status != 200 || !strings.Contains(string(raw), "PER_IDENTITY") {
				t.Fatalf("partial intake: %d %s", status, raw)
			}
			for id, want := range map[string]string{"A": "PENDING", "B": "STOPPED", "X": "STOPPED"} {
				if c, _ := st.GetCommand("dose-1", id); c.Status != want {
					t.Fatalf("%s is %s, want %s", id, c.Status, want)
				}
			}
			lc, _ := st.GetBarrierLifecycle("dose-1", 1, "t1")
			if lc.Status != "PARTIAL" {
				t.Fatalf("lifecycle %s, want PARTIAL", lc.Status)
			}
			a.mu.Lock()
			status, out = a.runBarrierLocked(protocol.BarrierRequest{SchemaVersion: 1, DomainID: "dose-1", Generation: 1, Token: "t1"})
			a.mu.Unlock()
			raw, _ = json.Marshal(out)
			if status != 409 || !strings.Contains(string(raw), "BARRIER_PARTIAL") {
				t.Fatalf("a retry of a PARTIAL token keeps its meaning: %d %s", status, raw)
			}
		})
	}
}

// F54 (release 084, PREREG_extra_084.md (c)): PERIODIC_FULL. A blocked candidate is answered at once (no
// certificate search, no query at admission); the sweep queries the aged unresolved commands (Q_A), then
// fences (F) those the queries left unresolved, within the probe budget, and EVICON's threshold rule
// decides when; on a stop-only owner it stops instead.
func TestF54PeriodicFull(t *testing.T) {
	t.Run("K3F: query, then fence", func(t *testing.T) {
		A, C := dose(t, "A", 4, 1), dose(t, "C", 4, 2)
		r := newDeviceRig(t, func(c *device.Config) { c.DedupHorizon = -1; c.Query = true; c.Fence = true })
		n, url := barrierCounter(t, r)
		a, st, disp := rig083(t, r, probe.EVICON, 10, 8, func(c *AuthorityConfig) {
			c.EndpointURL = url
			c.ResolveTrigger = TriggerPeriodicFull
			c.ResolvePolicy = resolve.Policy{Mode: resolve.ModeImmediate, QueryFirst: true}
			c.ResolveCosts = resolve.Costs{HoldPerUnitPerSec: 1, StopCost: 0.001, FenceCost: 0.001}
		})
		for _, x := range []protocol.CommandRequest{A, C} {
			r.drop[x.CommandID] = true
			invokeCandidate(t, a, x)
		}
		_ = disp.DispatchOnce(context.Background())
		if rc, err := r.dev.Apply(C); err != nil || rc.Outcome != "APPLIED" {
			t.Fatalf("C: %+v %v", rc, err)
		}
		time.Sleep(5 * time.Millisecond)
		_, out := invokeCandidate(t, a, dose(t, "D", 4, 3)) // 4 + 4 + 4 > 10: blocked, answered at once
		if out.Decision == "ADMITTED" || strings.Contains(out.Reason, "EVICON") || len(a.queriedAt) != 0 || atomic.LoadInt64(n) != 0 {
			t.Fatalf("PERIODIC_FULL has no admission trigger: %+v, queries %d, fences %d", out, len(a.queriedAt), atomic.LoadInt64(n))
		}
		if err := a.SweepOnce(); err != nil {
			t.Fatal(err)
		}
		if len(a.queriedAt) != 2 || atomic.LoadInt64(n) != 1 {
			t.Fatalf("one sweep: query both, then fence: queries %d, fences %d", len(a.queriedAt), atomic.LoadInt64(n))
		}
		for id, want := range map[string]string{"A": "STOPPED", "C": "APPLIED"} {
			if c, _ := st.GetCommand("dose-1", id); c.Status != want {
				t.Fatalf("%s is %s, want %s", id, c.Status, want)
			}
		}
		if _, out := invokeCandidate(t, a, dose(t, "D2", 4, 4)); out.Decision != "ADMITTED" {
			t.Fatalf("after the sweep the candidate fits: %+v", out)
		}
	})
	t.Run("budget: one owner call kept for the fence", func(t *testing.T) {
		r := newDeviceRig(t, func(c *device.Config) { c.DedupHorizon = -1; c.Query = true; c.Fence = true })
		n, url := barrierCounter(t, r)
		a, _, disp := rig083(t, r, probe.EVICON, 100, 8, func(c *AuthorityConfig) {
			c.EndpointURL = url
			c.ProbeBudget = 2
			c.ResolveTrigger = TriggerPeriodicFull
			c.ResolvePolicy = resolve.Policy{Mode: resolve.ModeImmediate, QueryFirst: true}
			c.ResolveCosts = resolve.Costs{HoldPerUnitPerSec: 1, StopCost: 0.001, FenceCost: 0.001}
		})
		for i, id := range []string{"A", "B", "C"} {
			r.drop[id] = true
			invokeCandidate(t, a, dose(t, id, 1, uint64(i+1)))
		}
		_ = disp.DispatchOnce(context.Background())
		time.Sleep(5 * time.Millisecond)
		if err := a.SweepOnce(); err != nil {
			t.Fatal(err)
		}
		if len(a.queriedAt) != 1 || atomic.LoadInt64(n) != 1 {
			t.Fatalf("budget 2: one query and one fence, got queries %d, fences %d", len(a.queriedAt), atomic.LoadInt64(n))
		}
	})
	t.Run("K0S: stop", func(t *testing.T) {
		r := newDeviceRig(t, func(c *device.Config) { c.Stop = true })
		a, st, disp := rig083(t, r, probe.EVICON, 10, 8, func(c *AuthorityConfig) {
			c.ResolveTrigger = TriggerPeriodicFull
			c.ResolvePolicy = resolve.Policy{Mode: resolve.ModeImmediate, QueryFirst: true}
			c.ResolveCosts = resolve.Costs{HoldPerUnitPerSec: 1, StopCost: 0.001, FenceCost: 0.001}
		})
		r.drop["A"] = true
		invokeCandidate(t, a, dose(t, "A", 4, 1))
		_ = disp.DispatchOnce(context.Background())
		time.Sleep(5 * time.Millisecond)
		if err := a.SweepOnce(); err != nil {
			t.Fatal(err)
		}
		if c, _ := st.GetCommand("dose-1", "A"); c.Status != "STOPPED" {
			t.Fatalf("A is %s, want STOPPED", c.Status)
		}
	})
	t.Run("threshold rule", func(t *testing.T) {
		// ski rental with fence cost 5 and hold cost 1 per unit-second: a 4-unit target waits 1.25 s
		r := newDeviceRig(t, func(c *device.Config) { c.Fence = true })
		n, url := barrierCounter(t, r)
		a, st, disp := rig083(t, r, probe.EVICON, 10, 8, func(c *AuthorityConfig) {
			c.EndpointURL = url
			c.ResolveTrigger = TriggerPeriodicFull
			c.ResolvePolicy = resolve.Policy{Mode: resolve.ModeSkiRental, QueryFirst: true}
			c.ResolveCosts = resolve.Costs{HoldPerUnitPerSec: 1, StopCost: 1, FenceCost: 5}
		})
		r.drop["A"] = true
		invokeCandidate(t, a, dose(t, "A", 4, 1))
		_ = disp.DispatchOnce(context.Background())
		if err := a.SweepOnce(); err != nil {
			t.Fatal(err)
		}
		if atomic.LoadInt64(n) != 0 {
			t.Fatal("below the threshold PERIODIC_FULL waits")
		}
		time.Sleep(1300 * time.Millisecond)
		if err := a.SweepOnce(); err != nil {
			t.Fatal(err)
		}
		if c, _ := st.GetCommand("dose-1", "A"); atomic.LoadInt64(n) != 1 || c.Status != "STOPPED" {
			t.Fatalf("past the threshold PERIODIC_FULL fences: fences %d, A %s", atomic.LoadInt64(n), c.Status)
		}
	})
}

func TestFenceIntakeValidation084(t *testing.T) {
	st, err := journal.Open(filepath.Join(t.TempDir(), "a.db"))
	if err != nil {
		t.Fatal(err)
	}
	defer st.Close()
	d := journal.Domain{ID: "x", Generation: 1, InstalledEpoch: 1, Dimension: 1, ComponentMax: []int64{1}, Anchor: []int64{0}, ContractHash: "t"}
	if _, err := NewAuthority(AuthorityConfig{Store: st, Domain: d, Profile: "DEVICE", Policy: probe.EVICON, EvidenceContract: fenceOnlyContract(), FenceIntake: "partial"}); err == nil {
		t.Fatal("an unknown fence intake must be refused")
	}
	a, err := NewAuthority(AuthorityConfig{Store: st, Domain: d, Profile: "DEVICE", Policy: probe.EVICON, EvidenceContract: fenceOnlyContract()})
	if err != nil || a.cfg.FenceIntake != FenceIntakeAllOrNothing || a.cfg.ResolveTrigger != TriggerBlockAndSweep {
		t.Fatalf("defaults must stay R5 and BLOCK_AND_SWEEP: %v %+v", err, a.cfg)
	}
}

func fenceOnlyContract() contract.EvidenceContract {
	return contract.EvidenceContract{SchemaVersion: 1, ID: "fence-only-test", System: "test owner", Source: contract.SourceRuntimeVerified,
		Capabilities: []contract.EvidenceCapability{contract.CapFence}, VerifiedTestIDs: []string{"F53b"}}
}
