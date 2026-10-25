package runtime

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"sync"
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

// deviceRig: an evicon-device behind HTTP; effect requests whose command ID is
// in drop never reach the device (lost on the way).
type deviceRig struct {
	dev   *device.Device
	plant *plant.Plant
	srv   *httptest.Server
	mu    sync.Mutex
	drop  map[string]bool
	// beforeEffect (release 072, optional) runs when a dispatch reaches the device,
	// before the device handles it: a test can commit evidence while the dispatch is
	// in flight.
	beforeEffect func(protocol.CommandRequest)
}

func newDeviceRig(t *testing.T, mut func(*device.Config)) *deviceRig {
	t.Helper()
	dir := t.TempDir()
	p, err := plant.Open(filepath.Join(dir, "plant.jsonl"), false)
	if err != nil {
		t.Fatal(err)
	}
	cfg := device.Config{DeviceID: "dose-1", DomainID: "dose-1", StateDir: filepath.Join(dir, "dev"), Plant: device.LocalPlant{P: p}}
	mut(&cfg)
	d, err := device.Open(cfg)
	if err != nil {
		t.Fatal(err)
	}
	r := &deviceRig{dev: d, plant: p, drop: map[string]bool{}}
	inner := d.Handler("test")
	r.srv = httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, req *http.Request) {
		if req.URL.Path == "/v1/effects" {
			body, _ := io.ReadAll(req.Body)
			var cr protocol.CommandRequest
			_ = json.Unmarshal(body, &cr)
			r.mu.Lock()
			lost, hook := r.drop[cr.CommandID], r.beforeEffect
			r.mu.Unlock()
			if hook != nil {
				hook(cr)
			}
			if lost {
				http.Error(w, "lost", 503)
				return
			}
			req.Body = io.NopCloser(bytes.NewReader(body))
		}
		inner.ServeHTTP(w, req)
	}))
	t.Cleanup(func() { r.srv.Close(); d.Close(); p.Close() })
	return r
}

func deviceAuthority(t *testing.T, r *deviceRig) (*Authority, *journal.Store, *outbox.Dispatcher) {
	t.Helper()
	d := journal.Domain{ID: "dose-1", Generation: 1, InstalledEpoch: 1, Dimension: 1, ComponentMin: []int64{0}, ComponentMax: []int64{12}, Anchor: []int64{0}, ContractHash: "device-test"}
	st, err := journal.Open(filepath.Join(t.TempDir(), "authority.db"))
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { st.Close() })
	disp := outbox.New(st, time.Hour, nil)
	a, err := NewAuthority(AuthorityConfig{Store: st, Domain: d, EndpointURL: r.srv.URL, Profile: "DEVICE", Policy: probe.EVICON, Dispatcher: disp,
		ProbeBudget: 4, MaxOutstanding: 4, MaxConfigurations: 256, PlannerComputeBudget: 50 * time.Millisecond,
		EvidenceContract: r.dev.Contract(), ResolvePolicy: resolve.Policy{Mode: resolve.ModeImmediate}, AmbiguityAfter: time.Millisecond,
		ResolveCosts: resolve.Costs{HoldPerUnitPerSec: 1, StopCost: 0.001, FenceCost: 0.001}})
	if err != nil {
		t.Fatal(err)
	}
	return a, st, disp
}

// F29: EVICON drives a K3 device (stop or fence): the lost command is resolved
// by owner evidence, the candidate is admitted, the late original never
// applies, and the plant (independent truth) never exceeds the bound.
func TestF29EviconOnDeviceFleetContracts(t *testing.T) {
	for _, tc := range []struct {
		name string
		mut  func(*device.Config)
	}{
		{"K3S", func(c *device.Config) { c.DedupHorizon = -1; c.Query = true; c.Stop = true }},
		{"K0S", func(c *device.Config) { c.Stop = true }}, // release 072: stop without dedup or query
		{"K3F", func(c *device.Config) { c.DedupHorizon = -1; c.Query = true; c.Fence = true }},
		{"CUTOFF", func(c *device.Config) { c.Query = true; c.Fence = true }},
	} {
		t.Run(tc.name, func(t *testing.T) {
			r := newDeviceRig(t, tc.mut)
			a, st, disp := deviceAuthority(t, r)
			A := dose(t, "A", 8, 1)
			r.drop["A"] = true
			if code, out := invokeCandidate(t, a, A); code != 202 {
				t.Fatalf("A: %d %+v", code, out)
			}
			_ = disp.DispatchOnce(context.Background())
			code, out := invokeCandidate(t, a, dose(t, "C", 8, 2))
			if code != 202 || out.Decision != "ADMITTED" {
				t.Fatalf("C must be admitted after backward evidence: %d %+v", code, out)
			}
			// stop: tombstone receipt; fence: sealed as unclaimed below the
			// owner-certified cutoff (061, EVICON) so the client can reissue
			if ra, _ := st.GetCommand("dose-1", "A"); ra.Status != "STOPPED" {
				t.Fatalf("A must be STOPPED, got %s", ra.Status)
			}
			if tc.name != "K3S" && tc.name != "K0S" {
				if dom, _ := st.GetDomain("dose-1"); dom.CutoffSequence < 1 {
					t.Fatalf("authority anchor must cover A, cutoff=%d", dom.CutoffSequence)
				}
			}
			// late original reaches the device
			late, err := r.dev.Apply(A)
			if err != nil || late.Outcome != "STOPPED" {
				t.Fatalf("late A: %+v %v", late, err)
			}
			_ = disp.DispatchOnce(context.Background())
			s := r.plant.Summary()
			if s.Total != 1 || s.Devices[0].Level != 8 {
				t.Fatalf("plant truth: %+v", s)
			}
		})
	}
}

// F30: on a K1 device (finite dedup, no query/stop/fence) EVICON has no
// backward evidence and must defer (escalate) instead of guessing.
func TestF30EviconDefersOnK1Device(t *testing.T) {
	r := newDeviceRig(t, func(c *device.Config) { c.DedupHorizon = 45 * time.Second })
	a, _, disp := deviceAuthority(t, r)
	r.drop["A"] = true
	invokeCandidate(t, a, dose(t, "A", 8, 1))
	_ = disp.DispatchOnce(context.Background())
	code, out := invokeCandidate(t, a, dose(t, "C", 8, 2))
	if code == 202 || !strings.Contains(out.Reason, "EVICON") {
		t.Fatalf("C must be deferred by EVICON: %d %+v", code, out)
	}
	if lv := r.dev.Contract().Level(); lv != contract.K1 {
		t.Fatalf("level %s", lv)
	}
}

func TestDeviceProfileRequiresEvicon(t *testing.T) {
	st, err := journal.Open(filepath.Join(t.TempDir(), "a.db"))
	if err != nil {
		t.Fatal(err)
	}
	defer st.Close()
	d := journal.Domain{ID: "x", Generation: 1, InstalledEpoch: 1, Dimension: 1, ComponentMax: []int64{1}, Anchor: []int64{0}, ContractHash: "t"}
	if _, err := NewAuthority(AuthorityConfig{Store: st, Domain: d, Profile: "DEVICE", Policy: probe.M1}); err == nil {
		t.Fatal("DEVICE without an evidence contract must fail")
	}
	doc := contract.EvidenceContract{SchemaVersion: 1, ID: "doc", Source: contract.SourceDocumented, Capabilities: []contract.EvidenceCapability{contract.CapStop}}
	if _, err := NewAuthority(AuthorityConfig{Store: st, Domain: d, Profile: "DEVICE", Policy: probe.EVICON, EvidenceContract: doc}); err == nil {
		t.Fatal("documented contracts must not drive EVICON")
	}
	meas := contract.EvidenceContract{SchemaVersion: 1, ID: "m", Source: contract.SourceMeasured, Capabilities: []contract.EvidenceCapability{contract.CapStop}}
	if _, err := NewAuthority(AuthorityConfig{Store: st, Domain: d, Profile: "DEVICE", Policy: probe.EVICON, EvidenceContract: meas}); err == nil {
		t.Fatal("a measured contract without per-capability provenance must be rejected (review RV03)")
	}
	meas.Provenance = map[string]string{"Q_S": "P9,P1,P9N:intervening=64"}
	if _, err := NewAuthority(AuthorityConfig{Store: st, Domain: d, Profile: "DEVICE", Policy: probe.EVICON, EvidenceContract: meas}); err != nil {
		t.Fatalf("measured contracts with provenance are evidence-backed: %v", err)
	}
}

// F36 (release 062, review RV06 in the runtime): a claim left by a crash
// between effect and receipt is an orphan the non-atomic device cannot
// certify. A stop answers UNCERTAIN; a full-frontier fence is ABORTED (never
// anchored, nothing sealed) instead of leaving the domain paused; the
// identity stays ambiguous, EviCon stops targeting it, and the bound holds.
func TestF36CrashOrphanIsNeverResolvedOrSealed(t *testing.T) {
	A := dose(t, "A", 8, 1)
	orphan := func(c *device.Config) {
		rec := map[string]any{"k": "REC", "r": map[string]any{"id": "A", "hash": A.IntentHash, "seq": 1, "value": 8, "status": "CLAIMED", "at_ms": time.Now().UnixMilli()}}
		raw, _ := json.Marshal(rec)
		if err := os.MkdirAll(c.StateDir, 0o755); err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(filepath.Join(c.StateDir, c.DeviceID+".journal"), append(raw, '\n'), 0o644); err != nil {
			t.Fatal(err)
		}
	}
	for _, tc := range []struct {
		name string
		mut  func(*device.Config)
	}{
		{"K3S", func(c *device.Config) { c.DedupHorizon = -1; c.Query = true; c.Stop = true; orphan(c) }},
		{"K3F", func(c *device.Config) { c.DedupHorizon = -1; c.Query = true; c.Fence = true; orphan(c) }},
	} {
		t.Run(tc.name, func(t *testing.T) {
			r := newDeviceRig(t, tc.mut)
			if res := r.plant.Apply(plant.ApplyRequest{Device: "dose-1", Cmd: "A", Value: 8}); !res.OK { // the effect did happen
				t.Fatalf("plant: %+v", res)
			}
			a, st, disp := deviceAuthority(t, r)
			r.drop["A"] = true
			invokeCandidate(t, a, A)
			_ = disp.DispatchOnce(context.Background())
			for i := 0; i < 3; i++ {
				if _, out := invokeCandidate(t, a, dose(t, fmt.Sprintf("C%d", i), 8, uint64(2+i))); out.Decision == "ADMITTED" {
					t.Fatalf("no candidate may be admitted while A is an orphan: %+v", out)
				}
				if err := a.SweepOnce(); err != nil {
					t.Fatal(err)
				}
			}
			if ra, _ := st.GetCommand("dose-1", "A"); ra.Status == "STOPPED" || ra.Status == "APPLIED" {
				t.Fatalf("an orphan must stay ambiguous, got %s", ra.Status)
			}
			if _, pending, _ := st.PendingBarrier("dose-1"); pending {
				t.Fatal("a fence that cannot be certified must not leave the domain paused")
			}
			if !a.isOwnerUncertain("A") {
				t.Fatal("the orphan must be marked owner-uncertain")
			}
			if s := r.plant.Summary(); s.Total != 1 || s.Devices[0].MaxLevel > 12 {
				t.Fatalf("plant truth: %+v", s)
			}
		})
	}
}
