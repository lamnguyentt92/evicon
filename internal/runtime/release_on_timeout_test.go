package runtime

import (
	"context"
	"path/filepath"
	"testing"
	"time"

	"evicon/internal/device"
	"evicon/internal/journal"
	"evicon/internal/outbox"
	"evicon/internal/probe"
)

// F44 (release 077): the RELEASE_ON_TIMEOUT baseline is unsafe by design. A lost command is
// committed STOPPED after the timeout without owner evidence, its capacity is released, a new
// command is admitted into it, and the late original then takes effect: the plant (truth)
// exceeds the bound. EVICON on the same owner defers instead (F30), and the guard itself is
// unchanged (only rule R2 is broken).
func TestF44ReleaseOnTimeoutIsUnsafeByDesign(t *testing.T) {
	r := newDeviceRig(t, func(c *device.Config) {}) // K0: no dedup, no query, no stop, no fence
	d := journal.Domain{ID: "dose-1", Generation: 1, InstalledEpoch: 1, Dimension: 1, ComponentMin: []int64{0}, ComponentMax: []int64{12}, Anchor: []int64{0}, ContractHash: "device-test"}
	st, err := journal.Open(filepath.Join(t.TempDir(), "authority.db"))
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { st.Close() })
	disp := outbox.New(st, time.Hour, nil)
	a, err := NewAuthority(AuthorityConfig{Store: st, Domain: d, EndpointURL: r.srv.URL, Profile: "DEVICE", Policy: probe.ReleaseOnTimeout,
		Dispatcher: disp, ProbeBudget: 4, MaxOutstanding: 4, MaxConfigurations: 256, PlannerComputeBudget: 50 * time.Millisecond,
		EvidenceContract: r.dev.Contract(), ReleaseAfter: time.Millisecond})
	if err != nil {
		t.Fatal(err)
	}
	A := dose(t, "A", 8, 1)
	r.drop["A"] = true
	if code, out := invokeCandidate(t, a, A); code != 202 {
		t.Fatalf("A: %d %+v", code, out)
	}
	_ = disp.DispatchOnce(context.Background())
	if code, out := invokeCandidate(t, a, dose(t, "B", 8, 2)); code == 202 && out.Decision == "ADMITTED" {
		t.Fatalf("before the timeout the guard must block B (8 + 8 > 12): %+v", out)
	}
	time.Sleep(5 * time.Millisecond)
	n, err := a.ReleaseSweepOnce()
	if err != nil || n != 1 {
		t.Fatalf("release sweep: n=%d err=%v", n, err)
	}
	if ra, _ := st.GetCommand("dose-1", "A"); ra.Status != "STOPPED" {
		t.Fatalf("A must be committed STOPPED without owner evidence, got %s", ra.Status)
	}
	code, out := invokeCandidate(t, a, dose(t, "C", 8, 3))
	if code != 202 || out.Decision != "ADMITTED" {
		t.Fatalf("C must be admitted into the released capacity: %d %+v", code, out)
	}
	r.drop["A"] = false
	_ = disp.DispatchOnce(context.Background())
	if _, err := r.dev.Apply(A); err != nil { // the late original reaches the device
		t.Fatal(err)
	}
	if s := r.plant.Summary(); s.Devices[0].Level <= 12 {
		t.Fatalf("the baseline must be able to exceed the bound (late original + re-admitted capacity): %+v", s)
	}
}

// F45 (release 077): the RELEASE_ON_READ baseline (state-read reconciliation) is unsafe by
// design. Two commands are lost on the way back to the authority: A never reached the owner, B
// took effect and its reply was lost. The read finds B (APPLIED, recorded as the owner's answer)
// and does not find A, which the baseline takes for non-occurrence: A is committed STOPPED, its
// capacity is re-admitted, and the late original of A then exceeds the bound. Owner evidence of
// application (Q_A) does not certify that A will never run.
func TestF45ReleaseOnReadIsUnsafeByDesign(t *testing.T) {
	r := newDeviceRig(t, func(c *device.Config) { c.Query = true; c.DedupHorizon = -1 }) // K2: dedup, receipt query
	d := journal.Domain{ID: "dose-1", Generation: 1, InstalledEpoch: 1, Dimension: 1, ComponentMin: []int64{0}, ComponentMax: []int64{12}, Anchor: []int64{0}, ContractHash: "device-test"}
	st, err := journal.Open(filepath.Join(t.TempDir(), "authority.db"))
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { st.Close() })
	disp := outbox.New(st, time.Hour, nil)
	a, err := NewAuthority(AuthorityConfig{Store: st, Domain: d, EndpointURL: r.srv.URL, Profile: "DEVICE", Policy: probe.ReleaseOnRead,
		Dispatcher: disp, ProbeBudget: 4, MaxOutstanding: 4, MaxConfigurations: 256, PlannerComputeBudget: 50 * time.Millisecond,
		EvidenceContract: r.dev.Contract(), ReleaseAfter: time.Millisecond})
	if err != nil {
		t.Fatal(err)
	}
	A, B := dose(t, "A", 4, 1), dose(t, "B", 4, 2)
	r.drop["A"], r.drop["B"] = true, true
	for _, c := range []string{"A", "B"} {
		req := A
		if c == "B" {
			req = B
		}
		if code, out := invokeCandidate(t, a, req); code != 202 || out.Decision != "ADMITTED" {
			t.Fatalf("%s: %d %+v", c, code, out)
		}
	}
	_ = disp.DispatchOnce(context.Background())
	if _, err := r.dev.Apply(B); err != nil { // B took effect, its reply was lost
		t.Fatal(err)
	}
	if code, out := invokeCandidate(t, a, dose(t, "X", 8, 3)); code == 202 && out.Decision == "ADMITTED" {
		t.Fatalf("before the read the guard must block X (4 + 4 + 8 > 12): %+v", out)
	}
	time.Sleep(5 * time.Millisecond)
	n, err := a.ReleaseSweepOnce()
	if err != nil || n != 1 {
		t.Fatalf("read sweep: n=%d err=%v (want exactly A released)", n, err)
	}
	if ra, _ := st.GetCommand("dose-1", "A"); ra.Status != "STOPPED" {
		t.Fatalf("A (not found at the owner) must be committed STOPPED without evidence of non-occurrence, got %s", ra.Status)
	}
	if rb, _ := st.GetCommand("dose-1", "B"); rb.Status != "APPLIED" {
		t.Fatalf("B must be recorded APPLIED from the owner's receipt, got %s", rb.Status)
	}
	code, out := invokeCandidate(t, a, dose(t, "C", 8, 4))
	if code != 202 || out.Decision != "ADMITTED" {
		t.Fatalf("C must be admitted into A's released capacity: %d %+v", code, out)
	}
	r.drop["A"], r.drop["B"] = false, false
	_ = disp.DispatchOnce(context.Background())
	if _, err := r.dev.Apply(A); err != nil { // the late original of A reaches the owner
		t.Fatal(err)
	}
	if s := r.plant.Summary(); s.Devices[0].Level <= 12 {
		t.Fatalf("the baseline must be able to exceed the bound (late A + re-admitted capacity): %+v", s)
	}
}

// F46 (release 077): RELEASE_ON_READ releases a command
// whose owner cannot be reached (its reported state does not show the effect).
func TestF46ReleaseOnReadUnreachableOwner(t *testing.T) {
	r := newDeviceRig(t, func(c *device.Config) { c.Query = true; c.DedupHorizon = -1 })
	d := journal.Domain{ID: "dose-1", Generation: 1, InstalledEpoch: 1, Dimension: 1, ComponentMin: []int64{0}, ComponentMax: []int64{12}, Anchor: []int64{0}, ContractHash: "device-test"}
	st, err := journal.Open(filepath.Join(t.TempDir(), "authority.db"))
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { st.Close() })
	disp := outbox.New(st, time.Hour, nil)
	a, err := NewAuthority(AuthorityConfig{Store: st, Domain: d, EndpointURL: r.srv.URL, Profile: "DEVICE", Policy: probe.ReleaseOnRead,
		Dispatcher: disp, ProbeBudget: 4, MaxOutstanding: 4, MaxConfigurations: 256, PlannerComputeBudget: 50 * time.Millisecond,
		EvidenceContract: r.dev.Contract(), ReleaseAfter: time.Millisecond})
	if err != nil {
		t.Fatal(err)
	}
	r.drop["A"] = true
	if code, out := invokeCandidate(t, a, dose(t, "A", 4, 1)); code != 202 || out.Decision != "ADMITTED" {
		t.Fatalf("A: %d %+v", code, out)
	}
	_ = disp.DispatchOnce(context.Background())
	r.dev.SetOnline(false) // the owner does not answer reads while offline
	time.Sleep(5 * time.Millisecond)
	if n, err := a.ReleaseSweepOnce(); err != nil || n != 1 {
		t.Fatalf("read sweep with the owner offline: n=%d err=%v (want A released)", n, err)
	}
	if ra, _ := st.GetCommand("dose-1", "A"); ra.Status != "STOPPED" {
		t.Fatalf("A must be committed STOPPED, got %s", ra.Status)
	}
	if n, err := (&Authority{cfg: AuthorityConfig{Policy: probe.EVICON}}).ReleaseSweepOnce(); err != nil || n != 0 {
		t.Fatalf("the sweep must do nothing for EVICON: n=%d err=%v", n, err)
	}
}
