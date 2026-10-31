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
	"evicon/internal/resolve"
)

func rig083(t *testing.T, r *deviceRig, pol probe.Method, bound int64, window int, mut func(*AuthorityConfig)) (*Authority, *journal.Store, *outbox.Dispatcher) {
	t.Helper()
	d := journal.Domain{ID: "dose-1", Generation: 1, InstalledEpoch: 1, Dimension: 1, ComponentMin: []int64{0}, ComponentMax: []int64{bound}, Anchor: []int64{0}, ContractHash: "device-test"}
	st, err := journal.Open(filepath.Join(t.TempDir(), "authority.db"))
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { st.Close() })
	disp := outbox.New(st, time.Hour, nil)
	cfg := AuthorityConfig{Store: st, Domain: d, EndpointURL: r.srv.URL, Profile: "DEVICE", Policy: pol, Dispatcher: disp,
		ProbeBudget: 4, MaxOutstanding: window, MaxConfigurations: 256, PlannerComputeBudget: 50 * time.Millisecond,
		EvidenceContract: r.dev.Contract(), AmbiguityAfter: time.Millisecond}
	if mut != nil {
		mut(&cfg)
	}
	a, err := NewAuthority(cfg)
	if err != nil {
		t.Fatal(err)
	}
	return a, st, disp
}

// F49 (release 083, T1): M1W is M1 plus EVICON's window-full trigger. Two commands took effect with their
// replies lost and fill the window (max-outstanding 2: the candidate would be the third tracked command). M1 defers the
// candidate (a full window is a plain deferral for it) and never queries; M1W queries the two aged
// ambiguous commands, records their APPLIED receipts (Q_A), and admits the candidate in the freed slots.
// Neither stops or fences, and the guard is unchanged (the bound is never at risk here).
func TestF49QueryOnlyWindowTrigger(t *testing.T) {
	for _, tc := range []struct {
		pol      probe.Method
		admitted bool
	}{{probe.M1, false}, {probe.M1W, true}} {
		t.Run(string(tc.pol), func(t *testing.T) {
			r := newDeviceRig(t, func(c *device.Config) { c.Query = true; c.DedupHorizon = -1 }) // K2
			a, st, disp := rig083(t, r, tc.pol, 100, 2, nil)
			A, B := dose(t, "A", 2, 1), dose(t, "B", 2, 2)
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
			if _, err := r.dev.Apply(A); err != nil { // took effect, the reply was lost
				t.Fatal(err)
			}
			if _, err := r.dev.Apply(B); err != nil {
				t.Fatal(err)
			}
			time.Sleep(5 * time.Millisecond)
			code, out := invokeCandidate(t, a, dose(t, "C", 2, 3))
			if got := code == 202 && out.Decision == "ADMITTED"; got != tc.admitted {
				t.Fatalf("%s: C admitted=%v, want %v (%d %+v)", tc.pol, got, tc.admitted, code, out)
			}
			ra, _ := st.GetCommand("dose-1", "A")
			rb, _ := st.GetCommand("dose-1", "B")
			if tc.admitted && (ra.Status != "APPLIED" || rb.Status != "APPLIED") {
				t.Fatalf("M1W must record the owner's APPLIED receipts: A=%s B=%s", ra.Status, rb.Status)
			}
			if !tc.admitted && (ra.Status == "APPLIED" || rb.Status == "APPLIED") {
				t.Fatalf("M1 must not query on a full window: A=%s B=%s", ra.Status, rb.Status)
			}
		})
	}
}

// F50 (release 083, T2): RELEASE_ON_READ_ANSWERED releases a command only on an ANSWERED NOT_FOUND to a read
// made release-after after its creation and at least the settle time after its last dispatch attempt; an
// unanswered read (owner offline) and a read too soon after a send release nothing. With shadow reads
// (release 083 device option) an offline owner answers from its records, as a device shadow does, and the
// baseline then releases a command a held channel may still deliver (unsafe by design on a queue channel).
func TestF50ReleaseOnReadAnswered(t *testing.T) {
	k2 := func(c *device.Config) { c.Query = true; c.DedupHorizon = -1 }
	setup := func(t *testing.T, mut func(*device.Config), settle time.Duration) (*deviceRig, *Authority, *journal.Store, *outbox.Dispatcher) {
		r := newDeviceRig(t, mut)
		a, st, disp := rig083(t, r, probe.ReleaseOnReadAnswered, 12, 4, func(c *AuthorityConfig) {
			c.ReleaseAfter, c.ReleaseSettle = 30*time.Millisecond, settle
		})
		r.drop["A"] = true
		if code, out := invokeCandidate(t, a, dose(t, "A", 4, 1)); code != 202 || out.Decision != "ADMITTED" {
			t.Fatalf("A: %d %+v", code, out)
		}
		_ = disp.DispatchOnce(context.Background()) // the send is lost (never reaches the owner)
		return r, a, st, disp
	}
	t.Run("answered NOT_FOUND releases", func(t *testing.T) {
		_, a, st, _ := setup(t, k2, 10*time.Millisecond)
		time.Sleep(40 * time.Millisecond)
		if n, err := a.ReleaseSweepOnce(); err != nil || n != 1 {
			t.Fatalf("sweep: n=%d err=%v (want A released)", n, err)
		}
		if ra, _ := st.GetCommand("dose-1", "A"); ra.Status != "STOPPED" {
			t.Fatalf("A must be committed STOPPED, got %s", ra.Status)
		}
	})
	t.Run("unanswered read releases nothing", func(t *testing.T) {
		r, a, st, _ := setup(t, k2, 10*time.Millisecond)
		r.dev.SetOnline(false)
		time.Sleep(40 * time.Millisecond)
		if n, err := a.ReleaseSweepOnce(); err != nil || n != 0 {
			t.Fatalf("sweep with the owner offline: n=%d err=%v (want nothing released)", n, err)
		}
		if ra, _ := st.GetCommand("dose-1", "A"); ra.Status == "STOPPED" {
			t.Fatal("A must stay unresolved after an unanswered read")
		}
		r.dev.SetOnline(true)
		if n, err := a.ReleaseSweepOnce(); err != nil || n != 1 {
			t.Fatalf("sweep with the owner back: n=%d err=%v (want A released)", n, err)
		}
	})
	t.Run("not settled: a recent send", func(t *testing.T) {
		_, a, st, disp := setup(t, k2, 30*time.Millisecond)
		time.Sleep(32 * time.Millisecond)
		_ = disp.DispatchOnce(context.Background()) // first item attempt count 1: the cut (release-after - settle = 0) refuses it
		if err := st.RecordDispatchFailure("dose-1", "A", "test: a send just failed"); err != nil {
			t.Fatal(err)
		}
		if n, err := a.ReleaseSweepOnce(); err != nil || n != 0 {
			t.Fatalf("sweep right after a send: n=%d err=%v (want nothing released)", n, err)
		}
		time.Sleep(35 * time.Millisecond)
		if n, err := a.ReleaseSweepOnce(); err != nil || n != 1 {
			t.Fatalf("sweep after the settle time: n=%d err=%v (want A released)", n, err)
		}
	})
	t.Run("shadow reads answer while offline", func(t *testing.T) {
		r, a, st, _ := setup(t, func(c *device.Config) { k2(c); c.ShadowReads = true }, 10*time.Millisecond)
		r.dev.SetOnline(false)
		time.Sleep(40 * time.Millisecond)
		if n, err := a.ReleaseSweepOnce(); err != nil || n != 1 {
			t.Fatalf("sweep with a shadow read: n=%d err=%v (want A released)", n, err)
		}
		if ra, _ := st.GetCommand("dose-1", "A"); ra.Status != "STOPPED" {
			t.Fatalf("A must be committed STOPPED, got %s", ra.Status)
		}
	})
	t.Run("retry cut", func(t *testing.T) {
		_, _, st, _ := setup(t, k2, 10*time.Millisecond)
		allow := RetryCutFunc(st, nil, 20*time.Millisecond)
		if !allow("dose-1", "A", 1) {
			t.Fatal("a resend before the cut must be allowed")
		}
		time.Sleep(25 * time.Millisecond)
		if allow("dose-1", "A", 1) {
			t.Fatal("a resend after the cut must be refused")
		}
		if !allow("dose-1", "A", 0) {
			t.Fatal("the first send is never refused")
		}
		if RetryCutFunc(st, func(string, string, int64) bool { return false }, time.Hour)("dose-1", "A", 1) {
			t.Fatal("the cut must keep the base guard's refusal")
		}
	})
	t.Run("settle above release-after is refused", func(t *testing.T) {
		r := newDeviceRig(t, k2)
		st, _ := journal.Open(filepath.Join(t.TempDir(), "a.db"))
		defer st.Close()
		d := journal.Domain{ID: "dose-1", Generation: 1, InstalledEpoch: 1, Dimension: 1, ComponentMin: []int64{0}, ComponentMax: []int64{12}, Anchor: []int64{0}}
		if _, err := NewAuthority(AuthorityConfig{Store: st, Domain: d, EndpointURL: r.srv.URL, Profile: "DEVICE", Policy: probe.ReleaseOnReadAnswered,
			EvidenceContract: r.dev.Contract(), ReleaseAfter: time.Second, ReleaseSettle: 2 * time.Second}); err == nil {
			t.Fatal("want an error")
		}
	})
}

// F51 (release 083, T5): with the greedy certificate EVICON resolves a block whose deficit needs more members
// than the enumeration searches (gamma = 3). Six lost doses of 2 hold the bound of 12; a dose of 7 needs four of
// them stopped. The enumeration finds no certificate of size <= 3 and EVICON waits (Theorem 3's fallback); the
// greedy certificate names four, EVICON stops them (Q_S), and the candidate is admitted. The default is the
// enumeration, unchanged.
func TestF51GreedyCertificateResolvesLargeDeficit(t *testing.T) {
	for _, tc := range []struct {
		cert     string
		admitted bool
	}{{"", false}, {CertificateEnumerate, false}, {CertificateGreedy, true}} {
		t.Run("cert="+tc.cert, func(t *testing.T) {
			r := newDeviceRig(t, func(c *device.Config) { c.Stop = true }) // K0S
			a, st, disp := rig083(t, r, probe.EVICON, 12, 10, func(c *AuthorityConfig) {
				c.Certificate = tc.cert
				c.ResolvePolicy = resolve.Policy{Mode: resolve.ModeImmediate}
				c.ResolveCosts = resolve.Costs{HoldPerUnitPerSec: 1, StopCost: 0.001, FenceCost: 0.001}
				c.CertificateDepth = 3
			})
			if tc.cert == "" && a.cfg.Certificate != CertificateEnumerate {
				t.Fatalf("default certificate search %q, want enumerate", a.cfg.Certificate)
			}
			for i := 0; i < 6; i++ {
				id := string(rune('A' + i))
				r.drop[id] = true
				if code, out := invokeCandidate(t, a, dose(t, id, 2, uint64(i+1))); code != 202 || out.Decision != "ADMITTED" {
					t.Fatalf("%s: %d %+v", id, code, out)
				}
			}
			_ = disp.DispatchOnce(context.Background())
			time.Sleep(5 * time.Millisecond)
			code, out := invokeCandidate(t, a, dose(t, "X", 7, 7))
			if got := code == 202 && out.Decision == "ADMITTED"; got != tc.admitted {
				t.Fatalf("X admitted=%v, want %v (%d %+v)", got, tc.admitted, code, out)
			}
			stopped := 0
			for i := 0; i < 6; i++ {
				if c, _ := st.GetCommand("dose-1", string(rune('A'+i))); c.Status == "STOPPED" {
					stopped++
				}
			}
			if tc.admitted && stopped < 4 {
				t.Fatalf("greedy: %d stopped, want at least 4", stopped)
			}
			if !tc.admitted && stopped != 0 {
				t.Fatalf("enumeration: %d stopped, want 0 (no certificate of size <= 3)", stopped)
			}
		})
	}
}
