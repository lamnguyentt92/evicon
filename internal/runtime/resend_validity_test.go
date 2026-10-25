package runtime

import (
	"path/filepath"
	"testing"
	"time"

	"evicon/internal/device"
	"evicon/internal/journal"
	"evicon/internal/outbox"
	"evicon/internal/probe"
)

// F47 (release 077): with the intent's validity, the T4 guard allows same-identity resends of an
// unresolved command under an unbounded horizon only until created + validity (Eq. 4 with the
// validity cut; the simulator's rule). Without validity (releases 072-076) resends stay allowed.
func TestF47ResendStopsAtValidity(t *testing.T) {
	r := newDeviceRig(t, func(c *device.Config) { c.Query = true; c.DedupHorizon = -1 }) // K2: D_h unbounded
	d := journal.Domain{ID: "dose-1", Generation: 1, InstalledEpoch: 1, Dimension: 1, ComponentMin: []int64{0}, ComponentMax: []int64{12}, Anchor: []int64{0}, ContractHash: "device-test"}
	st, err := journal.Open(filepath.Join(t.TempDir(), "authority.db"))
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { st.Close() })
	disp := outbox.New(st, time.Hour, nil)
	a, err := NewAuthority(AuthorityConfig{Store: st, Domain: d, EndpointURL: r.srv.URL, Profile: "DEVICE", Policy: probe.EVICON,
		Dispatcher: disp, ProbeBudget: 4, MaxOutstanding: 4, MaxConfigurations: 256, PlannerComputeBudget: 50 * time.Millisecond,
		EvidenceContract: r.dev.Contract()})
	if err != nil {
		t.Fatal(err)
	}
	if code, out := invokeCandidate(t, a, dose(t, "A", 4, 1)); code != 202 || out.Decision != "ADMITTED" {
		t.Fatalf("A: %d %+v", code, out)
	}
	c := r.dev.Contract()
	withCut := RetryAllowedUntilFunc(st, c, -1, 80*time.Millisecond)
	noCut := RetryAllowedUntilFunc(st, c, -1, 0)
	if !withCut("dose-1", "A", 0) || !withCut("dose-1", "A", 1) {
		t.Fatal("first send and a resend inside the validity must be allowed")
	}
	time.Sleep(120 * time.Millisecond)
	if withCut("dose-1", "A", 1) {
		t.Fatal("a resend after the validity must be suppressed")
	}
	if !noCut("dose-1", "A", 5) {
		t.Fatal("without a validity cut an unbounded horizon allows resends while the command is unresolved")
	}
	if !withCut("dose-1", "A", 0) {
		t.Fatal("the first send is never suppressed")
	}
}
