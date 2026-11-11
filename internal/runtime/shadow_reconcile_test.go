package runtime

// Release 086 (PREREG_extra_086.md SH; reviewer comment M3): SHADOW_RECONCILE, the documented device-shadow
// reconciliation loop (shadow_reconcile.go).
//
//   TestShadowReconcilePolicy086        the policy is parsed, has the reconciliation sweep and no resolver on a block;
//                                        the authority wires its Republisher into the dispatcher's retry guard
//   TestShadowReconcileK2Safe086        K2 (dedup without a horizon, receipt read): a lost command and an applied
//                                        command with a lost reply. The read records the applied one, publishes the
//                                        lost one again (also after the retry guard's window), never releases; the
//                                        late original is deduplicated; the bound holds; the next read waits for
//                                        the next reconciliation timeout
//   TestShadowReconcileK0Unsafe086      negative control, K0 (no dedup, no read): the same loop re-publishes an applied
//                                        command whose reply was lost and the owner executes it twice

import (
	"context"
	"path/filepath"
	"testing"
	"time"

	"evicon/internal/device"
	"evicon/internal/journal"
	"evicon/internal/outbox"
	"evicon/internal/probe"
	"evicon/internal/protocol"
)

func TestShadowReconcilePolicy086(t *testing.T) {
	m, err := probe.ParseMethod("shadow_reconcile")
	if err != nil || m != probe.ShadowReconcile {
		t.Fatalf("ParseMethod: %v %v", m, err)
	}
	if !probe.HasReconcileSweep(m) || probe.IsReleaseBaseline(m) || !probe.IsShadowReconcile(m) {
		t.Fatal("SHADOW_RECONCILE has the reconciliation sweep and is not a release baseline")
	}
	st, err := journal.Open(filepath.Join(t.TempDir(), "a.db"))
	if err != nil {
		t.Fatal(err)
	}
	defer st.Close()
	disp := outbox.New(st, time.Hour, nil)
	base := 0
	disp.RetryAllowed = func(string, string, int64) bool { base++; return false }
	d := journal.Domain{ID: "dose-1", Generation: 1, InstalledEpoch: 1, Dimension: 1, Anchor: []int64{0}}
	a, err := NewAuthority(AuthorityConfig{Store: st, Domain: d, Profile: "E2", Policy: probe.ShadowReconcile, Dispatcher: disp})
	if err != nil {
		t.Fatal(err)
	}
	if a.cfg.Republish == nil || a.cfg.ReleaseAfter != 10*time.Second {
		t.Fatalf("Republisher %v, ReleaseAfter %s (want 10 s)", a.cfg.Republish, a.cfg.ReleaseAfter)
	}
	if disp.RetryAllowed("dose-1", "x", 1) || base != 1 {
		t.Fatal("without a request the retry guard decides")
	}
	a.cfg.Republish.Request("dose-1", "x")
	if !disp.RetryAllowed("dose-1", "x", 1) || base != 1 {
		t.Fatal("a requested re-publish is allowed once, whatever the guard says")
	}
	if disp.RetryAllowed("dose-1", "x", 2) || base != 2 {
		t.Fatal("the request is used up")
	}
}

func shadowAuthority(t *testing.T, r *deviceRig, every time.Duration) (*Authority, *journal.Store, *outbox.Dispatcher) {
	t.Helper()
	d := journal.Domain{ID: "dose-1", Generation: 1, InstalledEpoch: 1, Dimension: 1, ComponentMin: []int64{0}, ComponentMax: []int64{12}, Anchor: []int64{0}, ContractHash: "device-test"}
	st, err := journal.Open(filepath.Join(t.TempDir(), "authority.db"))
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { st.Close() })
	disp := outbox.New(st, time.Hour, nil)
	// the campaign's retry guard after its window (resend_until): no resend of its own any more
	disp.RetryAllowed = func(string, string, int64) bool { return false }
	a, err := NewAuthority(AuthorityConfig{Store: st, Domain: d, EndpointURL: r.srv.URL, Profile: "DEVICE", Policy: probe.ShadowReconcile,
		Dispatcher: disp, ProbeBudget: 4, MaxOutstanding: 4, MaxConfigurations: 256, PlannerComputeBudget: 50 * time.Millisecond,
		EvidenceContract: r.dev.Contract(), ReleaseAfter: every})
	if err != nil {
		t.Fatal(err)
	}
	return a, st, disp
}

func TestShadowReconcileK2Safe086(t *testing.T) {
	r := newDeviceRig(t, func(c *device.Config) { c.Query = true; c.DedupHorizon = -1 }) // K2
	// release 088: a 1 s reconciliation timeout (086: 30 ms). The steps before the first sweep write the journal with
	// synchronous=FULL; on a VM disk with slow fsync they took longer than 30 ms and the first sweep already read.
	a, st, disp := shadowAuthority(t, r, time.Second)
	A, B := dose(t, "A", 4, 1), dose(t, "B", 4, 2)
	r.drop["A"], r.drop["B"] = true, true
	for _, c := range []protocol.CommandRequest{A, B} {
		if code, out := invokeCandidate(t, a, c); code != 202 || out.Decision != "ADMITTED" {
			t.Fatalf("%s: %d %+v", c.CommandID, code, out)
		}
	}
	_ = disp.DispatchOnce(context.Background())
	if _, err := r.dev.Apply(B); err != nil { // B took effect, its reply was lost
		t.Fatal(err)
	}
	if code, out := invokeCandidate(t, a, dose(t, "X", 8, 3)); code == 202 && out.Decision == "ADMITTED" {
		t.Fatalf("the guard must block X (4 + 4 + 8 > 12): %+v", out)
	}
	if n, err := a.ReleaseSweepOnce(); err != nil || n != 0 {
		t.Fatalf("before the reconciliation timeout nothing is read: n=%d %v", n, err)
	}
	time.Sleep(1100 * time.Millisecond)
	n, err := a.ReleaseSweepOnce()
	if err != nil || n != 1 {
		t.Fatalf("reconciliation: n=%d err=%v (want A published again)", n, err)
	}
	if rb, _ := st.GetCommand("dose-1", "B"); rb.Status != "APPLIED" {
		t.Fatalf("B must be recorded APPLIED from the reported state, got %s", rb.Status)
	}
	if ra, _ := st.GetCommand("dose-1", "A"); ra.Status != "PENDING" {
		t.Fatalf("A must stay unresolved (never released), got %s", ra.Status)
	}
	if code, out := invokeCandidate(t, a, dose(t, "C", 8, 4)); code == 202 && out.Decision == "ADMITTED" {
		t.Fatalf("nothing is released: C must stay blocked (4 + 4 + 8 > 12): %+v", out)
	}
	if n, _ := a.ReleaseSweepOnce(); n != 0 {
		t.Fatalf("A was reconciled just now: the next read waits for the next timeout (n=%d)", n)
	}
	r.drop["A"] = false
	_ = disp.DispatchOnce(context.Background()) // the re-publish (the retry guard alone would not send it)
	if ra, _ := st.GetCommand("dose-1", "A"); ra.Status != "APPLIED" {
		t.Fatalf("the re-published A must take effect and be recorded, got %s", ra.Status)
	}
	if _, err := r.dev.Apply(A); err != nil { // a late copy of the original reaches the device: deduplicated
		t.Fatal(err)
	}
	s := r.plant.Summary()
	if s.Devices[0].Level != 8 || s.Duplicates != 0 {
		t.Fatalf("each command takes effect once (level 8, no duplicate): %+v", s)
	}
	if a.cfg.Republish.Requested() != 1 {
		t.Fatalf("one re-publish request, got %d", a.cfg.Republish.Requested())
	}
}

func TestShadowReconcileK0Unsafe086(t *testing.T) {
	r := newDeviceRig(t, func(c *device.Config) {}) // K0: no dedup, no receipt read
	a, _, disp := shadowAuthority(t, r, 10*time.Millisecond)
	B := dose(t, "B", 5, 1)
	r.drop["B"] = true
	if code, out := invokeCandidate(t, a, B); code != 202 || out.Decision != "ADMITTED" {
		t.Fatalf("B: %d %+v", code, out)
	}
	_ = disp.DispatchOnce(context.Background())
	if _, err := r.dev.Apply(B); err != nil { // B took effect, its reply was lost
		t.Fatal(err)
	}
	time.Sleep(20 * time.Millisecond)
	if n, err := a.ReleaseSweepOnce(); err != nil || n != 1 {
		t.Fatalf("the reported state cannot show B (no read on K0): re-publish expected, n=%d %v", n, err)
	}
	r.drop["B"] = false
	_ = disp.DispatchOnce(context.Background())
	if s := r.plant.Summary(); s.Duplicates == 0 {
		t.Fatalf("negative control: without dedup the re-publish must execute B twice: %+v", s)
	}
}
