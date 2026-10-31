package runtime

// Release 084 (rule R5', PREREG_extra_084.md (a)): property tests of per-identity fence intake on random
// executions with crash orphans, fences and losses, against the real authority, the real evicon-device and
// the plant as the independent truth. In every execution: no false STOPPED (STOPPED for an identity that
// took effect), no false APPLIED, no duplicate effect, and the plant never exceeds the bound. The negative
// control breaks premise FC (the owner's fence answer omits the identities it cannot certify, the
// "sealorphans" defect of the TLA+ model): the checker must then find a false STOPPED.

import (
	"bytes"
	"context"
	"encoding/json"
	"io"
	"math/rand"
	"net/http"
	"net/http/httptest"
	"path/filepath"
	"sync"
	"testing"
	"time"

	"evicon/internal/device"
	"evicon/internal/journal"
	"evicon/internal/outbox"
	"evicon/internal/plant"
	"evicon/internal/probe"
	"evicon/internal/protocol"
	"evicon/internal/resolve"
)

type fiOutcome struct {
	falseStopped, falseApplied, duplicates, boundViolations int
	orphans, partial, aborted, sealed, admitted             int
}

type fiRig struct {
	mu         sync.Mutex
	rng        *rand.Rand
	dev        *device.Device
	plant      *plant.Plant
	srv        *httptest.Server
	held       []protocol.CommandRequest // requests lost now, delivered late
	crashArmed string
	incomplete bool // negative control: fence answers omit UNCERTAIN identities (no FC)
	orphans    int
	partial    int
	aborted    int
}

func newFIRig(t *testing.T, rng *rand.Rand, mut func(*device.Config), incomplete bool) *fiRig {
	t.Helper()
	dir := t.TempDir()
	p, err := plant.Open(filepath.Join(dir, "plant.jsonl"), false)
	if err != nil {
		t.Fatal(err)
	}
	r := &fiRig{rng: rng, plant: p, incomplete: incomplete}
	cfg := device.Config{DeviceID: "dose-1", DomainID: "dose-1", StateDir: filepath.Join(dir, "dev"), Plant: device.LocalPlant{P: p},
		CrashHook: func(point, id string) {
			// a crash between the effect and its receipt: the claim stays without a receipt (an orphan)
			if point == device.CrashAfterEffectBeforeReceipt && r.crashArmed == id {
				r.crashArmed = ""
				r.orphans++
				panic(http.ErrAbortHandler)
			}
		}}
	mut(&cfg)
	d, err := device.Open(cfg)
	if err != nil {
		t.Fatal(err)
	}
	r.dev = d
	inner := d.Handler("test")
	r.srv = httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, req *http.Request) {
		switch req.URL.Path {
		case "/v1/effects":
			body, _ := io.ReadAll(req.Body)
			var cr protocol.CommandRequest
			_ = json.Unmarshal(body, &cr)
			r.mu.Lock()
			fate := r.rng.Intn(100)
			r.mu.Unlock()
			req.Body = io.NopCloser(bytes.NewReader(body))
			switch {
			case fate < 15: // the request is lost
				http.Error(w, "lost", 503)
				return
			case fate < 25: // the request is held and delivered late
				r.mu.Lock()
				r.held = append(r.held, cr)
				r.mu.Unlock()
				http.Error(w, "held", 503)
				return
			case fate < 40: // the owner applies it; the reply is lost
				inner.ServeHTTP(httptest.NewRecorder(), req)
				http.Error(w, "reply lost", 503)
				return
			case fate < 52: // the owner crashes between the effect and the receipt
				r.crashArmed = cr.CommandID
				defer func() { r.crashArmed = "" }()
			}
			inner.ServeHTTP(w, req)
		case "/v1/barriers":
			rec := httptest.NewRecorder()
			inner.ServeHTTP(rec, req)
			var br protocol.BarrierReceipt
			if rec.Code/100 == 2 && json.Unmarshal(rec.Body.Bytes(), &br) == nil {
				uncertain := 0
				kept := br.Receipts[:0]
				for _, x := range br.Receipts {
					if x.Outcome != "APPLIED" && x.Outcome != "STOPPED" {
						uncertain++
						if r.incomplete {
							continue // negative control: the answer omits what the owner cannot certify
						}
					}
					kept = append(kept, x)
				}
				br.Receipts = kept
				w.Header().Set("content-type", "application/json")
				w.WriteHeader(rec.Code)
				_ = json.NewEncoder(w).Encode(br)
				return
			}
			for k, v := range rec.Header() {
				w.Header()[k] = v
			}
			w.WriteHeader(rec.Code)
			_, _ = w.Write(rec.Body.Bytes())
		default:
			inner.ServeHTTP(w, req)
		}
	}))
	t.Cleanup(func() { r.srv.Close(); d.Close(); p.Close() })
	return r
}

func (r *fiRig) deliverHeld(n int) {
	r.mu.Lock()
	if n > len(r.held) {
		n = len(r.held)
	}
	batch := append([]protocol.CommandRequest(nil), r.held[:n]...)
	r.held = r.held[n:]
	r.mu.Unlock()
	for _, cr := range batch {
		_, _ = r.dev.Apply(cr) // a late copy reaches the owner
	}
}

// runFIExecution plays one random execution and checks it against the plant.
func runFIExecution(t *testing.T, seed int64, intake string, incomplete bool) fiOutcome {
	t.Helper()
	rng := rand.New(rand.NewSource(seed))
	query := rng.Intn(3) == 0
	dedup := query && rng.Intn(2) == 0
	r := newFIRig(t, rng, func(c *device.Config) {
		c.Fence = true
		c.Query = query
		if dedup {
			c.DedupHorizon = -1
		}
	}, incomplete)
	bound := int64(8 + rng.Intn(10))
	r.plant.SetLimit("dose-1", bound)
	d := journal.Domain{ID: "dose-1", Generation: 1, InstalledEpoch: 1, Dimension: 1, ComponentMin: []int64{0}, ComponentMax: []int64{bound}, Anchor: []int64{0}, ContractHash: "fi-property"}
	st, err := journal.Open(filepath.Join(t.TempDir(), "authority.db"))
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { st.Close() })
	disp := outbox.New(st, time.Hour, nil)
	ec := r.dev.Contract()
	disp.RetryAllowed = RetryAllowedFunc(st, ec, -1) // the T4 retry guard of every testbed run
	mode := resolve.ModeImmediate
	if rng.Intn(2) == 0 {
		mode = resolve.ModeSkiRental
	}
	a, err := NewAuthority(AuthorityConfig{Store: st, Domain: d, EndpointURL: r.srv.URL, Profile: "DEVICE", Policy: probe.EVICON, Dispatcher: disp,
		ProbeBudget: 4, MaxOutstanding: 3 + rng.Intn(5), MaxConfigurations: 256, PlannerComputeBudget: 50 * time.Millisecond,
		EvidenceContract: ec, AmbiguityAfter: time.Millisecond, FenceIntake: intake,
		ResolvePolicy: resolve.Policy{Mode: mode, QueryFirst: true}, ResolveCosts: resolve.Costs{HoldPerUnitPerSec: 1, QueryCost: 0.01, StopCost: 0.01, FenceCost: 0.01}})
	if err != nil {
		t.Fatal(err)
	}
	var seq uint64
	ids := []string{}
	steps := 14 + rng.Intn(14)
	for i := 0; i < steps; i++ {
		switch k := rng.Intn(10); {
		case k < 6:
			seq++
			id := "c" + string(rune('a'+i%26)) + itoaSeq(seq)
			req := dose(t, id, int64(1+rng.Intn(4)), seq)
			if _, out := invokeCandidate(t, a, req); out.Decision == "ADMITTED" {
				ids = append(ids, id)
			}
			_ = disp.DispatchOnce(context.Background())
		case k < 8:
			if err := a.SweepOnce(); err != nil {
				t.Fatal(err)
			}
		default:
			r.deliverHeld(1 + rng.Intn(2))
		}
		time.Sleep(2 * time.Millisecond)
	}
	r.deliverHeld(1 << 20)
	for i := 0; i < 3; i++ {
		_ = disp.DispatchOnce(context.Background())
		_ = a.SweepOnce()
		time.Sleep(2 * time.Millisecond)
	}
	var o fiOutcome
	o.orphans, o.admitted = r.orphans, len(ids)
	for _, id := range ids {
		c, err := st.GetCommand("dose-1", id)
		if err != nil {
			t.Fatal(err)
		}
		eff := r.plant.Truth(id, "dose-1").Effects
		if c.Status == "STOPPED" && eff > 0 {
			o.falseStopped++
		}
		if c.Status == "APPLIED" && eff == 0 {
			o.falseApplied++
		}
		if eff > 1 {
			o.duplicates++
		}
	}
	for _, ds := range r.plant.Summary().Devices {
		if ds.Violations {
			o.boundViolations++
		}
	}
	o.partial, o.aborted = countLifecycles(t, st)
	return o
}

func itoaSeq(v uint64) string {
	b := []byte{}
	for {
		b = append([]byte{byte('0' + v%10)}, b...)
		v /= 10
		if v == 0 {
			return string(b)
		}
	}
}

func countLifecycles(t *testing.T, st *journal.Store) (partial, aborted int) {
	t.Helper()
	p, a, err := st.BarrierStatusCounts("dose-1")
	if err != nil {
		t.Fatal(err)
	}
	return p, a
}

// TestFenceIntakePropertyRandomExecutions: R5' (and R5) are safe on every random execution; without FC the
// checker finds the false STOPPED that sealing an omitted orphan creates.
func TestFenceIntakePropertyRandomExecutions(t *testing.T) {
	n := 60
	if testing.Short() {
		n = 15
	}
	for _, tc := range []struct {
		name       string
		intake     string
		incomplete bool
	}{
		{"per-identity (R5')", FenceIntakePerIdentity, false},
		{"all-or-nothing (R5)", FenceIntakeAllOrNothing, false},
		{"negative control: per-identity without FC", FenceIntakePerIdentity, true},
	} {
		t.Run(tc.name, func(t *testing.T) {
			var tot fiOutcome
			execsWithOrphanFenced := 0
			for i := 0; i < n; i++ {
				o := runFIExecution(t, int64(1000+i), tc.intake, tc.incomplete)
				if !tc.incomplete && o.falseStopped+o.falseApplied+o.duplicates+o.boundViolations > 0 {
					t.Fatalf("seed %d: unsafe execution under %s: %+v", 1000+i, tc.intake, o)
				}
				tot.falseStopped += o.falseStopped
				tot.falseApplied += o.falseApplied
				tot.duplicates += o.duplicates
				tot.boundViolations += o.boundViolations
				tot.orphans += o.orphans
				tot.partial += o.partial
				tot.aborted += o.aborted
				tot.admitted += o.admitted
				if o.orphans > 0 && (o.partial > 0 || o.aborted > 0) {
					execsWithOrphanFenced++
				}
			}
			t.Logf("%s: %d executions, %d admitted, %d crash orphans, %d per-identity fences, %d aborted fences, violations: false STOPPED %d, false APPLIED %d, duplicates %d, bound %d",
				tc.name, n, tot.admitted, tot.orphans, tot.partial, tot.aborted, tot.falseStopped, tot.falseApplied, tot.duplicates, tot.boundViolations)
			if tot.orphans == 0 || (!tc.incomplete && execsWithOrphanFenced == 0) {
				t.Fatalf("the random executions must contain crash orphans under fences (orphans %d, executions with a fence over an orphan %d)", tot.orphans, execsWithOrphanFenced)
			}
			switch {
			case tc.incomplete && tot.falseStopped == 0:
				t.Fatal("negative control: without FC the checker must find a false STOPPED")
			case tc.intake == FenceIntakePerIdentity && !tc.incomplete && tot.partial == 0:
				t.Fatal("R5' must have completed fences with per-identity intake")
			case tc.intake == FenceIntakeAllOrNothing && tot.partial != 0:
				t.Fatal("R5 never completes a fence with per-identity intake")
			}
		})
	}
}
