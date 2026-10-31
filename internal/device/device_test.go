package device

import (
	"errors"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"testing"
	"time"

	"evicon/internal/contract"
	"evicon/internal/domain"
	"evicon/internal/plant"
	"evicon/internal/protocol"
)

type clock struct {
	mu sync.Mutex
	t  time.Time
}

func (c *clock) now() time.Time { c.mu.Lock(); defer c.mu.Unlock(); return c.t }
func (c *clock) add(d time.Duration) {
	c.mu.Lock()
	c.t = c.t.Add(d)
	c.mu.Unlock()
}

type rig struct {
	t     *testing.T
	dir   string
	plant *plant.Plant
	clk   *clock
	cfg   Config
	dev   *Device
}

type crashSignal struct{ point string }

func newRig(t *testing.T, mut func(*Config)) *rig {
	t.Helper()
	dir := t.TempDir()
	p, err := plant.Open(filepath.Join(dir, "plant.jsonl"), false)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { p.Close() })
	clk := &clock{t: time.Unix(1_800_000_000, 0)}
	cfg := Config{DeviceID: "pump-1", DomainID: "d", StateDir: filepath.Join(dir, "state"), Plant: LocalPlant{p}, Now: clk.now}
	if mut != nil {
		mut(&cfg)
	}
	r := &rig{t: t, dir: dir, plant: p, clk: clk, cfg: cfg}
	r.open()
	return r
}

func (r *rig) open() {
	r.t.Helper()
	d, err := Open(r.cfg)
	if err != nil {
		r.t.Fatal(err)
	}
	r.dev = d
	r.t.Cleanup(func() { d.Close() })
}

func (r *rig) reopen() {
	r.dev.Close()
	r.cfg.CrashHook = nil
	r.open()
}

func req(id string, seq uint64, v int64) protocol.CommandRequest {
	return protocol.CommandRequest{SchemaVersion: 1, DomainID: "d", Generation: 1, CommandID: id, Operation: "ADD", Value: []int64{v}, Epoch: 1, Sequence: seq}
}

func stopFor(t *testing.T, id string, seq uint64, v int64) protocol.StopRequest {
	c := req(id, seq, v)
	if err := c.ValidateAndFillHash(1); err != nil {
		t.Fatal(err)
	}
	return protocol.StopRequest{SchemaVersion: 1, DomainID: "d", Generation: 1, CommandID: id, IntentHash: c.IntentHash, Sequence: seq}
}

func (r *rig) apply(id string, seq uint64, v int64) protocol.Receipt {
	r.t.Helper()
	rc, err := r.dev.Apply(req(id, seq, v))
	if err != nil {
		r.t.Fatalf("apply %s: %v", id, err)
	}
	return rc
}

func (r *rig) effects(id string) int { return r.plant.Truth(id, "pump-1").Effects }

// applyCrashing runs Apply with a crash armed at point and swallows the crash.
func (r *rig) applyCrashing(id string, seq uint64, v int64, point string) {
	r.t.Helper()
	r.dev.cfg.CrashHook = func(p, cmd string) {
		if p == point && cmd == id {
			panic(crashSignal{p})
		}
	}
	func() {
		defer func() {
			x := recover()
			if _, ok := x.(crashSignal); !ok {
				r.t.Fatalf("expected crash at %s, got %v", point, x)
			}
		}()
		_, _ = r.dev.Apply(req(id, seq, v))
	}()
}

// D01: K0 (no dedup): a same-identity resend applies again.
func TestD01NoDedupDuplicates(t *testing.T) {
	r := newRig(t, nil)
	r.apply("a", 1, 2)
	r.apply("a", 1, 2)
	if r.effects("a") != 2 {
		t.Fatalf("K0 must duplicate, got %d effects", r.effects("a"))
	}
	if c := r.dev.Contract(); c.Level() != contract.K0 {
		t.Fatalf("level %s", c.Level())
	}
}

// D02: finite dedup horizon: replay inside, duplicate after eviction.
func TestD02FiniteHorizon(t *testing.T) {
	r := newRig(t, func(c *Config) { c.DedupHorizon = 45 * time.Second })
	first := r.apply("a", 1, 2)
	r.clk.add(30 * time.Second)
	again := r.apply("a", 1, 2)
	if r.effects("a") != 1 || again.ReceiptVersion != first.ReceiptVersion {
		t.Fatalf("inside horizon must replay: effects=%d %+v", r.effects("a"), again)
	}
	r.clk.add(16 * time.Second)
	r.apply("a", 1, 2)
	if r.effects("a") != 2 {
		t.Fatalf("beyond horizon the device forgets: effects=%d", r.effects("a"))
	}
	c := r.dev.Contract()
	if c.Level() != contract.K1 || c.DedupHorizonMS != 45000 {
		t.Fatalf("%+v", c)
	}
}

// D03: query (Q_A) answers from receipts even after the dedup cache evicted them.
func TestD03Query(t *testing.T) {
	r := newRig(t, func(c *Config) { c.DedupHorizon = time.Second; c.Query = true })
	r.apply("a", 1, 1)
	r.clk.add(time.Hour)
	q, err := r.dev.Receipt("a")
	if err != nil || q.Outcome != "APPLIED" {
		t.Fatalf("%+v %v", q, err)
	}
	if err := protocol.ValidateReceipt(q, "d", 1, "a", q.IntentHash, 1); err != nil {
		t.Fatal(err)
	}
	if _, err := r.dev.Receipt("nope"); !errors.Is(err, os.ErrNotExist) {
		t.Fatalf("unknown id must be not-found, got %v", err)
	}
	r2 := newRig(t, nil)
	if _, err := r2.dev.Receipt("a"); !errors.Is(err, ErrUnsupported) {
		t.Fatal("query must be unsupported when disabled")
	}
}

// D04: per-identity stop: tombstone blocks the late arrival; stop after
// APPLIED returns APPLIED; stop wins against an in-flight pre-effect claim.
func TestD04Stop(t *testing.T) {
	r := newRig(t, func(c *Config) { c.DedupHorizon = time.Second; c.Stop = true })
	s, err := r.dev.Stop(stopFor(t, "late", 1, 3))
	if err != nil || s.Outcome != "STOPPED" {
		t.Fatalf("%+v %v", s, err)
	}
	r.clk.add(time.Hour) // tombstones are not dedup cache entries
	if rc := r.apply("late", 1, 3); rc.Outcome != "STOPPED" || r.effects("late") != 0 {
		t.Fatalf("late arrival must stay STOPPED: %+v effects=%d", rc, r.effects("late"))
	}
	r.apply("done", 2, 1)
	if s, _ := r.dev.Stop(stopFor(t, "done", 2, 1)); s.Outcome != "APPLIED" {
		t.Fatalf("stop after effect must return APPLIED: %+v", s)
	}
	bad := stopFor(t, "done", 2, 1)
	bad.IntentHash = "x"
	if _, err := r.dev.Stop(bad); !errors.Is(err, ErrIdentityConflict) {
		t.Fatal("identity conflict expected")
	}
	// in-flight claim, plant write not started
	rd := newRig(t, func(c *Config) { c.Stop = true; c.EffectDelay = 200 * time.Millisecond; c.Now = time.Now })
	done := make(chan protocol.Receipt)
	go func() { rc, _ := rd.dev.Apply(req("slow", 1, 1)); done <- rc }()
	time.Sleep(50 * time.Millisecond)
	s2, err := rd.dev.Stop(stopFor(t, "slow", 1, 1))
	if err != nil || s2.Outcome != "STOPPED" {
		t.Fatalf("%+v %v", s2, err)
	}
	if rc := <-done; rc.Outcome != "STOPPED" || rd.effects("slow") != 0 {
		t.Fatalf("pre-effect stop must win: %+v effects=%d", rc, rd.effects("slow"))
	}
	if c := r.dev.Contract(); c.Level() != contract.K3 || !c.Has(contract.CapStop) {
		t.Fatalf("%+v", c)
	}
}

// D05: owner fence: durable cutoff seals lower sequences, survives restart,
// token replay is idempotent and a token with another cutoff conflicts.
func TestD05Fence(t *testing.T) {
	r := newRig(t, func(c *Config) { c.Fence = true; c.Query = true; c.DedupHorizon = -1 })
	r.apply("a", 1, 1)
	br, err := r.dev.Barrier(protocol.BarrierRequest{SchemaVersion: 1, DomainID: "d", Generation: 1, Token: "t1", CutoffSequence: 3})
	if err != nil || len(br.Receipts) != 1 || br.Receipts[0].Outcome != "APPLIED" {
		t.Fatalf("%+v %v", br, err)
	}
	r.reopen()
	if rc := r.apply("b", 2, 1); rc.Outcome != "STOPPED" || rc.Reason != "SEALED_BY_FENCE" || r.effects("b") != 0 {
		t.Fatalf("sealed sequence must be STOPPED after restart: %+v", rc)
	}
	if rc := r.apply("c", 4, 1); rc.Outcome != "APPLIED" {
		t.Fatalf("above cutoff applies: %+v", rc)
	}
	again, err := r.dev.Barrier(protocol.BarrierRequest{SchemaVersion: 1, DomainID: "d", Generation: 1, Token: "t1", CutoffSequence: 3})
	if err != nil || again.CutoffSequence != 3 {
		t.Fatalf("token replay: %+v %v", again, err)
	}
	if _, err := r.dev.Barrier(protocol.BarrierRequest{SchemaVersion: 1, DomainID: "d", Generation: 1, Token: "t1", CutoffSequence: 9}); !errors.Is(err, ErrIdentityConflict) {
		t.Fatal("token conflict expected")
	}
}

// D06: owner-enforced expiry (T).
func TestD06Expiry(t *testing.T) {
	r := newRig(t, func(c *Config) {
		c.EnforceExpiry = true
		c.ExpiryHint = 30 * time.Second
		c.DedupHorizon = -1
		c.Query = true
	})
	q := req("old", 1, 1)
	q.ExpiresAtMS = r.clk.now().Add(-time.Millisecond).UnixMilli()
	rc, err := r.dev.Apply(q)
	if err != nil || rc.Outcome != "STOPPED" || rc.Reason != "EXPIRED_AT_DEVICE" || r.effects("old") != 0 {
		t.Fatalf("%+v %v", rc, err)
	}
	ok := req("fresh", 2, 1)
	ok.ExpiresAtMS = r.clk.now().Add(time.Second).UnixMilli()
	if rc := r.applyReq(ok); rc.Outcome != "APPLIED" {
		t.Fatalf("%+v", rc)
	}
	c := r.dev.Contract()
	if !c.Has(contract.CapExpiry) || c.ExpiryMS != 30000 || !c.ExpiryRevealsStopped() {
		t.Fatalf("T + Q_A + unbounded D_h must reveal STOPPED after expiry: %+v", c)
	}
}

func (r *rig) applyReq(q protocol.CommandRequest) protocol.Receipt {
	rc, err := r.dev.Apply(q)
	if err != nil {
		r.t.Fatal(err)
	}
	return rc
}

// D07: atomic device (X): crash after the plant write, before the receipt;
// recovery yields APPLIED and a retry never duplicates.
func TestD07AtomicCrashRecovery(t *testing.T) {
	r := newRig(t, func(c *Config) { c.Atomic = true; c.Query = true })
	r.applyCrashing("a", 1, 2, CrashAfterEffectBeforeReceipt)
	r.reopen()
	q, err := r.dev.Receipt("a")
	if err != nil || q.Outcome != "APPLIED" || q.Reason != "ATOMIC_RECOVERY" {
		t.Fatalf("%+v %v", q, err)
	}
	r.apply("a", 1, 2)
	if r.effects("a") != 1 {
		t.Fatalf("X: exactly one effect, got %d", r.effects("a"))
	}
	// crash before the effect: recovery performs it exactly once
	r.applyCrashing("b", 2, 1, CrashAfterClaimBeforeEffect)
	r.reopen()
	if r.effects("b") != 1 {
		t.Fatalf("claimed command must be completed once, got %d", r.effects("b"))
	}
	if c := r.dev.Contract(); c.Level() != contract.K4 {
		t.Fatalf("level %s", c.Level())
	}
	if _, err := Open(Config{DeviceID: "x", DomainID: "d", StateDir: t.TempDir(), Plant: LocalPlant{r.plant}, Atomic: true, DedupHorizon: time.Second}); err == nil {
		t.Fatal("atomic with finite horizon must be rejected")
	}
}

// D08: non-atomic crash between effect and receipt: the device must never
// claim STOPPED for the orphaned claim; offline queue/drop modes.
func TestD08NonAtomicCrashAndOffline(t *testing.T) {
	r := newRig(t, func(c *Config) { c.DedupHorizon = -1; c.Query = true; c.Stop = true })
	r.applyCrashing("a", 1, 2, CrashAfterEffectBeforeReceipt)
	r.reopen()
	if q, _ := r.dev.Receipt("a"); q.Outcome != "UNCERTAIN" {
		t.Fatalf("orphaned claim must be UNCERTAIN, got %+v", q)
	}
	if s, _ := r.dev.Stop(stopFor(t, "a", 1, 2)); s.Outcome == "STOPPED" {
		t.Fatal("stop must not answer STOPPED for a claim whose effect is unknown")
	}
	if rc := r.apply("a", 1, 2); rc.Outcome != "UNCERTAIN" || r.effects("a") != 1 {
		t.Fatalf("retry must not re-execute: %+v effects=%d", rc, r.effects("a"))
	}
	// offline: drop refuses, queue delivers late
	rd := newRig(t, func(c *Config) { c.OfflineMode = "drop" })
	rd.dev.SetOnline(false)
	if _, err := rd.dev.Apply(req("x", 1, 1)); !errors.Is(err, ErrOffline) {
		t.Fatal("drop mode must refuse while offline")
	}
	rq := newRig(t, func(c *Config) { c.OfflineMode = "queue"; c.Now = time.Now })
	rq.dev.SetOnline(false)
	done := make(chan protocol.Receipt)
	go func() { rc, _ := rq.dev.Apply(req("y", 1, 1)); done <- rc }()
	time.Sleep(50 * time.Millisecond)
	if rq.effects("y") != 0 {
		t.Fatal("no effect while offline")
	}
	rq.dev.SetOnline(true)
	if rc := <-done; rc.Outcome != "APPLIED" || rq.effects("y") != 1 {
		t.Fatalf("queued command must apply on reconnect (late delivery): %+v", rc)
	}
}

// D09 (release 068): a device with F but without dedup (K0F, K1F after eviction, CUTOFF)
// keeps the terminal receipt of every identity at or below an installed cutoff. A late copy
// of an APPLIED command that arrives after the fence gets APPLIED back, no second effect,
// and later barriers keep reporting APPLIED (067 emulator bug: the copy was sealed as
// STOPPED over the APPLIED receipt, a false STOPPED from the owner).
func TestD09FenceKeepsAppliedReceiptOfLateCopy(t *testing.T) {
	for _, tc := range []struct {
		name string
		mut  func(*Config)
	}{
		{"K0F", func(c *Config) { c.Fence = true }},
		{"CUTOFF", func(c *Config) { c.Fence = true; c.Query = true }},
	} {
		t.Run(tc.name, func(t *testing.T) {
			r := newRig(t, tc.mut)
			if rc := r.apply("a", 1, 2); rc.Outcome != "APPLIED" {
				t.Fatalf("%+v", rc)
			}
			br, err := r.dev.Barrier(protocol.BarrierRequest{SchemaVersion: 1, DomainID: "d", Generation: 1, Token: "t1", CutoffSequence: 1})
			if err != nil || len(br.Receipts) != 1 || br.Receipts[0].Outcome != "APPLIED" {
				t.Fatalf("barrier: %+v %v", br, err)
			}
			late := r.apply("a", 1, 2) // the delayed copy arrives after the cutoff
			if late.Outcome != "APPLIED" || r.effects("a") != 1 {
				t.Fatalf("late copy after the fence: %+v effects=%d (want APPLIED, 1 effect)", late, r.effects("a"))
			}
			for i, tok := range []string{"t2", "t3"} {
				if i == 1 {
					r.reopen() // the journal replay must give the same receipts
				}
				br, err = r.dev.Barrier(protocol.BarrierRequest{SchemaVersion: 1, DomainID: "d", Generation: 1, Token: tok, CutoffSequence: 1})
				if err != nil || len(br.Receipts) != 1 || br.Receipts[0].Outcome != "APPLIED" {
					t.Fatalf("barrier %s after the late copy: %+v %v", tok, br, err)
				}
			}
			if rc := r.apply("b", 1, 1); rc.Outcome != "STOPPED" || rc.Reason != "SEALED_BY_FENCE" {
				t.Fatalf("an identity never seen at or below the cutoff is still sealed: %+v", rc)
			}
		})
	}
}

// D10 (release 068): the device never journals the opposite terminal outcome for one
// identity, whatever the path (here a forgotten APPLIED identity re-sent above a bound).
func TestD10NoTerminalReversal(t *testing.T) {
	r := newRig(t, func(c *Config) { c.Bound = 3 })
	r.apply("a", 1, 3)
	if _, err := r.dev.Apply(req("a", 1, 3)); !errors.Is(err, ErrTerminalReversal) {
		t.Fatalf("a bound rejection must not overwrite the APPLIED receipt with STOPPED, got %v", err)
	}
	if r.effects("a") != 1 {
		t.Fatalf("effects %d", r.effects("a"))
	}
	if rc := r.apply("c", 2, 1); rc.Outcome != "STOPPED" || rc.Reason != "BOUND_REJECT" {
		t.Fatalf("a new identity above the bound is rejected: %+v", rc)
	}
}

// D11 (release 072): K0S, a per-identity stop without dedup or query
// (experiment S). A stop before the effect leaves a durable tombstone that a
// late copy cannot pass; a stop after the effect answers APPLIED; a stop on a
// crash orphan answers UNCERTAIN (the stop isolates the orphan, Prop. orphan).
func TestD11StopWithoutDedup(t *testing.T) {
	r := newRig(t, func(c *Config) { c.Stop = true })
	if c := r.dev.Contract(); c.Level() != contract.K3 || !c.Has(contract.CapStop) || c.Has(contract.CapDedup) || c.Has(contract.CapQueryApplied) {
		t.Fatalf("K0S contract: %+v", c)
	}
	if s, err := r.dev.Stop(stopFor(t, "lost", 1, 3)); err != nil || s.Outcome != "STOPPED" {
		t.Fatalf("%+v %v", s, err)
	}
	r.clk.add(time.Hour)
	if rc := r.apply("lost", 1, 3); rc.Outcome != "STOPPED" || r.effects("lost") != 0 {
		t.Fatalf("late copy after stop: %+v effects=%d", rc, r.effects("lost"))
	}
	r.apply("done", 2, 1)
	if s, _ := r.dev.Stop(stopFor(t, "done", 2, 1)); s.Outcome != "APPLIED" {
		t.Fatalf("stop after effect: %+v", s)
	}
	r.applyCrashing("orphan", 3, 2, CrashAfterEffectBeforeReceipt)
	r.reopen()
	if s, _ := r.dev.Stop(stopFor(t, "orphan", 3, 2)); s.Outcome != "UNCERTAIN" {
		t.Fatalf("stop on an orphan must be UNCERTAIN, got %+v", s)
	}
	if s, _ := r.dev.Stop(stopFor(t, "other", 4, 1)); s.Outcome != "STOPPED" {
		t.Fatalf("other identities must still be stoppable next to an orphan: %+v", s)
	}
}

// D12 (release 072, negative control): NEG_SEAL seals a crash orphan as
// STOPPED in a fence answer although its effect happened: a false STOPPED that
// the testbed checker must report.
func TestD12NegativeControlSealsOrphan(t *testing.T) {
	r := newRig(t, func(c *Config) { c.Fence = true; c.NegSealOrphans = true })
	r.applyCrashing("orphan", 1, 2, CrashAfterEffectBeforeReceipt)
	r.reopen()
	r.cfg.NegSealOrphans = true
	br, err := r.dev.Barrier(protocol.BarrierRequest{SchemaVersion: 1, DomainID: "d", Generation: 1, Token: "t", CutoffSequence: 1})
	if err != nil || len(br.Receipts) != 1 || br.Receipts[0].Outcome != "STOPPED" || r.effects("orphan") != 1 {
		t.Fatalf("negative control must report the applied orphan as STOPPED: %+v %v effects=%d", br, err, r.effects("orphan"))
	}
	// the honest device reports UNCERTAIN for the same history
	h := newRig(t, func(c *Config) { c.Fence = true })
	h.applyCrashing("orphan", 1, 2, CrashAfterEffectBeforeReceipt)
	h.reopen()
	hb, err := h.dev.Barrier(protocol.BarrierRequest{SchemaVersion: 1, DomainID: "d", Generation: 1, Token: "t", CutoffSequence: 1})
	if err != nil || len(hb.Receipts) != 1 || hb.Receipts[0].Outcome != "UNCERTAIN" {
		t.Fatalf("honest fence must report UNCERTAIN: %+v %v", hb, err)
	}
}

// D13 (release 072, negative control): NEG_DEDUP advertises D_h (unbounded)
// but executes a resend of an applied identity again: a duplicate effect.
func TestD13NegativeControlReexecutes(t *testing.T) {
	r := newRig(t, func(c *Config) { c.DedupHorizon = -1; c.Query = true; c.NegReexecute = true })
	if c := r.dev.Contract(); !c.Has(contract.CapDedup) {
		t.Fatalf("negative control must advertise D_h: %+v", c)
	}
	r.apply("a", 1, 2)
	if rc := r.apply("a", 1, 2); rc.Outcome != "APPLIED" || r.effects("a") != 2 {
		t.Fatalf("negative control must duplicate: %+v effects=%d", rc, r.effects("a"))
	}
}

// D-ATT (release 077): the device records which dispatch attempt took effect, once per identity.
func TestEffectAttemptLogged(t *testing.T) {
	dir := t.TempDir()
	p, err := plant.Open(filepath.Join(dir, "plant.jsonl"), false)
	if err != nil {
		t.Fatal(err)
	}
	defer p.Close()
	d, err := Open(Config{DeviceID: "dev-1", DomainID: "dev-1", StateDir: filepath.Join(dir, "st"), Plant: LocalPlant{P: p}, DedupHorizon: -1, Query: true})
	if err != nil {
		t.Fatal(err)
	}
	defer d.Close()
	req := protocol.CommandRequest{SchemaVersion: 1, DomainID: "dev-1", Generation: 1, CommandID: "c1", Operation: domain.OpAdd, Value: []int64{2}, Epoch: 1, Sequence: 1}
	if _, err := d.ApplyAttempt(req, 3); err != nil {
		t.Fatal(err)
	}
	if _, err := d.ApplyAttempt(req, 4); err != nil { // a duplicate: replayed, no new effect
		t.Fatal(err)
	}
	raw, err := os.ReadFile(filepath.Join(dir, "st", "dev-1.effects.jsonl"))
	if err != nil {
		t.Fatal(err)
	}
	lines := strings.Split(strings.TrimSpace(string(raw)), "\n")
	if len(lines) != 1 || !strings.Contains(lines[0], `"command_id":"c1"`) || !strings.Contains(lines[0], `"attempt":3`) {
		t.Fatalf("want one record of attempt 3, got %q", raw)
	}
}

// Release 084: the emulated device answers its queries from its journal, which keeps every record: its contracts with
// Q_A declare r = infinity, so K3T (the D06 settings) keeps expiry-revealed evidence; a contract without Q_A declares nothing.
func TestDeviceRetention084(t *testing.T) {
	k3t := newRig(t, func(c *Config) {
		c.DedupHorizon = -1
		c.EnforceExpiry = true
		c.ExpiryHint = 30 * time.Second
		c.Query = true
	})
	c := k3t.dev.Contract()
	if c.RecordRetentionMS != contract.UnboundedRetentionMS || !c.ExpiryRevealsStopped() || c.Level() != contract.K3 {
		t.Fatalf("emulated K3T keeps ER: %+v", c)
	}
	k0f := newRig(t, func(c *Config) { c.Fence = true })
	if c := k0f.dev.Contract(); c.RecordRetentionMS != 0 {
		t.Fatalf("no query, no retention declaration: %+v", c)
	}
}
